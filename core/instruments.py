"""
Instrument map: queries the Dhan option chain API to find the strikes
whose delta falls in the target range (0.10–0.15).

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

    async def run(self) -> None:
        while True:
            try:
                await self._calibrate()
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Recalibration failed: %s", exc, exc_info=True)
                # Retry quickly after failure (60s) rather than waiting the full interval
                await asyncio.sleep(60)

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
    # Dhan option chain API
    # ------------------------------------------------------------------

    async def _fetch_map(self) -> InstrumentMap:
        if not self._dhan_access_token:
            raise ValueError("Dhan access token not set — update it via the UI Config panel")

        url = "https://api.dhan.co/v2/optionchain"
        headers = {
            "client-id":    self._dhan_client_id,
            "access-token": self._dhan_access_token,
            "Content-Type": "application/json",
        }
        is_index = self._instrument_name in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")
        body: dict = {
            "UnderlyingScrip": self._instrument_name,
            "UnderlyingType":  "INDEX" if is_index else "EQUITY",
            # ExpiryDate omitted → Dhan returns the nearest expiry
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=body)
            if not resp.is_success:
                logger.error(
                    "Option chain API returned %d — Dhan response: %s",
                    resp.status_code, resp.text[:500],
                )
                resp.raise_for_status()
            data = resp.json()
        return self._parse(data)

    def _parse(self, data: dict) -> InstrumentMap:
        spot       = float(data["underlyingValue"])
        atm_strike = self._round_to_100(spot) if "BANK" in self._instrument_name \
                     else self._round_to_50(spot)

        calls: list[OptionStrike] = []
        puts:  list[OptionStrike] = []
        T     = 5.0 / 252.0   # approximate time-to-expiry
        sigma = 0.14

        for entry in data.get("data", []):
            strike = float(entry["strikePrice"])
            for side in ("CE", "PE"):
                is_call     = side == "CE"
                ltp         = float(entry.get(f"{side}ltp", 0) or 0)
                security_id = str(entry.get(f"{side}securityId", ""))
                symbol      = f"{self._instrument_name}-{int(strike)}-{side}"
                delta       = bs_delta(spot, strike, T, sigma, is_call)
                obj = OptionStrike(symbol=symbol, security_id=security_id,
                                   strike_price=strike, is_call=is_call,
                                   delta=round(delta, 4), ltp=ltp)
                (calls if is_call else puts).append(obj)

        if not calls or not puts:
            raise ValueError(
                f"Option chain returned no strikes for {self._instrument_name} "
                f"(calls={len(calls)}, puts={len(puts)})"
            )
        atm_call  = min(calls, key=lambda s: abs(s.strike_price - atm_strike))
        atm_put   = min(puts,  key=lambda s: abs(s.strike_price - atm_strike))
        otm_calls = [c for c in calls if c.strike_price > atm_strike]
        otm_puts  = [p for p in puts  if p.strike_price < atm_strike]
        otm_call  = find_closest_delta_strike(otm_calls or calls, self._target_delta)
        otm_put   = find_closest_delta_strike(otm_puts  or puts,  self._target_delta)

        spot_security_id = str(data.get("underlyingSecurityId", "13")) or "13"

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

    @staticmethod
    def _round_to_50(price: float) -> float:
        return round(price / 50) * 50

    @staticmethod
    def _round_to_100(price: float) -> float:
        return round(price / 100) * 100
