"""
NIFTY Put-Call Ratio (PCR) tracker.

PCR = total put OI / total call OI across the subscribed option grid.

Interpretation (directional, not contrarian):
  PCR > 1.2 → more put OI than call OI  → PUT HEAVY (bearish market positioning)
  PCR < 0.8 → more call OI than put OI  → CALL HEAVY (bullish market positioning)
  0.8–1.2   → balanced
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PCRTracker:
    def __init__(self) -> None:
        self._call_oi: dict[str, int] = {}  # security_id → OI
        self._put_oi:  dict[str, int] = {}

    def reset(self) -> None:
        self._call_oi.clear()
        self._put_oi.clear()

    def update(self, security_id: str, oi: int, is_call: bool) -> None:
        if oi <= 0:
            return
        if is_call:
            self._call_oi[security_id] = oi
        else:
            self._put_oi[security_id] = oi

    @property
    def total_call_oi(self) -> int:
        return sum(self._call_oi.values())

    @property
    def total_put_oi(self) -> int:
        return sum(self._put_oi.values())

    def pcr(self) -> float | None:
        c = self.total_call_oi
        p = self.total_put_oi
        if c == 0 or p == 0:
            return None
        return p / c

    def sentiment(self) -> str:
        val = self.pcr()
        if val is None:
            return 'WAIT'
        if val >= 1.2:
            return 'PUT_HEAVY'
        if val <= 0.8:
            return 'CALL_HEAVY'
        return 'BALANCED'

    def snapshot(self) -> dict:
        val = self.pcr()
        return {
            'pcr':      round(val, 3) if val is not None else None,
            'sentiment': self.sentiment(),
            'call_oi':  self.total_call_oi,
            'put_oi':   self.total_put_oi,
        }
