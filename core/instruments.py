"""
Instrument map: resolves which strikes to track.

For live mode  — queries the Dhan option chain API to find strikes
                 whose delta falls in the target range (0.10–0.15).
For mock mode  — returns the MockTickFeed's fixed symbol names so the
                 rest of the system works identically.

InstrumentManager runs a recalibration loop every 30 minutes and
notifies the SignalEngine of new symbol assignments.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from core.signal import SignalEngine
from core.ws_client import MockTickFeed

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


@dataclass
class InstrumentMap:
    timestamp: float
    spot_symbol: str
    spot_ltp: float
    atm_strike: float
    atm_call: OptionStrike
    atm_put: OptionStrike
    otm_call: OptionStrike        # delta closest to 0.10–0.15
    otm_put: OptionStrike


# ---------------------------------------------------------------------------
# Black-Scholes delta (used for mock and for live chain filtering)
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
    if T <= 0 or S <= 0 or K <= 0:
        return (1.0 if S > K else 0.0) if is_call else (-1.0 if S < K else 0.0)
    r = 0.065
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0


def find_closest_delta_strike(
    strikes: list[OptionStrike],
    target_delta: float,
) -> OptionStrike:
    """Return the strike whose |delta| is closest to target_delta."""
    return min(strikes, key=lambda s: abs(abs(s.delta) - target_delta))


# ---------------------------------------------------------------------------
# InstrumentManager
# ---------------------------------------------------------------------------

class InstrumentManager:
    """Resolves and periodically recalibrates the 7-leg instrument map.

    In mock mode: returns fixed MockTickFeed symbols immediately.
    In live mode: calls the Dhan option chain REST endpoint.

    After each recalibration it calls signal_engine.set_instrument_map()
    so rolling windows are re-anchored to the new symbols.
    """

    def __init__(
        self,
        signal_engine: SignalEngine,
        recalibration_interval_minutes: float = 30.0,
        target_delta_min: float = 0.10,
        target_delta_max: float = 0.15,
        mock_mode: bool = True,
        dhan_client_id: str = "",
        dhan_access_token: str = "",
        instrument_name: str = "NIFTY",
    ) -> None:
        self._signal_engine = signal_engine
        self._interval = recalibration_interval_minutes * 60.0
        self._target_delta_mid = (target_delta_min + target_delta_max) / 2.0
        self._mock_mode = mock_mode
        self._dhan_client_id = dhan_client_id
        self._dhan_access_token = dhan_access_token
        self._instrument_name = instrument_name

        self._current_map: Optional[InstrumentMap] = None
        self._last_calibration: float = 0.0

    async def run(self) -> None:
        """Recalibration loop — runs for the life of the engine task."""
        while True:
            try:
                await self._calibrate()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Instrument recalibration failed: %s", exc, exc_info=True)
            await asyncio.sleep(self._interval)

    async def _calibrate(self) -> None:
        if self._mock_mode:
            imap = self._mock_map()
        else:
            imap = await self._live_map()

        self._current_map = imap
        self._last_calibration = time.monotonic()

        self._signal_engine.set_instrument_map(
            spot_symbol=imap.spot_symbol,
            atm_call_symbol=imap.atm_call.symbol,
            atm_put_symbol=imap.atm_put.symbol,
            otm_call_symbol=imap.otm_call.symbol,
            otm_put_symbol=imap.otm_put.symbol,
        )
        logger.info(
            "Instrument map calibrated — ATM %.0f | OTM call %.0f (Δ=%.3f) | OTM put %.0f (Δ=%.3f)",
            imap.atm_strike,
            imap.otm_call.strike_price, imap.otm_call.delta,
            imap.otm_put.strike_price,  imap.otm_put.delta,
        )

    def current_map(self) -> Optional[InstrumentMap]:
        return self._current_map

    # ------------------------------------------------------------------
    # Mock map (no API needed)
    # ------------------------------------------------------------------

    def _mock_map(self) -> InstrumentMap:
        spot = 22_000.0
        atm  = 22_000.0
        T    = 5.0 / 252.0
        sigma = 0.14

        atm_call_delta = bs_delta(spot, atm,        T, sigma, True)
        atm_put_delta  = bs_delta(spot, atm,        T, sigma, False)
        otm_call_delta = bs_delta(spot, atm + 200,  T, sigma, True)
        otm_put_delta  = bs_delta(spot, atm - 200,  T, sigma, False)

        return InstrumentMap(
            timestamp=time.time(),
            spot_symbol=MockTickFeed.SPOT,
            spot_ltp=spot,
            atm_strike=atm,
            atm_call=OptionStrike(
                symbol=MockTickFeed.ATM_CALL, security_id="1",
                strike_price=atm, is_call=True,
                delta=round(atm_call_delta, 4), ltp=0,
            ),
            atm_put=OptionStrike(
                symbol=MockTickFeed.ATM_PUT, security_id="2",
                strike_price=atm, is_call=False,
                delta=round(atm_put_delta, 4), ltp=0,
            ),
            otm_call=OptionStrike(
                symbol=MockTickFeed.OTM_CALL, security_id="3",
                strike_price=atm + 200, is_call=True,
                delta=round(otm_call_delta, 4), ltp=0,
            ),
            otm_put=OptionStrike(
                symbol=MockTickFeed.OTM_PUT, security_id="4",
                strike_price=atm - 200, is_call=False,
                delta=round(otm_put_delta, 4), ltp=0,
            ),
        )

    # ------------------------------------------------------------------
    # Live map via Dhan option chain API
    # ------------------------------------------------------------------

    async def _live_map(self) -> InstrumentMap:
        # Dhan option chain endpoint
        url = "https://api.dhan.co/v2/optionchain"
        headers = {
            "client-id": self._dhan_client_id,
            "access-token": self._dhan_access_token,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json={
                "UnderlyingScrip": self._instrument_name,
                "UnderlyingType": "INDEX" if self._instrument_name in ("NIFTY", "BANKNIFTY", "FINNIFTY") else "EQUITY",
                "ExpiryDate": "",   # nearest expiry
            })
            resp.raise_for_status()
            data = resp.json()

        return self._parse_option_chain(data)

    def _parse_option_chain(self, data: dict) -> InstrumentMap:
        spot = float(data["underlyingValue"])
        atm_strike = self._round_to_100(spot) if "BANK" in self._instrument_name else self._round_to_50(spot)

        calls: list[OptionStrike] = []
        puts:  list[OptionStrike] = []

        T = 5.0 / 252.0  # will be refined once we parse the expiry date
        sigma = 0.14

        for entry in data.get("data", []):
            strike = float(entry["strikePrice"])
            for side in ("CE", "PE"):
                is_call = (side == "CE")
                ltp = float(entry.get(f"{side}ltp", 0) or 0)
                security_id = str(entry.get(f"{side}securityId", ""))
                symbol = f"{self._instrument_name}-{int(strike)}-{side}"
                delta = bs_delta(spot, strike, T, sigma, is_call)
                strike_obj = OptionStrike(
                    symbol=symbol, security_id=security_id,
                    strike_price=strike, is_call=is_call,
                    delta=round(delta, 4), ltp=ltp,
                )
                (calls if is_call else puts).append(strike_obj)

        atm_call = min(calls, key=lambda s: abs(s.strike_price - atm_strike))
        atm_put  = min(puts,  key=lambda s: abs(s.strike_price - atm_strike))

        # OTM: keep only strikes farther than ATM from spot, then find target delta
        otm_calls = [c for c in calls if c.strike_price > atm_strike]
        otm_puts  = [p for p in puts  if p.strike_price < atm_strike]

        otm_call = find_closest_delta_strike(otm_calls or calls, self._target_delta_mid)
        otm_put  = find_closest_delta_strike(otm_puts  or puts,  self._target_delta_mid)

        return InstrumentMap(
            timestamp=time.time(),
            spot_symbol=f"{self._instrument_name}-SPOT",
            spot_ltp=spot,
            atm_strike=atm_strike,
            atm_call=atm_call,
            atm_put=atm_put,
            otm_call=otm_call,
            otm_put=otm_put,
        )

    @staticmethod
    def _round_to_50(price: float) -> float:
        return round(price / 50) * 50

    @staticmethod
    def _round_to_100(price: float) -> float:
        return round(price / 100) * 100
