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

    def snapshot(self) -> dict:
        c = sum(self._call_oi.values())
        p = sum(self._put_oi.values())
        val = (p / c) if c != 0 and p != 0 else None
        if val is None:
            sentiment = 'WAIT'
        elif val >= 1.2:
            sentiment = 'PUT_HEAVY'
        elif val <= 0.8:
            sentiment = 'CALL_HEAVY'
        else:
            sentiment = 'BALANCED'
        return {
            'pcr':       round(val, 3) if val is not None else None,
            'sentiment': sentiment,
            'call_oi':   c,
            'put_oi':    p,
        }
