"""
NIFTY Put-Call Ratio (PCR) tracker.

PCR = near-ATM put OI / near-ATM call OI (±4 strikes from ATM).

Interpretation (directional, not contrarian):
  PCR > 1.1 → more put OI than call OI  → PUT HEAVY (bearish positioning)
  PCR < 0.9 → more call OI than put OI  → CALL HEAVY (bullish positioning)
  0.9–1.1   → balanced (trend used as tie-breaker in Gate 5)
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

    def snapshot(self) -> dict:
        c = sum(self._call_oi.values())
        p = sum(self._put_oi.values())
        val = (p / c) if c != 0 else None
        if val is None:
            sentiment = 'WAIT'
        elif val >= 1.1:
            sentiment = 'PUT_HEAVY'
        elif val <= 0.9:
            sentiment = 'CALL_HEAVY'
        else:
            sentiment = 'BALANCED'
        return {
            'pcr':       round(val, 3) if val is not None else None,
            'sentiment': sentiment,
            'call_oi':   c,
            'put_oi':    p,
        }
