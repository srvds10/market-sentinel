"""
Paper-trade execution engine.

State machine per trade:
  OPEN  →  CLOSED (exit reason: STOP_LOSS | TRAILING_STOP | DIVERGENCE | TIME_STOP | KILL_SWITCH)

Guards:
  - Morning filter: only execute between 09:15 and 09:30
  - 15-minute cooldown after any trade close
  - Daily drawdown kill switch: halt if cumulative daily loss >= 40% of start capital
  - Force-close all open trades at 15:15
  - Max 20% of virtual capital per trade
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from core.signal import SignalEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ExitReason(str, Enum):
    STOP_LOSS         = "STOP_LOSS"
    TRAILING_STOP     = "TRAILING_STOP"
    DIVERGENCE        = "DIVERGENCE"
    TIME_STOP         = "TIME_STOP"
    KILL_SWITCH       = "KILL_SWITCH"


class EngineBlock(str, Enum):
    """Reason why a new trade cannot be opened right now."""
    NONE            = "NONE"
    MORNING_FILTER  = "MORNING_FILTER"
    COOLDOWN        = "COOLDOWN"
    KILL_SWITCH     = "KILL_SWITCH"
    POSITION_OPEN   = "POSITION_OPEN"
    WARMING_UP      = "WARMING_UP"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class PaperTrade:
    id: str
    opened_at: float
    symbol: str
    direction: str          # CALL | PUT
    entry_price: float
    qty: int
    capital_at_risk: float  # ₹ allocated (entry_price * qty * lot_size)
    z_score_entry: float
    stop_loss: float        # absolute price floor
    trailing_high: float    # running peak (for trailing stop)
    trailing_active: bool   # True once profit >= activation threshold
    closed_at: float | None = None
    exit_price: float | None = None
    exit_reason: ExitReason | None = None
    pnl: float | None = None
    pnl_pct: float | None = None

    def to_dict(self) -> dict:
        return {
            "id":               self.id,
            "opened_at":        self.opened_at,
            "closed_at":        self.closed_at,
            "symbol":           self.symbol,
            "direction":        self.direction,
            "entry_price":      self.entry_price,
            "exit_price":       self.exit_price,
            "qty":              self.qty,
            "capital_at_risk":  self.capital_at_risk,
            "z_score_entry":    self.z_score_entry,
            "stop_loss":        self.stop_loss,
            "exit_reason":      self.exit_reason.value if self.exit_reason else None,
            "pnl":              self.pnl,
            "pnl_pct":          self.pnl_pct,
        }


@dataclass
class ExecutionConfig:
    virtual_capital: float = 1_000_000.0
    max_position_pct: float = 0.20
    stop_loss_pct: float = 0.30
    trailing_stop_activation_pct: float = 0.20
    trailing_stop_pct: float = 0.15
    cooldown_minutes: float = 15.0
    morning_filter_start: str = "09:15"
    morning_filter_end: str = "09:30"
    force_close_time: str = "15:15"
    daily_drawdown_kill_pct: float = 0.40
    lot_size: int = 50


# ---------------------------------------------------------------------------
# Execution engine
# ---------------------------------------------------------------------------

class ExecutionEngine:
    """Manages one open paper trade at a time.

    Call:
      try_open(signal, atm_ltp)   — attempt to open after guards pass
      on_tick(symbol, ltp)        — feed latest price for open trade
      check_time_stop()           — call on every tick or timer
      on_divergence_collapse()    — call when Z-score drops below threshold

    Returns closed PaperTrade objects; callers persist them via Database.
    """

    def __init__(self, config: ExecutionConfig) -> None:
        self.config = config
        self._capital = config.virtual_capital
        self._start_capital = config.virtual_capital
        self._day_start_capital = config.virtual_capital
        self._open_trade: Optional[PaperTrade] = None
        self._last_close_ts: float = 0.0
        self._kill_active: bool = False
        self._warmup_until: float = 0.0   # set by engine on reconnect

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------

    @property
    def capital(self) -> float:
        return self._capital

    @property
    def open_trade(self) -> Optional[PaperTrade]:
        return self._open_trade

    @property
    def kill_active(self) -> bool:
        return self._kill_active

    def daily_pnl(self) -> float:
        return self._capital - self._day_start_capital

    def daily_pnl_pct(self) -> float:
        return self.daily_pnl() / self._day_start_capital if self._day_start_capital else 0.0

    def reset_day(self) -> None:
        """Call at session start (09:15) each morning."""
        self._day_start_capital = self._capital
        self._kill_active = False
        logger.info("Day reset — capital ₹%.0f", self._capital)

    def set_warmup(self, seconds: float) -> None:
        self._warmup_until = time.monotonic() + seconds
        logger.info("Execution warmup blackout for %.0fs", seconds)

    # ------------------------------------------------------------------
    # Guard checks
    # ------------------------------------------------------------------

    def _current_block(self) -> EngineBlock:
        mono = time.monotonic()
        if mono < self._warmup_until:
            return EngineBlock.WARMING_UP
        if self._kill_active:
            return EngineBlock.KILL_SWITCH
        if self._open_trade is not None:
            return EngineBlock.POSITION_OPEN
        if not self._in_morning_window():
            return EngineBlock.MORNING_FILTER
        cooldown_seconds = self.config.cooldown_minutes * 60.0
        if time.time() - self._last_close_ts < cooldown_seconds:
            return EngineBlock.COOLDOWN
        return EngineBlock.NONE

    def _in_morning_window(self) -> bool:
        now = datetime.now()
        start_h, start_m = map(int, self.config.morning_filter_start.split(":"))
        end_h,   end_m   = map(int, self.config.morning_filter_end.split(":"))
        t = now.hour * 60 + now.minute
        return (start_h * 60 + start_m) <= t <= (end_h * 60 + end_m)

    def _past_force_close(self) -> bool:
        now = datetime.now()
        h, m = map(int, self.config.force_close_time.split(":"))
        return now.hour * 60 + now.minute >= h * 60 + m

    # ------------------------------------------------------------------
    # Open
    # ------------------------------------------------------------------

    def try_open(
        self,
        signal: SignalEvent,
        atm_ltp: float,
        atm_symbol: str,
    ) -> Optional[PaperTrade]:
        block = self._current_block()
        if block != EngineBlock.NONE:
            logger.debug("Open blocked: %s", block.value)
            return None

        capital_at_risk = self._capital * self.config.max_position_pct
        qty = max(1, int(capital_at_risk / (atm_ltp * self.config.lot_size)))
        actual_risk = qty * atm_ltp * self.config.lot_size

        stop_loss = atm_ltp * (1.0 - self.config.stop_loss_pct)

        trade = PaperTrade(
            id=str(uuid.uuid4())[:8],
            opened_at=time.time(),
            symbol=atm_symbol,
            direction=signal.direction,
            entry_price=atm_ltp,
            qty=qty,
            capital_at_risk=actual_risk,
            z_score_entry=signal.z_score,
            stop_loss=stop_loss,
            trailing_high=atm_ltp,
            trailing_active=False,
        )
        self._open_trade = trade
        logger.info(
            "PAPER OPEN  %s  %s @ ₹%.2f  qty=%d  SL=₹%.2f  Z=%.2f",
            trade.id, trade.symbol, atm_ltp, qty, stop_loss, signal.z_score,
        )
        return trade

    # ------------------------------------------------------------------
    # Price update (called on every ATM tick)
    # ------------------------------------------------------------------

    def on_tick(self, symbol: str, ltp: float) -> Optional[PaperTrade]:
        """Returns closed trade if an exit condition is met, else None."""
        trade = self._open_trade
        if trade is None or trade.symbol != symbol:
            return None

        # --- update trailing high ---
        if ltp > trade.trailing_high:
            self._open_trade = PaperTrade(
                **{**trade.__dict__, "trailing_high": ltp,
                   "trailing_active": trade.trailing_active or self._trailing_activated(trade, ltp)}
            )
            trade = self._open_trade

        # --- trailing stop check ---
        if trade.trailing_active:
            trail_floor = trade.trailing_high * (1.0 - self.config.trailing_stop_pct)
            if ltp <= trail_floor:
                return self._close(ltp, ExitReason.TRAILING_STOP)

        # --- hard stop loss ---
        if ltp <= trade.stop_loss:
            return self._close(ltp, ExitReason.STOP_LOSS)

        return None

    def _trailing_activated(self, trade: PaperTrade, ltp: float) -> bool:
        profit_pct = (ltp - trade.entry_price) / trade.entry_price
        return profit_pct >= self.config.trailing_stop_activation_pct

    # ------------------------------------------------------------------
    # Named exit triggers
    # ------------------------------------------------------------------

    def on_divergence_collapse(self, ltp: float) -> Optional[PaperTrade]:
        if self._open_trade is None:
            return None
        return self._close(ltp, ExitReason.DIVERGENCE)

    def check_time_stop(self, atm_ltp: float) -> Optional[PaperTrade]:
        if self._open_trade is None:
            return None
        if self._past_force_close():
            return self._close(atm_ltp, ExitReason.TIME_STOP)
        return None

    def trigger_kill_switch(self, atm_ltp: float) -> Optional[PaperTrade]:
        self._kill_active = True
        logger.warning("KILL SWITCH ACTIVATED — daily loss >= %.0f%%",
                       self.config.daily_drawdown_kill_pct * 100)
        if self._open_trade is not None:
            return self._close(atm_ltp, ExitReason.KILL_SWITCH)
        return None

    # ------------------------------------------------------------------
    # Internal close
    # ------------------------------------------------------------------

    def _close(self, exit_price: float, reason: ExitReason) -> PaperTrade:
        trade = self._open_trade
        assert trade is not None

        pnl = (exit_price - trade.entry_price) * trade.qty * self.config.lot_size
        pnl_pct = pnl / trade.capital_at_risk if trade.capital_at_risk else 0.0
        self._capital += pnl

        closed = PaperTrade(
            **{
                **trade.__dict__,
                "closed_at":   time.time(),
                "exit_price":  exit_price,
                "exit_reason": reason,
                "pnl":         round(pnl, 2),
                "pnl_pct":     round(pnl_pct, 4),
            }
        )
        self._open_trade = None
        self._last_close_ts = time.time()

        logger.info(
            "PAPER CLOSE %s  %s @ ₹%.2f  reason=%s  pnl=₹%.2f (%.1f%%)",
            closed.id, closed.symbol, exit_price,
            reason.value, pnl, pnl_pct * 100,
        )

        # Check kill switch threshold after updating capital
        if self._should_kill():
            self._kill_active = True
            logger.warning("Kill switch threshold breached — halting for session")

        return closed

    def _should_kill(self) -> bool:
        if self._kill_active:
            return True
        if self._day_start_capital <= 0:
            return False
        loss_pct = (self._day_start_capital - self._capital) / self._day_start_capital
        return loss_pct >= self.config.daily_drawdown_kill_pct
