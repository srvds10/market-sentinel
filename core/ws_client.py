"""
Tick provider: Dhan HQ live market data WebSocket.

Uses dhanhq async API directly (bypasses run_forever which calls
asyncio.run() and crashes when uvicorn's loop is already running):

  await feed.connect()
  data = await feed.get_instrument_data()   # yields to event loop while waiting
  await feed.disconnect()
"""

from __future__ import annotations

import asyncio
import io
import logging
import struct
import time
from abc import ABC, abstractmethod
from contextlib import redirect_stdout
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
    """Live Nifty feed via Dhan HQ WebSocket.

    Uses the documented dhanhq v2.1.0+ API:
        feed = MarketFeed(dhan_context, instruments, version)
        feed.run_forever()
        while True:
            data = feed.get_data()

    Reconnects automatically on disconnection.
    """

    # Exchange segment string → MarketFeed class constant name + integer fallback
    # Integers are stable across versions; class attrs preferred when available
    _SEGMENT_ATTR = {
        "NSE_IDX":  ("IDX",      0),
        "NSE_EQ":   ("NSE",      1),
        "NSE_FNO":  ("NSE_FNO",  2),
        "NSE_CURR": ("NSE_CURR", 3),
        "BSE_FNO":  ("BSE_FNO",  8),
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
        delay = self._reconnect_delay
        while self._running:
            try:
                await self._connect_and_stream()
                delay = self._reconnect_delay  # reset backoff after a live session
            except asyncio.CancelledError:
                break
            except Exception as exc:
                exc_str = str(exc)
                is_429  = "429" in exc_str
                is_disc = "disconnect packet" in exc_str or "code=" in exc_str
                level   = logger.error if is_disc else logger.warning
                level("Dhan WS error: %s — reconnecting in %.0fs", exc, delay)
                if self._running:
                    await asyncio.sleep(delay)
                # Faster back-off on rate-limit (429), normal doubling otherwise
                delay = min(delay * (3 if is_429 else 2), 120.0)
                continue
            if self._running:
                await asyncio.sleep(delay)

    async def _connect_and_stream(self) -> None:
        try:
            from dhanhq import DhanContext, MarketFeed
        except ImportError:
            logger.error("dhanhq not installed — run: pip install dhanhq")
            await asyncio.sleep(30)
            return

        instruments = self._instrument_provider()
        if not instruments:
            logger.warning("No instruments yet — waiting for InstrumentManager")
            await asyncio.sleep(10)
            return

        dhan_instruments = []
        for inst in instruments:
            entry = self._SEGMENT_ATTR.get(inst.exchange_segment)
            if entry is None:
                logger.warning("Unknown exchange segment '%s' for %s — skipping",
                               inst.exchange_segment, inst.symbol)
                continue
            attr_name, fallback_int = entry
            seg = getattr(MarketFeed, attr_name, fallback_int)
            dhan_instruments.append((seg, inst.security_id, MarketFeed.Ticker))

        if not dhan_instruments:
            logger.error("No valid instruments after segment mapping")
            await asyncio.sleep(10)
            return

        sym_map = {inst.security_id: inst.symbol for inst in instruments}

        logger.info("Dhan WS connecting — %d instruments: %s",
                    len(instruments), [i.symbol for i in instruments])

        context = DhanContext(self._client_id, self._access_token)
        feed = MarketFeed(context, dhan_instruments, "v2")

        # Intercept dhanhq's server_disconnection() which only does print() internally.
        # We capture stdout and re-emit through the logger so the reason is visible.
        _orig_disc = feed.server_disconnection

        def _patched_disc(data: bytes) -> None:
            buf = io.StringIO()
            with redirect_stdout(buf):
                try:
                    _orig_disc(data)
                except Exception:
                    pass
            msg = buf.getvalue().strip()
            if msg:
                logger.error("Dhan server disconnect: %s", msg)
            else:
                # Parse raw bytes ourselves as fallback
                try:
                    code = struct.unpack("<H", data[8:10])[0]
                    logger.error("Dhan server disconnect code=%d "
                                 "(805=too many conns, 806=no subscription, "
                                 "807=token expired, 808=bad client ID, 809=auth failed)", code)
                except Exception:
                    logger.error("Dhan server sent disconnect packet (payload: %s)", data[:16].hex())

        feed.server_disconnection = _patched_disc  # type: ignore[method-assign]

        # run_forever() calls asyncio.run() internally which crashes when
        # another loop is already running (uvicorn). Use the async API directly.
        await feed.connect()
        logger.info("Dhan WS connected")

        try:
            while self._running:
                try:
                    data = await feed.get_instrument_data()
                except Exception as recv_exc:
                    code   = getattr(recv_exc, "code", None)
                    reason = getattr(recv_exc, "reason", None)
                    if code is not None or reason is not None:
                        hint = ""
                        if code == 1006:
                            hint = (
                                " — Server closed without explanation. "
                                "Check Dhan portal: (1) enable Market Feed / Live Data "
                                "subscription under your app, (2) whitelist this server's "
                                "IP under My Apps → Allowed IPs."
                            )
                        raise ConnectionError(
                            f"Dhan WS closed — code={code} reason={reason!r}{hint}"
                        ) from recv_exc
                    raise

                if data is None:
                    # Dhan sent a server-disconnect binary packet (first_byte=50).
                    # dhanhq prints the reason code (805–809) to stdout; we re-raise
                    # so the reconnect loop picks it up and the user sees it in the log.
                    raise ConnectionError(
                        "Dhan server sent disconnect packet — check journalctl stdout "
                        "for error code: 807=token expired, 808=bad client ID, "
                        "809=auth failed, 806=no subscription, 805=too many connections"
                    )

                tick = self._parse(data, sym_map)
                if tick:
                    await self._emit(tick)
        finally:
            try:
                await feed.disconnect()
            except Exception:
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
