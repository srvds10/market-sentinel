"""
Instrument map: queries the Dhan option chain API to find the strikes
whose delta falls in the target range (0.10–0.15).

InstrumentManager runs a recalibration loop every 30 minutes and
notifies the SignalEngine of new symbol assignments.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import math
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import httpx

from core.signal import SignalEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class OptionStrike:
    symbol: str
    security_id: str
    strike_price: float
    is_call: bool
    delta: float
    ltp: float
    expiry_days: int = 5  # days to expiry, used for BS delta calc


@dataclass
class InstrumentMap:
    timestamp: float
    spot_symbol: str
    spot_security_id: str   # "13" for Nifty 50 on NSE_IDX
    spot_ltp: float
    atm_strike: float
    atm_call: OptionStrike
    atm_put: OptionStrike
    otm_call: OptionStrike  # delta closest to 0.10–0.15
    otm_put: OptionStrike


# ---------------------------------------------------------------------------
# Black-Scholes delta (for option chain delta filtering)
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    poly = t * (0.319381530
                + t * (-0.356563782
                       + t * (1.781477937
                              + t * (-1.821255978
                                     + t * 1.330274429))))
    p = 1.0 - (1.0 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * x * x) * poly
    return p if x >= 0 else 1.0 - p


def bs_delta(S: float, K: float, T: float, sigma: float, is_call: bool) -> float:
    if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
        return (1.0 if S > K else 0.0) if is_call else (-1.0 if S < K else 0.0)
    r  = 0.065
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0


def find_closest_delta_strike(strikes: list[OptionStrike], target: float) -> OptionStrike:
    return min(strikes, key=lambda s: abs(abs(s.delta) - target))


# ---------------------------------------------------------------------------
# InstrumentManager
# ---------------------------------------------------------------------------

class InstrumentManager:
    """Fetches the Dhan option chain and resolves the 5-leg instrument map.

    Recalibrates every 30 minutes so ATM/OTM strikes track a trending market.
    After each calibration it calls signal_engine.set_instrument_map() to
    re-anchor all rolling windows to the new symbols.
    """

    def __init__(
        self,
        signal_engine: SignalEngine,
        recalibration_interval_minutes: float = 30.0,
        target_delta_min: float = 0.10,
        target_delta_max: float = 0.15,
        dhan_client_id: str = "",
        dhan_access_token: str = "",
        instrument_name: str = "NIFTY",
    ) -> None:
        self._signal_engine   = signal_engine
        self._interval        = recalibration_interval_minutes * 60.0
        self._target_delta    = (target_delta_min + target_delta_max) / 2.0
        self._dhan_client_id  = dhan_client_id
        self._dhan_access_token = dhan_access_token
        self._instrument_name = instrument_name
        self._current_map: Optional[InstrumentMap] = None
        self._recalibrate_now = asyncio.Event()

    def update_token(self, token: str) -> None:
        """Hot-update the access token and trigger immediate recalibration."""
        self._dhan_access_token = token
        self._recalibrate_now.set()

    async def run(self) -> None:
        while True:
            try:
                await self._calibrate()
                # Wait for the interval or an immediate recalibrate request
                try:
                    await asyncio.wait_for(self._recalibrate_now.wait(), timeout=self._interval)
                    self._recalibrate_now.clear()
                    logger.info("Token updated — recalibrating instruments immediately")
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Recalibration failed: %s", exc, exc_info=True)
                # On failure, wait up to 60s or until a new token arrives
                try:
                    await asyncio.wait_for(self._recalibrate_now.wait(), timeout=60)
                    self._recalibrate_now.clear()
                    logger.info("Token updated — retrying recalibration")
                except asyncio.TimeoutError:
                    pass

    async def _calibrate(self) -> None:
        imap = await self._fetch_map()
        self._current_map = imap
        self._signal_engine.set_instrument_map(
            spot_symbol     = imap.spot_symbol,
            atm_call_symbol = imap.atm_call.symbol,
            atm_put_symbol  = imap.atm_put.symbol,
            otm_call_symbol = imap.otm_call.symbol,
            otm_put_symbol  = imap.otm_put.symbol,
        )
        logger.info(
            "Calibrated — ATM %.0f | OTM call %.0f (Δ=%.3f) | OTM put %.0f (Δ=%.3f)",
            imap.atm_strike,
            imap.otm_call.strike_price, imap.otm_call.delta,
            imap.otm_put.strike_price,  imap.otm_put.delta,
        )

    def current_map(self) -> Optional[InstrumentMap]:
        return self._current_map

    def get_dhan_instruments(self) -> list:
        """Return DhanInstrument list for the WS client subscription.
        Returns just Nifty spot until the first calibration completes.
        """
        from core.ws_client import DhanInstrument

        imap = self._current_map
        if imap is None:
            return [DhanInstrument("13", "NSE_IDX", "NIFTY-SPOT")]

        return [i for i in [
            DhanInstrument(imap.spot_security_id,        "NSE_IDX", imap.spot_symbol),
            DhanInstrument(imap.atm_call.security_id,    "NSE_FNO", imap.atm_call.symbol),
            DhanInstrument(imap.atm_put.security_id,     "NSE_FNO", imap.atm_put.symbol),
            DhanInstrument(imap.otm_call.security_id,    "NSE_FNO", imap.otm_call.symbol),
            DhanInstrument(imap.otm_put.security_id,     "NSE_FNO", imap.otm_put.symbol),
        ] if i.security_id]

    # ------------------------------------------------------------------
    # Instrument lookup via Dhan public scrip master (no auth required)
    # ------------------------------------------------------------------

    # Dhan publishes a daily instrument master CSV — no API key needed.
    _SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

    # Hardcoded spot security IDs for major indices on NSE_IDX
    _SPOT_SECURITY_IDS = {
        "NIFTY":       "13",
        "BANKNIFTY":   "25",
        "FINNIFTY":    "27",
        "MIDCPNIFTY":  "442",
    }

    async def _fetch_map(self) -> InstrumentMap:
        """Build instrument map from the public Dhan scrip master CSV.

        No authentication needed — the CSV is a public daily download.
        We need the spot LTP to calculate ATM strike, so we use the
        Dhan intraday chart API (which is in the official spec) as a
        fallback spot source when the WebSocket hasn't connected yet.
        If we have a current_map already, we reuse its spot_ltp.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(self._SCRIP_MASTER_URL)
            if not resp.is_success:
                raise ValueError(
                    f"Dhan scrip master download failed ({resp.status_code}). "
                    f"Check internet connectivity."
                )
            csv_text = resp.text

        # Parse: find nearest weekly/monthly expiry for this instrument
        calls, puts = self._parse_scrip_master(csv_text)

        if not calls or not puts:
            raise ValueError(
                f"No options found for {self._instrument_name} in scrip master"
            )

        # Use existing spot LTP if available, otherwise fall back to a
        # rough ATM calculation (will be corrected once WS ticks arrive)
        spot = self._current_map.spot_ltp if self._current_map else 0.0
        if spot <= 0:
            # Try to get spot from intraday chart (authenticated)
            spot = await self._fetch_spot_ltp()

        atm_strike = self._round_to_100(spot) if "BANK" in self._instrument_name \
                     else self._round_to_50(spot)

        # Calculate BS delta for each strike
        T     = max(calls[0].expiry_days, 1) / 252.0
        sigma = 0.14
        for s in calls + puts:
            s.delta = round(bs_delta(spot, s.strike_price, T, sigma, s.is_call), 4)

        atm_call  = min(calls, key=lambda s: abs(s.strike_price - atm_strike))
        atm_put   = min(puts,  key=lambda s: abs(s.strike_price - atm_strike))
        otm_calls = [c for c in calls if c.strike_price > atm_strike]
        otm_puts  = [p for p in puts  if p.strike_price < atm_strike]
        otm_call  = find_closest_delta_strike(otm_calls or calls, self._target_delta)
        otm_put   = find_closest_delta_strike(otm_puts  or puts,  self._target_delta)

        spot_security_id = self._SPOT_SECURITY_IDS.get(self._instrument_name, "13")

        logger.info(
            "Scrip master calibrated — spot=%.0f ATM=%.0f T=%dd | "
            "OTM call %s (Δ=%.3f) | OTM put %s (Δ=%.3f)",
            spot, atm_strike, atm_call.expiry_days,
            otm_call.symbol, otm_call.delta,
            otm_put.symbol,  otm_put.delta,
        )

        return InstrumentMap(
            timestamp        = time.time(),
            spot_symbol      = f"{self._instrument_name}-SPOT",
            spot_security_id = spot_security_id,
            spot_ltp         = spot,
            atm_strike       = atm_strike,
            atm_call         = atm_call,
            atm_put          = atm_put,
            otm_call         = otm_call,
            otm_put          = otm_put,
        )

    def _parse_scrip_master(self, csv_text: str) -> tuple[list[OptionStrike], list[OptionStrike]]:
        """Parse Dhan scrip master CSV and return call/put OptionStrike lists
        for the nearest expiry of self._instrument_name.

        CSV columns (relevant ones):
          SEM_EXM_EXCH_ID, SEM_SEGMENT, SEM_SMST_SECURITY_ID,
          SEM_INSTRUMENT_NAME, SEM_EXPIRY_DATE (YYYY-MM-DD),
          SEM_STRIKE_PRICE, SEM_OPTION_TYPE (CE/PE),
          SEM_TRADING_SYMBOL, SEM_CUSTOM_SYMBOL
        """
        reader = csv.DictReader(io.StringIO(csv_text))
        today  = date.today()

        # Collect all upcoming expiries for this underlying
        rows_by_expiry: dict[date, list[dict]] = {}
        for row in reader:
            seg  = row.get("SEM_SEGMENT", "")
            inst = row.get("SEM_INSTRUMENT_NAME", "")
            sym  = row.get("SEM_TRADING_SYMBOL", "") or row.get("SEM_CUSTOM_SYMBOL", "")
            opt  = row.get("SEM_OPTION_TYPE", "")

            if seg not in ("NSE_FNO", "NFO"):
                continue
            if inst not in ("OPTIDX", "OPTSTK"):
                continue
            if opt not in ("CE", "PE"):
                continue
            if not sym.startswith(self._instrument_name):
                continue

            exp_str = row.get("SEM_EXPIRY_DATE", "")
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            except ValueError:
                try:
                    exp_date = datetime.strptime(exp_str, "%d-%b-%Y").date()
                except ValueError:
                    continue

            if exp_date < today:
                continue
            rows_by_expiry.setdefault(exp_date, []).append(row)

        if not rows_by_expiry:
            raise ValueError(
                f"No upcoming {self._instrument_name} options found in scrip master. "
                f"The CSV may have different column names — first line: "
                f"{csv_text[:200]}"
            )

        nearest_expiry = min(rows_by_expiry)
        days_to_expiry = (nearest_expiry - today).days
        logger.info("Scrip master: using expiry %s (%d days away)", nearest_expiry, days_to_expiry)

        calls: list[OptionStrike] = []
        puts:  list[OptionStrike] = []

        for row in rows_by_expiry[nearest_expiry]:
            try:
                strike      = float(row.get("SEM_STRIKE_PRICE", 0))
                security_id = str(row.get("SEM_SMST_SECURITY_ID", "")).strip()
                opt_type    = row.get("SEM_OPTION_TYPE", "")
                symbol      = f"{self._instrument_name}-{int(strike)}-{opt_type}"
                is_call     = opt_type == "CE"
                obj = OptionStrike(
                    symbol=symbol, security_id=security_id,
                    strike_price=strike, is_call=is_call,
                    delta=0.0, ltp=0.0,
                    expiry_days=max(days_to_expiry, 1),
                )
                (calls if is_call else puts).append(obj)
            except (ValueError, KeyError):
                continue

        return sorted(calls, key=lambda s: s.strike_price), \
               sorted(puts,  key=lambda s: s.strike_price)

    async def _fetch_spot_ltp(self) -> float:
        """Get NIFTY spot via Dhan intraday chart (requires auth).
        Falls back to a hardcoded recent-ish value if auth fails.
        """
        if not self._dhan_access_token:
            logger.warning("No token — using fallback spot for initial calibration")
            return 24500.0  # rough NIFTY level; corrected once WS ticks arrive

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://api.dhan.co/v2/charts/intraday",
                    headers={
                        "access-token": self._dhan_access_token,
                        "client-id":    self._dhan_client_id,
                        "Content-Type": "application/json",
                    },
                    json={
                        "securityId":      "13",
                        "exchangeSegment": "IDX_I",
                        "instrument":      "INDEX",
                        "interval":        "1",
                        "fromDate":        date.today().strftime("%Y-%m-%d"),
                        "toDate":          date.today().strftime("%Y-%m-%d"),
                    },
                )
                if resp.is_success:
                    data = resp.json()
                    closes = data.get("close", [])
                    if closes:
                        return float(closes[-1])
        except Exception as e:
            logger.warning("Spot fetch via chart API failed: %s", e)

        logger.warning("Could not get live spot — using fallback; will recalibrate after first tick")
        return 24500.0

    @staticmethod
    def _round_to_50(price: float) -> float:
        return round(price / 50) * 50

    @staticmethod
    def _round_to_100(price: float) -> float:
        return round(price / 100) * 100
