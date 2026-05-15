"""
Signal engine: rolling 30-second delta windows, OTM/ATM ratio,
20-minute Z-score baseline. Fires a SignalEvent when the ratio
crosses +2 SD above its own recent mean.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Tick:
    symbol: str
    security_id: str
    ltp: float
    timestamp: float
    volume: int = 0
    oi: int = 0


@dataclass(frozen=True)
class SignalEvent:
    timestamp: float
    z_score: float
    ratio: float
    spot_ltp: float
    atm_symbol: str
    otm_call_symbol: str
    otm_put_symbol: str
    direction: str          # 'CALL' | 'PUT' | 'BOTH'
    delta_spot: float
    delta_otm: float


# ---------------------------------------------------------------------------
# Rolling 30-second price window
# ---------------------------------------------------------------------------

class RollingWindow:
    """Keeps the last `seconds` worth of (timestamp, price) pairs.

    delta() returns latest_price - oldest_price in the window.
    Returns None when fewer than 2 data points exist.
    """

    def __init__(self, seconds: float = 30.0) -> None:
        self.seconds = seconds
        self._data: deque[tuple[float, float]] = deque()

    def push(self, price: float, ts: float | None = None) -> None:
        ts = ts if ts is not None else time.monotonic()
        self._data.append((ts, price))
        cutoff = ts - self.seconds
        while self._data and self._data[0][0] < cutoff:
            self._data.popleft()

    def delta(self) -> float | None:
        if len(self._data) < 2:
            return None
        return self._data[-1][1] - self._data[0][1]

    def latest(self) -> float | None:
        return self._data[-1][1] if self._data else None

    def __len__(self) -> int:
        return len(self._data)


# ---------------------------------------------------------------------------
# Z-score tracker over a rolling lookback window
# ---------------------------------------------------------------------------

class ZScoreTracker:
    """Maintains a time-bounded deque of ratio samples.

    zscore(current) returns how many SDs current is above the recent mean.
    Returns None until min_samples have been collected.
    """

    def __init__(self, lookback_minutes: float = 20.0, min_samples: int = 30) -> None:
        self.lookback_seconds = lookback_minutes * 60.0
        self.min_samples = min_samples
        self._samples: deque[tuple[float, float]] = deque()   # (timestamp, ratio)

    def push(self, ratio: float, ts: float | None = None) -> None:
        ts = ts if ts is not None else time.monotonic()
        self._samples.append((ts, ratio))
        cutoff = ts - self.lookback_seconds
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def zscore(self, current_ratio: float) -> float | None:
        if len(self._samples) < self.min_samples:
            return None
        values = np.array([r for _, r in self._samples], dtype=np.float64)
        mean = values.mean()
        std = values.std()
        if std < 1e-9:
            return None
        return float((current_ratio - mean) / std)

    def sample_count(self) -> int:
        return len(self._samples)


# ---------------------------------------------------------------------------
# Signal engine
# ---------------------------------------------------------------------------

@dataclass
class SignalConfig:
    window_seconds: float = 30.0
    min_spot_delta: float = 2.0          # sideways filter
    zscore_threshold: float = 2.0
    zscore_lookback_minutes: float = 20.0
    min_history_samples: int = 30
    bias_window_seconds: float = 60.0    # 1-min rolling window for option-flow bias


class SignalEngine:
    """Routes ticks to per-symbol rolling windows and emits SignalEvents.

    Expected symbol roles are set via set_instrument_map() which the
    InstrumentManager calls after every 30-minute recalibration.

    Call on_tick() for every incoming Tick. Returns a SignalEvent when
    the Z-score threshold is breached, otherwise None.
    """

    def __init__(self, config: SignalConfig) -> None:
        self.config = config
        self._zscore = ZScoreTracker(
            lookback_minutes=config.zscore_lookback_minutes,
            min_samples=config.min_history_samples,
        )

        # These are updated by InstrumentManager on recalibration
        self._spot_symbol: str = ""
        self._atm_call_symbol: str = ""
        self._atm_put_symbol: str = ""
        self._otm_call_symbol: str = ""
        self._otm_put_symbol: str = ""

        # One rolling window per tracked symbol (30s for Z-score signal)
        self._windows: dict[str, RollingWindow] = {}
        # Separate 1-min windows for option-flow bias (OTM vs ATM comparison)
        self._bias_windows: dict[str, RollingWindow] = {}
        # Near-OTM symbols sampled for the bias signal (2 strikes per side)
        self._bias_otm_call_symbols: list[str] = []
        self._bias_otm_put_symbols:  list[str] = []
        self._last_z_score: float | None = None

    def reset(self) -> None:
        """Clear all rolling windows and Z-score history.

        Called on every WS reconnect so a gap in the feed doesn't corrupt
        the rolling mean/stdev used to derive Z-scores.
        """
        self._zscore = ZScoreTracker(
            lookback_minutes=self.config.zscore_lookback_minutes,
            min_samples=self.config.min_history_samples,
        )
        self._windows.clear()
        self._bias_windows.clear()
        self._bias_otm_call_symbols = []
        self._bias_otm_put_symbols  = []
        self._last_z_score = None

    # ------------------------------------------------------------------
    # Instrument map wiring
    # ------------------------------------------------------------------

    def set_instrument_map(
        self,
        spot_symbol: str,
        atm_call_symbol: str,
        atm_put_symbol: str,
        otm_call_symbol: str,
        otm_put_symbol: str,
        bias_otm_call_symbols: list[str] | None = None,
        bias_otm_put_symbols:  list[str] | None = None,
    ) -> None:
        self._spot_symbol = spot_symbol
        self._atm_call_symbol = atm_call_symbol
        self._atm_put_symbol = atm_put_symbol
        self._otm_call_symbol = otm_call_symbol
        self._otm_put_symbol = otm_put_symbol
        # Fall back to the single execution-OTM if the caller didn't supply
        # a bias-OTM list (keeps tests and legacy callers working).
        self._bias_otm_call_symbols = list(bias_otm_call_symbols or [otm_call_symbol])
        self._bias_otm_put_symbols  = list(bias_otm_put_symbols  or [otm_put_symbol])

        tracked = {
            spot_symbol, atm_call_symbol, atm_put_symbol,
            otm_call_symbol, otm_put_symbol,
        }
        # Preserve existing windows so history is not lost mid-session
        for sym in tracked:
            if sym not in self._windows:
                self._windows[sym] = RollingWindow(self.config.window_seconds)

        # 1-min bias windows for ATM legs + the 2 near-OTM strikes on each side
        bias_syms = {atm_call_symbol, atm_put_symbol}
        bias_syms.update(self._bias_otm_call_symbols)
        bias_syms.update(self._bias_otm_put_symbols)
        for sym in bias_syms:
            if sym and sym not in self._bias_windows:
                self._bias_windows[sym] = RollingWindow(self.config.bias_window_seconds)

    def register_symbols(self, symbols: set[str]) -> None:
        """Pre-create rolling windows for grid strikes that aren't yet active.

        Called by the engine after each grid build so every subscribed strike
        accumulates history; when the active ATM/OTM rolls onto one of these
        strikes, it already has a populated window and can fire signals
        immediately without re-warming.
        """
        for sym in symbols:
            if sym and sym not in self._windows:
                self._windows[sym] = RollingWindow(self.config.window_seconds)

    # ------------------------------------------------------------------
    # Tick ingestion
    # ------------------------------------------------------------------

    def on_tick(self, tick: Tick) -> Optional[SignalEvent]:
        win = self._windows.get(tick.symbol)
        if win is None:
            return None
        win.push(tick.ltp, tick.timestamp)

        # Feed option-flow bias windows for ATM/OTM legs
        bias_win = self._bias_windows.get(tick.symbol)
        if bias_win is not None:
            bias_win.push(tick.ltp, tick.timestamp)

        # Need at least spot + one OTM window populated
        if tick.symbol not in (self._otm_call_symbol, self._otm_put_symbol):
            return None

        return self._evaluate(tick.timestamp)

    # ------------------------------------------------------------------
    # Core ratio + Z-score math
    # ------------------------------------------------------------------

    def _evaluate(self, ts: float) -> Optional[SignalEvent]:
        spot_win = self._windows.get(self._spot_symbol)
        atm_call_win = self._windows.get(self._atm_call_symbol)
        atm_put_win = self._windows.get(self._atm_put_symbol)
        otm_call_win = self._windows.get(self._otm_call_symbol)
        otm_put_win = self._windows.get(self._otm_put_symbol)

        if not all([spot_win, atm_call_win, atm_put_win, otm_call_win, otm_put_win]):
            return None

        delta_spot = spot_win.delta()
        if delta_spot is None:
            return None

        # Sideways filter: ignore if underlying hasn't moved enough
        if abs(delta_spot) < self.config.min_spot_delta:
            return None

        # Calculate OTM deltas and pick the more active leg
        delta_otm_call = otm_call_win.delta()
        delta_otm_put = otm_put_win.delta()

        # Use the leg whose absolute move is larger (leading leg)
        if delta_otm_call is None and delta_otm_put is None:
            return None

        if delta_otm_call is None:
            delta_otm = delta_otm_put
            direction = "PUT"
            otm_sym = self._otm_put_symbol
        elif delta_otm_put is None:
            delta_otm = delta_otm_call
            direction = "CALL"
            otm_sym = self._otm_call_symbol
        elif abs(delta_otm_call) >= abs(delta_otm_put):
            delta_otm = delta_otm_call
            direction = "CALL"
            otm_sym = self._otm_call_symbol
        else:
            delta_otm = delta_otm_put
            direction = "PUT"
            otm_sym = self._otm_put_symbol

        # Robust ratio: |ΔOption / ΔSpot| — spot is in denominator (never zero after filter)
        ratio = abs(delta_otm / delta_spot)

        self._zscore.push(ratio, ts)
        z = self._zscore.zscore(ratio)
        self._last_z_score = z

        if z is None or z < self.config.zscore_threshold:
            return None

        return SignalEvent(
            timestamp=ts,
            z_score=round(z, 4),
            ratio=round(ratio, 6),
            spot_ltp=spot_win.latest() or 0.0,
            atm_symbol=self._atm_call_symbol,
            otm_call_symbol=self._otm_call_symbol,
            otm_put_symbol=self._otm_put_symbol,
            direction=direction,
            delta_spot=round(delta_spot, 4),
            delta_otm=round(delta_otm, 4),
        )

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def market_bias(self) -> tuple[str, float | None]:
        """Option-flow market bias over the last 1 minute.

        Compares OTM vs ATM premium momentum:
          BULLISH  — OTM call premium rising faster than ATM call
          BEARISH  — OTM put premium rising faster than ATM put
          SIDEWAYS — both OTM call and OTM put premiums declining
          UNKNOWN  — mixed signals or insufficient data

        Returns (label, trigger_delta) where trigger_delta is the 1-min
        change in the OTM leg that determined the label (None for SIDEWAYS
        / UNKNOWN).
        """
        bw_atm_c = self._bias_windows.get(self._atm_call_symbol)
        bw_atm_p = self._bias_windows.get(self._atm_put_symbol)

        if bw_atm_c is None or bw_atm_p is None:
            return "UNKNOWN", None

        d_atm_c = bw_atm_c.delta()
        d_atm_p = bw_atm_p.delta()

        # Average 1-min delta across the 2 near-OTM strikes on each side.
        # A symbol with an unfilled window is skipped; we proceed if at least
        # one OTM per side has data.
        def _avg_otm_delta(symbols: list[str]) -> float | None:
            vals = []
            for s in symbols:
                w = self._bias_windows.get(s)
                if w is None:
                    continue
                d = w.delta()
                if d is not None:
                    vals.append(d)
            return sum(vals) / len(vals) if vals else None

        d_otm_c = _avg_otm_delta(self._bias_otm_call_symbols)
        d_otm_p = _avg_otm_delta(self._bias_otm_put_symbols)

        if any(d is None for d in [d_atm_c, d_atm_p, d_otm_c, d_otm_p]):
            return "UNKNOWN", None

        # BULLISH: OTM call rising faster than ATM call
        if d_otm_c > 0 and d_otm_c > d_atm_c:  # type: ignore[operator]
            return "BULLISH", round(d_otm_c, 2)

        # BEARISH: OTM put rising faster than ATM put
        if d_otm_p > 0 and d_otm_p > d_atm_p:  # type: ignore[operator]
            return "BEARISH", round(d_otm_p, 2)

        # SIDEWAYS: both OTM options declining
        if d_otm_c <= 0 and d_otm_p <= 0:  # type: ignore[operator]
            return "SIDEWAYS", None

        return "UNKNOWN", None

    def z_score_sample_count(self) -> int:
        return self._zscore.sample_count()

    def current_z_score(self) -> float | None:
        return self._last_z_score

    def latest_spot(self) -> float | None:
        win = self._windows.get(self._spot_symbol)
        return win.latest() if win else None
