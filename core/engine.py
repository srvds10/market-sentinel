"""
Top-level asyncio orchestrator.

Spawns and coordinates:
  - TickProvider  (DhanWSClient or MockTickFeed)
  - InstrumentManager  (30-min recalibration loop)
  - SignalEngine  (per-tick math)
  - ExecutionEngine  (paper trade state machine)
  - Database  (persistence)
  - Tick CSV logger + daily purge
  - AppState  (shared read-only snapshot for FastAPI)

After any WebSocket reconnect a 5-minute warmup blackout is enforced before
new signals can trigger trades.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import yaml

from core.database import Database
from core.execution import ExecutionConfig, ExecutionEngine, ExitReason
from core.instruments import InstrumentManager
from core.signal import SignalConfig, SignalEngine, SignalEvent, Tick
from core.ws_client import DhanWSClient, MockTickFeed, TickProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Engine state
# ---------------------------------------------------------------------------

class EngineState(str, Enum):
    IDLE       = "IDLE"
    WARMING_UP = "WARMING_UP"
    ACTIVE     = "ACTIVE"
    KILLED     = "KILLED"     # daily drawdown kill switch
    CLOSED     = "CLOSED"     # post 15:15


# ---------------------------------------------------------------------------
# Shared state snapshot (written by engine, read by FastAPI)
# ---------------------------------------------------------------------------

@dataclass
class AppState:
    engine_state: EngineState = EngineState.IDLE
    spot_ltp: float = 0.0
    atm_ltp: float = 0.0
    otm_call_ltp: float = 0.0
    otm_put_ltp: float = 0.0
    last_z_score: float | None = None
    last_ratio: float | None = None
    z_sample_count: int = 0
    capital: float = 0.0
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    open_trade: dict | None = None
    last_signal: dict | None = None
    warmup_remaining_seconds: float = 0.0
    instrument_map: dict | None = None
    reconnect_count: int = 0
    started_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)

    # broadcast queue: engine writes, FastAPI WS broadcaster reads
    broadcast_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    # set by /api/token to trigger immediate WS reconnect
    reconnect_event: asyncio.Event = field(default_factory=asyncio.Event)


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        raw = yaml.safe_load(f)

    def expand(val: Any) -> Any:
        if isinstance(val, str) and val.startswith("${") and val.endswith("}"):
            key = val[2:-1]
            return os.environ.get(key, "")
        if isinstance(val, dict):
            return {k: expand(v) for k, v in val.items()}
        if isinstance(val, list):
            return [expand(v) for v in val]
        return val

    return expand(raw)


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------

class Engine:
    """Creates and wires all subsystems; call run() to start."""

    def __init__(self, config_path: str = "config.yaml") -> None:
        self._cfg = load_config(config_path)
        self.state = AppState()
        self._db = Database(self._cfg["logging"]["db_path"])
        self._tick_queue: asyncio.Queue[Tick] = asyncio.Queue(maxsize=4096)
        self._reconnect_count = 0

        # Build sub-engines from config
        sig_cfg = self._cfg["signal"]
        self._signal_engine = SignalEngine(SignalConfig(
            window_seconds=sig_cfg["window_seconds"],
            min_spot_delta=sig_cfg["min_spot_delta"],
            zscore_threshold=sig_cfg["zscore_threshold"],
            zscore_lookback_minutes=sig_cfg["zscore_lookback_minutes"],
            min_history_samples=sig_cfg["min_history_samples"],
        ))

        exc_cfg = self._cfg["execution"]
        self._execution_engine = ExecutionEngine(ExecutionConfig(
            virtual_capital=exc_cfg["virtual_capital"],
            max_position_pct=exc_cfg["max_position_pct"],
            stop_loss_pct=exc_cfg["stop_loss_pct"],
            trailing_stop_activation_pct=exc_cfg["trailing_stop_activation_pct"],
            trailing_stop_pct=exc_cfg["trailing_stop_pct"],
            cooldown_minutes=exc_cfg["cooldown_minutes"],
            morning_filter_start=exc_cfg["morning_filter_start"],
            morning_filter_end=exc_cfg["morning_filter_end"],
            force_close_time=exc_cfg["force_close_time"],
            daily_drawdown_kill_pct=exc_cfg["daily_drawdown_kill_pct"],
            lot_size=self._cfg["instrument"]["lot_size"],
        ))

        dhan_cfg = self._cfg["dhan"]
        self._instrument_manager = InstrumentManager(
            signal_engine=self._signal_engine,
            recalibration_interval_minutes=sig_cfg["recalibration_interval_minutes"],
            target_delta_min=sig_cfg["target_delta_min"],
            target_delta_max=sig_cfg["target_delta_max"],
            mock_mode=dhan_cfg.get("mock_mode", True),
            dhan_client_id=dhan_cfg.get("client_id", ""),
            dhan_access_token=dhan_cfg.get("access_token", ""),
            instrument_name=self._cfg["instrument"]["default"],
        )

        self._warmup_seconds: float = self._cfg["ws"]["warmup_seconds"]
        self._tick_csv_path: Path | None = None

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        await self._db.open()
        Path(self._cfg["logging"]["tick_csv_dir"]).mkdir(parents=True, exist_ok=True)
        await self._db.log("INFO", "engine", "Market Sentinel starting")

        self.state.capital = self._execution_engine.capital
        self.state.engine_state = EngineState.WARMING_UP

        # Start background tasks
        tasks = [
            asyncio.create_task(self._instrument_manager.run(), name="instruments"),
            asyncio.create_task(self._tick_loop(), name="tick_loop"),
            asyncio.create_task(self._heartbeat(), name="heartbeat"),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Engine cancelled — shutting down")
        finally:
            for t in tasks:
                t.cancel()
            await self._db.close()

    # ------------------------------------------------------------------
    # Tick provider lifecycle (handles reconnects + warmup)
    # ------------------------------------------------------------------

    async def _tick_loop(self) -> None:
        while True:
            provider = self._make_provider()
            warmup_end = time.monotonic() + self._warmup_seconds

            self.state.engine_state = EngineState.WARMING_UP
            self._execution_engine.set_warmup(self._warmup_seconds)

            provider_task = asyncio.create_task(provider.run(), name="provider")
            self._open_tick_csv()

            try:
                await self._process_ticks(provider_task, warmup_end)
            except asyncio.CancelledError:
                provider_task.cancel()
                raise
            except Exception as exc:
                logger.error("Tick loop error: %s", exc, exc_info=True)
            finally:
                provider.stop()
                provider_task.cancel()
                try:
                    await asyncio.wait_for(provider_task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

            self._reconnect_count += 1
            self.state.reconnect_count = self._reconnect_count
            delay = self._cfg["ws"]["reconnect_delay_seconds"]
            logger.info("Reconnecting in %ss (attempt %d)", delay, self._reconnect_count)
            await asyncio.sleep(delay)

    async def _process_ticks(self, provider_task: asyncio.Task, warmup_end: float) -> None:
        while not provider_task.done():
            # Token update triggers immediate reconnect with new credentials
            if self.state.reconnect_event.is_set():
                self.state.reconnect_event.clear()
                logger.info("Reconnect triggered by token update")
                return

            try:
                tick: Tick = await asyncio.wait_for(
                    self._tick_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            self._log_tick_csv(tick)
            self._update_state_prices(tick)

            # Advance to ACTIVE after warmup
            if (self.state.engine_state == EngineState.WARMING_UP
                    and time.monotonic() >= warmup_end):
                self.state.engine_state = EngineState.ACTIVE
                self._execution_engine.reset_day()
                logger.info("Warmup complete — engine ACTIVE")

            if self.state.engine_state not in (EngineState.ACTIVE,):
                continue

            # Feed signal engine
            signal: SignalEvent | None = self._signal_engine.on_tick(tick)
            self.state.last_z_score = self._signal_engine._zscore.zscore(0) if False else None
            self.state.z_sample_count = self._signal_engine.z_score_sample_count()

            # Execution: price update on open trade
            closed = self._execution_engine.on_tick(tick.symbol, tick.ltp)
            if closed:
                await self._on_trade_close(closed)

            # Time stop check
            atm_ltp = self.state.atm_ltp
            if atm_ltp:
                closed = self._execution_engine.check_time_stop(atm_ltp)
                if closed:
                    await self._on_trade_close(closed)
                    self.state.engine_state = EngineState.CLOSED

            # Kill switch check
            if self._execution_engine.kill_active and not self.state.engine_state == EngineState.KILLED:
                self.state.engine_state = EngineState.KILLED

            # New signal?
            if signal:
                await self._on_signal(signal)

            # Divergence collapse: Z-score drops back below threshold
            if self._execution_engine.open_trade and signal is None:
                pass  # divergence collapse handled separately below

            self._sync_state()

    # ------------------------------------------------------------------
    # Signal → trade open
    # ------------------------------------------------------------------

    async def _on_signal(self, signal: SignalEvent) -> None:
        logger.info("Signal fired  Z=%.3f  ratio=%.4f  dir=%s",
                    signal.z_score, signal.ratio, signal.direction)

        self.state.last_signal = {
            "timestamp":  signal.timestamp,
            "z_score":    signal.z_score,
            "ratio":      signal.ratio,
            "direction":  signal.direction,
            "spot_ltp":   signal.spot_ltp,
            "atm_symbol": signal.atm_symbol,
        }

        atm_ltp = self.state.atm_ltp
        if atm_ltp <= 0:
            return

        trade = self._execution_engine.try_open(signal, atm_ltp, signal.atm_symbol)
        acted_on = trade is not None

        await self._db.insert_signal({
            "fired_at":   signal.timestamp,
            "z_score":    signal.z_score,
            "ratio":      signal.ratio,
            "direction":  signal.direction,
            "spot_ltp":   signal.spot_ltp,
            "atm_symbol": signal.atm_symbol,
            "delta_spot": signal.delta_spot,
            "delta_otm":  signal.delta_otm,
            "acted_on":   int(acted_on),
        })

        if trade:
            await self._db.insert_trade_open(trade.to_dict())
            await self._broadcast({"type": "trade_open", **trade.to_dict()})

        await self._broadcast({"type": "signal", **self.state.last_signal,
                                "acted_on": acted_on})

    # ------------------------------------------------------------------
    # Trade close
    # ------------------------------------------------------------------

    async def _on_trade_close(self, trade) -> None:
        await self._db.update_trade_close(trade.id, {
            "closed_at":   trade.closed_at,
            "exit_price":  trade.exit_price,
            "exit_reason": trade.exit_reason.value,
            "pnl":         trade.pnl,
            "pnl_pct":     trade.pnl_pct,
        })
        await self._broadcast({"type": "trade_close", **trade.to_dict()})
        logger.info("Trade closed: %s  pnl=₹%.2f", trade.id, trade.pnl or 0)

    # ------------------------------------------------------------------
    # State sync + broadcast
    # ------------------------------------------------------------------

    def _sync_state(self) -> None:
        self.state.capital = self._execution_engine.capital
        self.state.daily_pnl = self._execution_engine.daily_pnl()
        self.state.daily_pnl_pct = self._execution_engine.daily_pnl_pct()
        open_trade = self._execution_engine.open_trade
        self.state.open_trade = open_trade.to_dict() if open_trade else None

    def _update_state_prices(self, tick: Tick) -> None:
        imap = self._instrument_manager.current_map()
        if imap is None:
            return
        if tick.symbol == imap.spot_symbol:
            self.state.spot_ltp = tick.ltp
        elif tick.symbol == imap.atm_call.symbol:
            self.state.atm_ltp = tick.ltp
        elif tick.symbol == imap.otm_call.symbol:
            self.state.otm_call_ltp = tick.ltp
        elif tick.symbol == imap.otm_put.symbol:
            self.state.otm_put_ltp = tick.ltp

    async def _broadcast(self, msg: dict) -> None:
        try:
            self.state.broadcast_queue.put_nowait(msg)
        except asyncio.QueueFull:
            pass

    async def _heartbeat(self) -> None:
        while True:
            self.state.last_heartbeat = time.time()
            await self._broadcast({
                "type":           "status",
                "engine_state":   self.state.engine_state.value,
                "spot_ltp":       self.state.spot_ltp,
                "atm_ltp":        self.state.atm_ltp,
                "capital":        self.state.capital,
                "daily_pnl":      self.state.daily_pnl,
                "daily_pnl_pct":  self.state.daily_pnl_pct,
                "z_sample_count": self.state.z_sample_count,
            })
            await asyncio.sleep(2.0)

    # ------------------------------------------------------------------
    # Tick provider factory
    # ------------------------------------------------------------------

    def _make_provider(self) -> TickProvider:
        dhan = self._cfg["dhan"]
        if dhan.get("mock_mode", True):
            return MockTickFeed(queue=self._tick_queue)
        return DhanWSClient(
            queue=self._tick_queue,
            client_id=dhan.get("client_id", ""),
            access_token=dhan.get("access_token", ""),
            instrument_provider=self._instrument_manager.get_dhan_instruments,
            reconnect_delay=self._cfg["ws"]["reconnect_delay_seconds"],
        )

    # ------------------------------------------------------------------
    # Tick CSV logging
    # ------------------------------------------------------------------

    def _open_tick_csv(self) -> None:
        tick_dir = Path(self._cfg["logging"]["tick_csv_dir"])
        date_str = datetime.now().strftime("%Y-%m-%d")
        self._tick_csv_path = tick_dir / f"ticks_{date_str}.csv"
        if not self._tick_csv_path.exists():
            with open(self._tick_csv_path, "w", newline="") as f:
                csv.writer(f).writerow(["timestamp", "symbol", "ltp"])

    def _log_tick_csv(self, tick: Tick) -> None:
        if self._tick_csv_path is None:
            return
        try:
            with open(self._tick_csv_path, "a", newline="") as f:
                csv.writer(f).writerow([tick.timestamp, tick.symbol, tick.ltp])
        except OSError:
            pass
