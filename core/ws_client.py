"""
Tick provider: Dhan HQ live market data WebSocket.

Uses dhanhq.marketfeed.MarketFeed (callback-based) with a bridging
asyncio.Queue so ticks flow into the engine's event loop cleanly.

Exchange segment integer constants (from dhanhq.marketfeed):
  IDX=0  NSE=1  NSE_FNO=2

Emits Tick objects into the asyncio.Queue passed at construction.
instrument_provider is called fresh on every (re)connect so subscriptions
always reflect the latest ATM/OTM strikes after a 30-minute recalibration.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

from core.signal import Tick

logger = logging.getLogger(__name__)


@dataclass
class DhanInstrument:
    security_id: str
    exchange_segment: str   # "NSE_IDX" | "NSE_FNO" | "NSE_EQ"
    symbol: str             # human-readable label used as Tick.symbol


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


class DhanWSClient(TickProvider):
    """Live Nifty feed via Dhan HQ WebSocket (dhanhq.marketfeed.MarketFeed).

    Reconnects automatically on disconnection. On each (re)connect it
    calls instrument_provider() to get the freshest strike list from
    InstrumentManager (updated every 30 minutes).
    """

    # Maps our exchange segment strings to dhanhq integer constants
    _EXCHANGE_MAP = {
        "NSE_IDX":  0,   # IDX
        "NSE_EQ":   1,   # NSE
        "NSE_FNO":  2,   # NSE_FNO
        "NSE_CURR": 3,
        "BSE_FNO":  8,
    }

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
            from dhanhq import dhan_context as dhan_ctx_module
        except ImportError:
            logger.error("dhanhq not installed. Run: pip install dhanhq")
            await asyncio.sleep(30)
            return

        instruments = self._instrument_provider()
        if not instruments:
            logger.warning("No instruments yet — waiting for InstrumentManager")
            await asyncio.sleep(10)
            return

        dhan_instruments = []
        for inst in instruments:
            seg = self._EXCHANGE_MAP.get(inst.exchange_segment)
            if seg is None:
                logger.warning("Unknown exchange segment '%s' for %s — skipping",
                               inst.exchange_segment, inst.symbol)
                continue
            dhan_instruments.append((seg, inst.security_id, marketfeed.Ticker))

        if not dhan_instruments:
            logger.error("No valid instruments after segment mapping")
            await asyncio.sleep(10)
            return

        sym_map = {inst.security_id: inst.symbol for inst in instruments}

        logger.info("Dhan WS connecting — %d instruments: %s",
                    len(instruments), [i.symbol for i in instruments])

        tick_queue: asyncio.Queue[Tick] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def on_message(_feed_obj, data: dict) -> None:
            tick = self._parse(data, sym_map)
            if tick:
                loop.call_soon_threadsafe(tick_queue.put_nowait, tick)

        context = dhan_ctx_module.DhanContext(self._client_id, self._access_token)
        feed = marketfeed.MarketFeed(
            context, dhan_instruments, version="v2", on_message=on_message
        )

        feed_task = asyncio.create_task(feed._run_async())
        logger.info("Dhan WS connected")

        try:
            while self._running and not feed_task.done():
                try:
                    tick = await asyncio.wait_for(tick_queue.get(), timeout=2.0)
                    await self._emit(tick)
                except asyncio.TimeoutError:
                    continue
        finally:
            feed._running = False
            feed_task.cancel()
            try:
                await asyncio.wait_for(feed_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    @staticmethod
    def _parse(data: dict, sym_map: dict[str, str]) -> Tick | None:
        ltp = data.get("LTP") or data.get("ltp")
        security_id = str(data.get("security_id", ""))
        if not ltp or not security_id:
            return None
        try:
            return Tick(
                symbol=sym_map.get(security_id, security_id),
                security_id=security_id,
                ltp=float(ltp),
                timestamp=time.time(),
            )
        except (ValueError, TypeError):
            return None
