"""
Tick providers.

DhanWSClient  — connects to the real Dhan HQ market-data WebSocket.
MockTickFeed  — generates synthetic Brownian-motion ticks with occasional
                IV-spike events so the signal engine can be tested without
                live credentials.

Both yield Tick objects via an asyncio.Queue injected at construction time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import struct
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosedError, WebSocketException

from core.signal import Tick

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class TickProvider(ABC):
    """Push Tick objects into queue; caller owns the queue."""

    def __init__(self, queue: asyncio.Queue[Tick]) -> None:
        self._queue = queue
        self._running = False

    @abstractmethod
    async def run(self) -> None:
        """Runs until stop() is called or a fatal error occurs."""
        ...

    def stop(self) -> None:
        self._running = False

    async def _emit(self, tick: Tick) -> None:
        await self._queue.put(tick)


# ---------------------------------------------------------------------------
# Dhan HQ WebSocket client
# ---------------------------------------------------------------------------

# Dhan feed uses a binary/JSON hybrid.  The subscription message is JSON;
# tick frames are binary structs prefixed by a 2-byte message type.
# See: https://dhanhq.co/docs/v2/live-market-feed/

_DHAN_FEED_URL = "wss://api-feed.dhan.co"
_MSG_TYPE_TICKER = 0x01
_MSG_TYPE_FULL   = 0x15


@dataclass
class DhanInstrument:
    security_id: str
    exchange_segment: str   # e.g. "NSE_EQ", "NSE_FNO"
    symbol: str


class DhanWSClient(TickProvider):
    """Live feed from Dhan HQ market data WebSocket.

    Automatically reconnects on disconnection.  Caller should wrap run()
    in a task and cancel it to shut down.
    """

    def __init__(
        self,
        queue: asyncio.Queue[Tick],
        client_id: str,
        access_token: str,
        instruments: list[DhanInstrument],
        reconnect_delay: float = 5.0,
    ) -> None:
        super().__init__(queue)
        self._client_id = client_id
        self._access_token = access_token
        self._instruments = instruments
        self._reconnect_delay = reconnect_delay

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                await self._connect_and_stream()
            except (ConnectionClosedError, WebSocketException, OSError) as exc:
                logger.warning("Dhan WS disconnected: %s — reconnecting in %ss",
                               exc, self._reconnect_delay)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Unexpected WS error: %s", exc, exc_info=True)
            if self._running:
                await asyncio.sleep(self._reconnect_delay)

    async def _connect_and_stream(self) -> None:
        async with websockets.connect(
            _DHAN_FEED_URL,
            extra_headers={
                "client-id": self._client_id,
                "authorization": f"Bearer {self._access_token}",
            },
        ) as ws:
            logger.info("Dhan WS connected")
            await ws.send(self._subscription_payload())
            async for raw in ws:
                if not self._running:
                    break
                tick = self._parse_frame(raw)
                if tick:
                    await self._emit(tick)

    def _subscription_payload(self) -> str:
        instruments = [
            {"security_id": i.security_id, "exchange_segment": i.exchange_segment}
            for i in self._instruments
        ]
        return json.dumps({
            "action": "subscribe",
            "mode": "ticker",
            "instruments": instruments,
        })

    def _parse_frame(self, raw: bytes | str) -> Tick | None:
        if isinstance(raw, str):
            return None
        if len(raw) < 3:
            return None
        msg_type = raw[0]
        if msg_type not in (_MSG_TYPE_TICKER, _MSG_TYPE_FULL):
            return None
        try:
            # Dhan binary ticker layout (simplified):
            # [0]  1B  message_type
            # [1]  1B  exchange_segment
            # [2:6]  4B  security_id (uint32)
            # [6:10] 4B  LTP (float32)
            # [10:14] 4B LTQ (uint32)
            # [14:18] 4B  OI  (uint32)
            security_id = str(struct.unpack_from(">I", raw, 2)[0])
            ltp = struct.unpack_from(">f", raw, 6)[0]
            # Map security_id back to symbol
            sym = next(
                (i.symbol for i in self._instruments if i.security_id == security_id),
                security_id,
            )
            return Tick(
                symbol=sym,
                security_id=security_id,
                ltp=round(float(ltp), 2),
                timestamp=time.time(),
            )
        except struct.error:
            return None


# ---------------------------------------------------------------------------
# Mock tick feed  (Brownian motion + occasional IV-spike events)
# ---------------------------------------------------------------------------

class MockTickFeed(TickProvider):
    """Synthetic tick feed for development and testing.

    Simulates:
    - Nifty spot as geometric Brownian motion
    - ATM / OTM options priced via a fast Black-Scholes approximation
    - Random IV spikes every 3–8 minutes that should trigger the signal engine
    """

    # ---- instrument symbols used by the mock feed ----
    SPOT        = "NIFTY-SPOT"
    ATM_CALL    = "NIFTY-ATM-CE"
    ATM_PUT     = "NIFTY-ATM-PE"
    OTM_CALL    = "NIFTY-OTM-CE"
    OTM_PUT     = "NIFTY-OTM-PE"

    def __init__(
        self,
        queue: asyncio.Queue[Tick],
        spot_start: float = 22_000.0,
        iv_base: float = 0.14,          # 14% base IV
        tick_interval: float = 0.5,     # seconds between tick batches
        spike_interval_range: tuple[float, float] = (180.0, 480.0),
    ) -> None:
        super().__init__(queue)
        self._spot = spot_start
        self._iv = iv_base
        self._tick_interval = tick_interval
        self._spike_low, self._spike_high = spike_interval_range

        # Derived strikes (recalculated when spot drifts significantly)
        self._atm_strike = self._round_to_50(spot_start)
        self._otm_call_strike = self._atm_strike + 200
        self._otm_put_strike  = self._atm_strike - 200

    async def run(self) -> None:
        self._running = True
        next_spike = time.monotonic() + random.uniform(self._spike_low, self._spike_high)

        while self._running:
            ts = time.time()
            mono = time.monotonic()

            # --- IV spike event ---
            in_spike = mono >= next_spike and mono < next_spike + 30.0
            if mono > next_spike + 30.0:
                next_spike = mono + random.uniform(self._spike_low, self._spike_high)
                logger.debug("Mock: IV spike ended, next in %.0fs",
                             next_spike - mono)

            iv_now = self._iv * (3.5 if in_spike else 1.0)

            # --- Brownian spot move ---
            dt = self._tick_interval
            drift = 0.0
            vol = 0.12  # annualised spot vol
            dW = random.gauss(0, math.sqrt(dt / (252 * 6.5 * 3600)))
            self._spot *= math.exp((drift - 0.5 * vol ** 2) * dt / (252 * 6.5 * 3600) + vol * dW)
            self._spot = round(self._spot, 2)

            # Recalibrate ATM strike if spot drifts > 100 pts
            if abs(self._spot - self._atm_strike) > 100:
                self._atm_strike = self._round_to_50(self._spot)
                self._otm_call_strike = self._atm_strike + 200
                self._otm_put_strike  = self._atm_strike - 200

            T = max((15.5 - (ts % 86400) / 3600) / 252, 1e-4)   # rough time to expiry

            atm_call_price = self._bs_price(self._spot, self._atm_strike,    T, iv_now, True)
            atm_put_price  = self._bs_price(self._spot, self._atm_strike,    T, iv_now, False)
            otm_call_price = self._bs_price(self._spot, self._otm_call_strike, T, iv_now, True)
            otm_put_price  = self._bs_price(self._spot, self._otm_put_strike,  T, iv_now, False)

            ticks = [
                Tick(self.SPOT,     "0",  self._spot,       ts),
                Tick(self.ATM_CALL, "1",  round(atm_call_price, 2), ts),
                Tick(self.ATM_PUT,  "2",  round(atm_put_price,  2), ts),
                Tick(self.OTM_CALL, "3",  round(otm_call_price, 2), ts),
                Tick(self.OTM_PUT,  "4",  round(otm_put_price,  2), ts),
            ]
            for t in ticks:
                await self._emit(t)

            await asyncio.sleep(self._tick_interval)

    # ------------------------------------------------------------------
    # Black-Scholes helpers (fast, no external calls)
    # ------------------------------------------------------------------

    @staticmethod
    def _bs_price(S: float, K: float, T: float, sigma: float, is_call: bool) -> float:
        if T <= 0:
            return max(S - K, 0.0) if is_call else max(K - S, 0.0)
        r = 0.065  # risk-free rate (approx RBI repo)
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        if is_call:
            return max(S * MockTickFeed._norm_cdf(d1) - K * math.exp(-r * T) * MockTickFeed._norm_cdf(d2), 0.05)
        else:
            return max(K * math.exp(-r * T) * MockTickFeed._norm_cdf(-d2) - S * MockTickFeed._norm_cdf(-d1), 0.05)

    @staticmethod
    def _norm_cdf(x: float) -> float:
        # Abramowitz & Stegun approximation (error < 7.5e-8)
        t = 1.0 / (1.0 + 0.2316419 * abs(x))
        poly = t * (0.319381530
                    + t * (-0.356563782
                           + t * (1.781477937
                                  + t * (-1.821255978
                                         + t * 1.330274429))))
        p = 1.0 - (1.0 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * x * x) * poly
        return p if x >= 0 else 1.0 - p

    @staticmethod
    def _round_to_50(price: float) -> float:
        return round(price / 50) * 50
