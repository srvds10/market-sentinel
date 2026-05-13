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

        # One rolling window per tracked symbol
        self._windows: dict[str, RollingWindow] = {}
        self._last_z_score: float | None = None

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
    ) -> None:
        self._spot_symbol = spot_symbol
        self._atm_call_symbol = atm_call_symbol
        self._atm_put_symbol = atm_put_symbol
        self._otm_call_symbol = otm_call_symbol
        self._otm_put_symbol = otm_put_symbol

        tracked = {
            spot_symbol, atm_call_symbol, atm_put_symbol,
            otm_call_symbol, otm_put_symbol,
        }
        # Preserve existing windows so history is not lost mid-session
        for sym in tracked:
            if sym not in self._windows:
                self._windows[sym] = RollingWindow(self.config.window_seconds)

    # ------------------------------------------------------------------
    # Tick ingestion
    # ------------------------------------------------------------------

    def on_tick(self, tick: Tick) -> Optional[SignalEvent]:
        win = self._windows.get(tick.symbol)
        if win is None:
            return None
        win.push(tick.ltp, tick.timestamp)

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

    def z_score_sample_count(self) -> int:
        return self._zscore.sample_count()

    def current_z_score(self) -> float | None:
        return self._last_z_score

    def latest_spot(self) -> float | None:
        win = self._windows.get(self._spot_symbol)
        return win.latest() if win else None
