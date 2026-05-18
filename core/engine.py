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
from core.timeutil import is_weekday_ist, ist_minutes_now
from core.ws_client import DhanWSClient, TickProvider

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
    atm_put_ltp: float = 0.0
    itm_call_ltp: float = 0.0
    itm_put_ltp: float = 0.0
    otm_call_ltp: float = 0.0
    otm_put_ltp: float = 0.0
    last_z_score: float | None = None
    last_ratio: float | None = None
    z_sample_count: int = 0
    market_bias: str = "UNKNOWN"
    spot_delta_5m: float | None = None
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
            bias_window_seconds=sig_cfg.get("bias_window_seconds", 60.0),
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
            last_entry_time=exc_cfg.get("last_entry_time", "14:00"),
            force_close_time=exc_cfg["force_close_time"],
            daily_drawdown_kill_pct=exc_cfg["daily_drawdown_kill_pct"],
            lot_size=self._cfg["instrument"]["lot_size"],
            slippage_rupees=exc_cfg.get("slippage_rupees", 2.0),
            scale_by_zscore=exc_cfg.get("scale_by_zscore", True),
            scale_zscore_per_lot=exc_cfg.get("scale_zscore_per_lot", 1.5),
            min_hold_minutes=exc_cfg.get("min_hold_minutes", 3.0),
        ))

        dhan_cfg = self._cfg["dhan"]
        self._instrument_manager = InstrumentManager(
            signal_engine=self._signal_engine,
            recalibration_interval_minutes=sig_cfg["recalibration_interval_minutes"],
            target_delta_min=sig_cfg["target_delta_min"],
            target_delta_max=sig_cfg["target_delta_max"],
            dhan_client_id=dhan_cfg.get("client_id", ""),
            dhan_access_token=dhan_cfg.get("access_token", ""),
            instrument_name=self._cfg["instrument"]["default"],
            on_instruments_changed=lambda: self.state.reconnect_event.set(),
        )

        self._warmup_seconds: float = self._cfg["ws"]["warmup_seconds"]
        self._tick_csv_path: Path | None = None
        self._stale_tick_timeout: float = self._cfg["ws"].get("stale_tick_timeout_seconds", 15.0)

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
            self._signal_engine.reset()
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
        last_tick_mono = time.monotonic()
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
                # Advance through warmup on a timer even if no ticks arrive
                if self.state.engine_state == EngineState.WARMING_UP:
                    remaining = warmup_end - time.monotonic()
                    self.state.warmup_remaining_seconds = max(0.0, remaining)
                    if remaining <= 0:
                        self.state.engine_state = EngineState.ACTIVE
                        self._execution_engine.reset_day()
                        logger.info("Warmup complete (no ticks yet) — engine ACTIVE")
                # Stale-tick detector: TCP may stay open while the feed silently
                # stalls.  Force a reconnect if no tick arrived in N seconds
                # during market hours.
                silent_for = time.monotonic() - last_tick_mono
                if (silent_for > self._stale_tick_timeout
                        and self._in_market_hours()
                        and self.state.engine_state != EngineState.WARMING_UP):
                    logger.warning(
                        "No ticks for %.0fs during market hours — forcing reconnect",
                        silent_for,
                    )
                    return
                continue

            last_tick_mono = time.monotonic()

            self._log_tick_csv(tick)
            self._update_state_prices(tick)

            # Feed signal engine every tick so Z-score baseline builds during warmup
            signal_candidate: SignalEvent | None = self._signal_engine.on_tick(tick)
            self.state.z_sample_count = self._signal_engine.z_score_sample_count()
            self.state.last_z_score = self._signal_engine.current_z_score()
            bias, delta_5m = self._signal_engine.market_bias()
            self.state.market_bias   = bias
            self.state.spot_delta_5m = delta_5m

            # Advance to ACTIVE after warmup; track countdown for UI
            if self.state.engine_state == EngineState.WARMING_UP:
                remaining = warmup_end - time.monotonic()
                self.state.warmup_remaining_seconds = max(0.0, remaining)
                if remaining <= 0:
                    self.state.engine_state = EngineState.ACTIVE
                    self._execution_engine.reset_day()
                    logger.info("Warmup complete — engine ACTIVE")

            if self.state.engine_state not in (EngineState.ACTIVE,):
                continue

            signal = signal_candidate

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

            # Divergence collapse: Z-score has dropped back below the threshold
            # while a trade is still open → exit the position.
            # Guarded by min_hold_minutes to prevent immediate exit when the
            # Z-score dips briefly at the threshold boundary right after entry.
            open_trade = self._execution_engine.open_trade
            if open_trade and signal is None:
                current_z = self._signal_engine.current_z_score()
                threshold = self._signal_engine.config.zscore_threshold
                atm_ltp_now = self.state.atm_ltp
                min_hold = self._execution_engine.config.min_hold_minutes * 60.0
                held_long_enough = (time.time() - open_trade.opened_at) >= min_hold
                if (held_long_enough
                        and current_z is not None
                        and current_z < threshold
                        and atm_ltp_now > 0):
                    closed = self._execution_engine.on_divergence_collapse(atm_ltp_now)
                    if closed:
                        await self._on_trade_close(closed)

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

        # Use the ATM leg that matches the signal direction for pricing.
        atm_ltp = (self.state.atm_ltp if signal.direction == "CALL"
                   else self.state.atm_put_ltp)
        if atm_ltp <= 0:
            return

        # Bias-confirmation gate: only act when the 1-min option-flow bias
        # agrees with the Z-score direction. BULLISH bias + CALL signal, or
        # BEARISH bias + PUT signal. SIDEWAYS / UNKNOWN / opposite-bias signals
        # are recorded but not traded.
        bias = self.state.market_bias
        aligned = (
            (bias == "BULLISH" and signal.direction == "CALL")
            or (bias == "BEARISH" and signal.direction == "PUT")
        )
        if aligned:
            trade = self._execution_engine.try_open(signal, atm_ltp, signal.atm_symbol)
        else:
            trade = None
            logger.info("Signal skipped — bias=%s does not confirm %s direction",
                        bias, signal.direction)
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
        # Feed every option tick into the LTP cache for IV computation
        self._instrument_manager.update_option_ltp(tick.symbol, tick.ltp)
        if tick.symbol == imap.spot_symbol:
            self.state.spot_ltp = tick.ltp
            # Live re-selection: if ATM/OTM has shifted within the grid, swap
            # the SignalEngine's active leg pointers — no WS reconnect needed.
            if self._instrument_manager.update_active_for_spot(tick.ltp):
                self._signal_engine.set_instrument_map(
                    spot_symbol     = imap.spot_symbol,
                    atm_call_symbol = imap.atm_call.symbol,
                    atm_put_symbol  = imap.atm_put.symbol,
                    otm_call_symbol = imap.otm_call.symbol,
                    otm_put_symbol  = imap.otm_put.symbol,
                    bias_otm_call_symbols = [s.symbol for s in imap.near_otm_calls],
                    bias_otm_put_symbols  = [s.symbol for s in imap.near_otm_puts],
                )
                logger.debug(
                    "Active legs rolled — ATM=%.0f  OTM call=%s  OTM put=%s",
                    imap.atm_strike, imap.otm_call.symbol, imap.otm_put.symbol,
                )
        elif tick.symbol == imap.atm_call.symbol:
            self.state.atm_ltp = tick.ltp
        elif tick.symbol == imap.atm_put.symbol:
            self.state.atm_put_ltp = tick.ltp
        elif imap.itm_call and tick.symbol == imap.itm_call.symbol:
            self.state.itm_call_ltp = tick.ltp
        elif imap.itm_put and tick.symbol == imap.itm_put.symbol:
            self.state.itm_put_ltp = tick.ltp
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
                "atm_put_ltp":    self.state.atm_put_ltp,
                "itm_call_ltp":   self.state.itm_call_ltp,
                "itm_put_ltp":    self.state.itm_put_ltp,
                "otm_call_ltp":   self.state.otm_call_ltp,
                "otm_put_ltp":    self.state.otm_put_ltp,
                "capital":        self.state.capital,
                "daily_pnl":      self.state.daily_pnl,
                "daily_pnl_pct":  self.state.daily_pnl_pct,
                "z_sample_count": self.state.z_sample_count,
                "market_bias":    self.state.market_bias,
                "spot_delta_5m":  self.state.spot_delta_5m,
                "last_z_score":   self.state.last_z_score,
                "reconnect_count": self.state.reconnect_count,
                "last_ratio":     self.state.last_ratio,
            })
            await asyncio.sleep(2.0)

    # ------------------------------------------------------------------
    # Tick provider factory
    # ------------------------------------------------------------------

    @staticmethod
    def _in_market_hours() -> bool:
        """NSE cash/derivatives session: 09:15–15:30 IST, Mon–Fri."""
        if not is_weekday_ist():
            return False
        m = ist_minutes_now()
        return (9 * 60 + 15) <= m <= (15 * 60 + 30)

    def _make_provider(self) -> TickProvider:
        dhan = self._cfg["dhan"]
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
