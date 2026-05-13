"""
Tick providers.

DhanWSClient  — connects to the real Dhan HQ market-data WebSocket
                using the official dhanhq library. Automatically
                reconnects and re-subscribes on disconnection.
MockTickFeed  — generates synthetic Brownian-motion ticks with occasional
                IV-spike events so the signal engine can be tested without
                live credentials.

Both yield Tick objects via an asyncio.Queue injected at construction time.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

from core.signal import Tick

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class TickProvider(ABC):
    def __init__(self, queue: asyncio.Queue[Tick]) -> None:
        self._queue = queue
        self._running = False

    @abstractmethod
    async def run(self) -> None: ...

    def stop(self) -> None:
        self._running = False

    async def _emit(self, tick: Tick) -> None:
        await self._queue.put(tick)


# ---------------------------------------------------------------------------
# Dhan instrument descriptor
# ---------------------------------------------------------------------------

@dataclass
class DhanInstrument:
    security_id: str
    exchange_segment: str   # "NSE_IDX" | "NSE_FNO" | "NSE_EQ"
    symbol: str             # human-readable label used as Tick.symbol


# ---------------------------------------------------------------------------
# Dhan HQ WebSocket client (official dhanhq library)
# ---------------------------------------------------------------------------

class DhanWSClient(TickProvider):
    """Live feed from Dhan HQ using the official dhanhq.marketfeed module.

    instrument_provider is a callable that returns the current list of
    DhanInstrument objects. It is called fresh on every (re)connect so
    the client always subscribes to the latest option strikes after a
    30-minute recalibration.
    """

    def __init__(
        self,
        queue: asyncio.Queue[Tick],
        client_id: str,
        access_token: str,
        instrument_provider: Callable[[], list[DhanInstrument]],
        reconnect_delay: float = 5.0,
    ) -> None:
        super().__init__(queue)
        self._client_id = client_id
        self._access_token = access_token
        self._instrument_provider = instrument_provider
        self._reconnect_delay = reconnect_delay

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                await self._connect_and_stream()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Dhan WS error: %s — reconnecting in %ss",
                               exc, self._reconnect_delay)
            if self._running:
                await asyncio.sleep(self._reconnect_delay)

    async def _connect_and_stream(self) -> None:
        try:
            from dhanhq import marketfeed
        except ImportError:
            logger.error("dhanhq package not installed. Run: pip install dhanhq")
            await asyncio.sleep(30)
            return

        instruments = self._instrument_provider()
        if not instruments:
            logger.warning("No instruments yet — waiting for InstrumentManager to calibrate")
            await asyncio.sleep(10)
            return

        # Convert to dhanhq format: (exchange_segment, security_id, feed_type)
        dhan_instruments = [
            (inst.exchange_segment, inst.security_id, marketfeed.Ticker)
            for inst in instruments
        ]

        # Build symbol lookup: security_id → symbol
        sym_map = {inst.security_id: inst.symbol for inst in instruments}

        logger.info("Dhan WS connecting — %d instruments: %s",
                    len(instruments), [i.symbol for i in instruments])

        async with marketfeed.DhanFeed(
            self._client_id,
            self._access_token,
            dhan_instruments,
            version="v2",
        ) as feed:
            logger.info("Dhan WS connected")
            async for data in feed:
                if not self._running:
                    break
                tick = self._parse(data, sym_map)
                if tick:
                    await self._emit(tick)

    @staticmethod
    def _parse(data: dict, sym_map: dict[str, str]) -> Tick | None:
        ltp = data.get("LTP") or data.get("ltp")
        security_id = str(data.get("security_id", ""))
        if not ltp or not security_id:
            return None
        symbol = sym_map.get(security_id, security_id)
        return Tick(
            symbol=symbol,
            security_id=security_id,
            ltp=float(ltp),
            timestamp=time.time(),
        )


# ---------------------------------------------------------------------------
# Mock tick feed  (Brownian motion + occasional IV-spike events)
# ---------------------------------------------------------------------------

class MockTickFeed(TickProvider):
    """Synthetic tick feed for development and testing.

    Simulates Nifty spot + ATM/OTM options using Black-Scholes.
    IV spikes every 3-8 minutes trigger the signal engine.
    """

    SPOT     = "NIFTY-SPOT"
    ATM_CALL = "NIFTY-ATM-CE"
    ATM_PUT  = "NIFTY-ATM-PE"
    OTM_CALL = "NIFTY-OTM-CE"
    OTM_PUT  = "NIFTY-OTM-PE"

    def __init__(
        self,
        queue: asyncio.Queue[Tick],
        spot_start: float = 22_000.0,
        iv_base: float = 0.14,
        tick_interval: float = 0.5,
        spike_interval_range: tuple[float, float] = (180.0, 480.0),
    ) -> None:
        super().__init__(queue)
        self._spot = spot_start
        self._iv = iv_base
        self._tick_interval = tick_interval
        self._spike_low, self._spike_high = spike_interval_range
        self._atm_strike = self._round_to_50(spot_start)
        self._otm_call_strike = self._atm_strike + 200
        self._otm_put_strike  = self._atm_strike - 200

    async def run(self) -> None:
        self._running = True
        next_spike = time.monotonic() + random.uniform(self._spike_low, self._spike_high)

        while self._running:
            ts   = time.time()
            mono = time.monotonic()

            in_spike = next_spike <= mono < next_spike + 30.0
            if mono > next_spike + 30.0:
                next_spike = mono + random.uniform(self._spike_low, self._spike_high)

            iv_now = self._iv * (3.5 if in_spike else 1.0)

            dt  = self._tick_interval
            vol = 0.12
            dW  = random.gauss(0, math.sqrt(dt / (252 * 6.5 * 3600)))
            self._spot *= math.exp(-0.5 * vol**2 * dt / (252 * 6.5 * 3600) + vol * dW)
            self._spot = round(self._spot, 2)

            if abs(self._spot - self._atm_strike) > 100:
                self._atm_strike      = self._round_to_50(self._spot)
                self._otm_call_strike = self._atm_strike + 200
                self._otm_put_strike  = self._atm_strike - 200

            T = max((15.5 - (ts % 86400) / 3600) / 252, 1e-4)

            for sym, K, is_call, sec_id in [
                (self.SPOT,     self._spot,             True,  "0"),
                (self.ATM_CALL, self._atm_strike,       True,  "1"),
                (self.ATM_PUT,  self._atm_strike,       False, "2"),
                (self.OTM_CALL, self._otm_call_strike,  True,  "3"),
                (self.OTM_PUT,  self._otm_put_strike,   False, "4"),
            ]:
                price = self._spot if sym == self.SPOT else self._bs_price(self._spot, K, T, iv_now, is_call)
                await self._emit(Tick(sym, sec_id, round(price, 2), ts))

            await asyncio.sleep(self._tick_interval)

    @staticmethod
    def _bs_price(S, K, T, sigma, is_call):
        if T <= 0:
            return max(S - K, 0) if is_call else max(K - S, 0)
        r  = 0.065
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        N  = MockTickFeed._norm_cdf
        if is_call:
            return max(S * N(d1) - K * math.exp(-r * T) * N(d2), 0.05)
        return max(K * math.exp(-r * T) * N(-d2) - S * N(-d1), 0.05)

    @staticmethod
    def _norm_cdf(x):
        t = 1.0 / (1.0 + 0.2316419 * abs(x))
        p = 1.0 - (1.0 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * x * x) * t * (
            0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429)))
        )
        return p if x >= 0 else 1.0 - p

    @staticmethod
    def _round_to_50(price):
        return round(price / 50) * 50
