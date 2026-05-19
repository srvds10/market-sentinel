"""
NIFTY heavyweight tracker.

Maintains an intraday TWAP (tick-weighted average price) for each
heavyweight stock and exposes a weighted directional score in [-1, +1]:

  +1 = all heavyweights trading above their VWAP (bullish)
  -1 = all heavyweights trading below their VWAP (bearish)
   0 = perfectly balanced

A stock only contributes once it has received ≥ 1 tick.  Absent stocks
are skipped when computing the score so a partial feed doesn't distort
the result.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field

from core.timeutil import now_ist

logger = logging.getLogger(__name__)


@dataclass
class HeavyweightStock:
    symbol: str          # NSE trading symbol, e.g. "HDFCBANK"
    weight: float        # raw NIFTY weight (%; will be normalised)
    security_id: str = ""
    ltp: float = 0.0
    _vwap_sum:   float = field(default=0.0, repr=False)
    _vwap_count: int   = field(default=0,   repr=False)
    _vwap_day:   int   = field(default=-1,  repr=False)

    # ---------------------------------------------------------------

    def on_tick(self, ltp: float) -> None:
        today_ord = now_ist().date().toordinal()
        if today_ord != self._vwap_day:
            self._vwap_sum   = 0.0
            self._vwap_count = 0
            self._vwap_day   = today_ord
        self._vwap_sum   += ltp
        self._vwap_count += 1
        self.ltp          = ltp

    @property
    def vwap(self) -> float | None:
        return self._vwap_sum / self._vwap_count if self._vwap_count > 0 else None

    @property
    def above_vwap(self) -> bool | None:
        v = self.vwap
        return None if v is None else self.ltp > v


class HeavyweightTracker:
    """
    Tracks the top-N NIFTY heavyweights and computes a weighted score.

    Parameters
    ----------
    stocks      : list of {symbol, weight} dicts from config
    threshold   : |score| below this → NEUTRAL (default 0.2)
    """

    def __init__(
        self,
        stocks: list[dict],
        threshold: float = 0.2,
    ) -> None:
        self._stocks: dict[str, HeavyweightStock] = {}   # security_id → stock
        self._by_symbol: dict[str, HeavyweightStock] = {}
        self._threshold = threshold

        total_w = sum(s["weight"] for s in stocks)
        for s in stocks:
            stock = HeavyweightStock(
                symbol=s["symbol"].upper(),
                weight=s["weight"] / total_w,  # normalise so weights sum to 1
            )
            self._by_symbol[stock.symbol] = stock

    # ------------------------------------------------------------------
    # Security-ID resolution (called once, from scrip master CSV)
    # ------------------------------------------------------------------

    def resolve_from_scrip_master(self, csv_text: str) -> list[str]:
        """
        Parse the Dhan scrip master CSV and fill in security_ids for all
        tracked stocks.  Returns a list of unresolved symbols.
        """
        reader = csv.DictReader(io.StringIO(csv_text))
        resolved = set()

        for row in reader:
            inst = row.get("SEM_INSTRUMENT_NAME", "").strip()
            if inst not in ("EQUITY", "EQ"):
                continue
            sym = (row.get("SEM_TRADING_SYMBOL") or row.get("SM_SYMBOL_NAME", "")).strip().upper()
            seg = row.get("SEM_EXM_EXCH_ID", row.get("SEM_SEGMENT", "")).strip().upper()
            if sym not in self._by_symbol:
                continue
            # Prefer the NSE exchange segment
            if seg not in ("NSE", "1", "N", "E"):
                continue
            sec_id = str(row.get("SEM_SMST_SECURITY_ID", "")).strip()
            if not sec_id:
                continue
            stock = self._by_symbol[sym]
            if not stock.security_id:           # take first match
                stock.security_id = sec_id
                self._stocks[sec_id] = stock
                resolved.add(sym)
                logger.debug("Heavyweight resolved %s → security_id=%s", sym, sec_id)

        unresolved = [s for s in self._by_symbol if s not in resolved]
        if unresolved:
            logger.warning("Heavyweights unresolved from scrip master: %s", unresolved)
        else:
            logger.info(
                "Heavyweights resolved: %d stocks",
                len(self._by_symbol),
            )
        return unresolved

    # ------------------------------------------------------------------
    # Tick ingestion
    # ------------------------------------------------------------------

    def on_tick(self, security_id: str, ltp: float) -> bool:
        """Returns True if the tick matched a tracked heavyweight."""
        stock = self._stocks.get(security_id)
        if stock:
            stock.on_tick(ltp)
            return True
        return False

    # ------------------------------------------------------------------
    # Score & direction
    # ------------------------------------------------------------------

    def weighted_score(self) -> float | None:
        """
        Weighted score in [-1, +1].
        Only stocks with at least one tick contribute.
        Returns None if no stock has received any ticks yet.
        """
        num = 0.0
        total_w = 0.0
        for stock in self._by_symbol.values():
            av = stock.above_vwap
            if av is None:
                continue
            num     += stock.weight * (1.0 if av else -1.0)
            total_w += stock.weight
        if total_w == 0:
            return None
        return num / total_w

    def direction(self) -> str:
        """'BULLISH' | 'BEARISH' | 'NEUTRAL' | 'WAIT'"""
        score = self.weighted_score()
        if score is None:
            return 'WAIT'
        if score >  self._threshold:
            return 'BULLISH'
        if score < -self._threshold:
            return 'BEARISH'
        return 'NEUTRAL'

    # ------------------------------------------------------------------
    # Snapshot for UI / heartbeat
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        score = self.weighted_score()
        stocks_info = [
            {
                "symbol":     s.symbol,
                "ltp":        round(s.ltp, 2),
                "vwap":       round(v, 2) if (v := s.vwap) is not None else None,
                "above_vwap": s.above_vwap,
                "weight":     round(s.weight * 100, 1),
            }
            for s in self._by_symbol.values()
        ]
        return {
            "score":     round(score, 4) if score is not None else None,
            "direction": self.direction(),
            "stocks":    sorted(stocks_info, key=lambda x: -x["weight"]),
        }

    # ------------------------------------------------------------------
    # DhanInstrument list for WS subscription
    # ------------------------------------------------------------------

    def dhan_instruments(self) -> list[tuple[str, str]]:
        """Returns [(security_id, 'NSE_EQ'), ...] for resolved stocks."""
        return [
            (s.security_id, "NSE_EQ")
            for s in self._by_symbol.values()
            if s.security_id
        ]
