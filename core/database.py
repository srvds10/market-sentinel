"""
Async SQLite wrapper using aiosqlite.

Tables:
  trades        — every paper trade open + close event
  signal_events — every fired SignalEvent (for back-analysis)
  system_log    — structured engine lifecycle messages
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS trades (
    id              TEXT PRIMARY KEY,
    opened_at       REAL NOT NULL,
    closed_at       REAL,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,     -- CALL | PUT
    entry_price     REAL NOT NULL,
    exit_price      REAL,
    qty             INTEGER NOT NULL,
    capital_at_risk REAL NOT NULL,
    z_score_entry   REAL NOT NULL,
    stop_loss       REAL NOT NULL,
    take_profit     REAL NOT NULL DEFAULT 0.0,
    exit_reason     TEXT,              -- STOP_LOSS | TAKE_PROFIT | DIVERGENCE | TIME_STOP | KILL_SWITCH
    pnl             REAL,
    pnl_pct         REAL
);

CREATE TABLE IF NOT EXISTS signal_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fired_at    REAL NOT NULL,
    z_score     REAL NOT NULL,
    ratio       REAL NOT NULL,
    direction   TEXT NOT NULL,
    spot_ltp    REAL NOT NULL,
    atm_symbol  TEXT NOT NULL,
    delta_spot  REAL NOT NULL,
    delta_otm   REAL NOT NULL,
    acted_on    INTEGER NOT NULL DEFAULT 0   -- 1 if a trade was opened
);

CREATE TABLE IF NOT EXISTS system_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at  REAL NOT NULL,
    level      TEXT NOT NULL,
    component  TEXT NOT NULL,
    message    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_opened  ON trades (opened_at);
CREATE INDEX IF NOT EXISTS idx_signals_fired  ON signal_events (fired_at);
CREATE INDEX IF NOT EXISTS idx_syslog_time    ON system_log (logged_at);
"""


class Database:
    def __init__(self, db_path: str = "data/market_sentinel.db") -> None:
        self._path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if self._db is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        await self._migrate()
        logger.info("Database opened: %s", self._path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    async def insert_trade_open(self, trade: dict) -> None:
        await self._execute(
            """INSERT INTO trades
               (id, opened_at, symbol, direction, entry_price,
                qty, capital_at_risk, z_score_entry, stop_loss, take_profit)
               VALUES (:id, :opened_at, :symbol, :direction, :entry_price,
                       :qty, :capital_at_risk, :z_score_entry, :stop_loss, :take_profit)""",
            trade,
        )

    async def update_trade_close(self, trade_id: str, update: dict) -> None:
        update["id"] = trade_id
        await self._execute(
            """UPDATE trades
               SET closed_at=:closed_at, exit_price=:exit_price,
                   exit_reason=:exit_reason, pnl=:pnl, pnl_pct=:pnl_pct
               WHERE id=:id""",
            update,
        )

    async def get_trades(
        self,
        limit: int = 100,
        open_only: bool = False,
    ) -> list[dict]:
        where = "WHERE closed_at IS NULL" if open_only else ""
        async with self._db.execute(
            f"SELECT * FROM trades {where} ORDER BY opened_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def daily_pnl(self, date_ts_start: float, date_ts_end: float) -> float:
        async with self._db.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM trades "
            "WHERE closed_at BETWEEN ? AND ? AND pnl IS NOT NULL",
            (date_ts_start, date_ts_end),
        ) as cur:
            row = await cur.fetchone()
        return float(row[0]) if row else 0.0

    # ------------------------------------------------------------------
    # Signal events
    # ------------------------------------------------------------------

    async def insert_signal(self, event: dict) -> int:
        async with self._db.execute(
            """INSERT INTO signal_events
               (fired_at, z_score, ratio, direction, spot_ltp, atm_symbol,
                delta_spot, delta_otm, acted_on)
               VALUES (:fired_at, :z_score, :ratio, :direction, :spot_ltp,
                       :atm_symbol, :delta_spot, :delta_otm, :acted_on)""",
            event,
        ) as cur:
            rowid = cur.lastrowid
        await self._db.commit()
        return rowid

    async def get_signals(self, limit: int = 50) -> list[dict]:
        async with self._db.execute(
            "SELECT *, fired_at AS timestamp FROM signal_events "
            "ORDER BY fired_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # System log
    # ------------------------------------------------------------------

    async def log(self, level: str, component: str, message: str) -> None:
        await self._execute(
            "INSERT INTO system_log (logged_at, level, component, message) VALUES (?,?,?,?)",
            (time.time(), level, component, message),
        )

    # ------------------------------------------------------------------
    # Records management
    # ------------------------------------------------------------------

    async def clear_records(self, trades: bool = True, signals: bool = True, logs: bool = False) -> dict[str, int]:
        """Delete rows from the requested tables. Returns row counts deleted per table."""
        counts: dict[str, int] = {}
        tables = []
        if trades:
            tables.append(("trades", "trades"))
        if signals:
            tables.append(("signal_events", "signals"))
        if logs:
            tables.append(("system_log", "logs"))
        for table, key in tables:
            async with self._db.execute(f"SELECT COUNT(*) FROM {table}") as cur:
                counts[key] = (await cur.fetchone())[0]
            await self._db.execute(f"DELETE FROM {table}")
        await self._db.commit()
        return counts

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _migrate(self) -> None:
        """Add columns introduced after initial deployment (safe to re-run)."""
        for ddl in [
            "ALTER TABLE trades ADD COLUMN take_profit REAL NOT NULL DEFAULT 0.0",
        ]:
            try:
                await self._db.execute(ddl)
                await self._db.commit()
            except Exception as exc:
                if "duplicate column" not in str(exc).lower() and "already exists" not in str(exc).lower():
                    logger.warning("Migration DDL failed unexpectedly: %s — %s", ddl, exc)

    async def _execute(self, sql: str, params: Any = ()) -> None:
        await self._db.execute(sql, params)
        await self._db.commit()
