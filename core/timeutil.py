"""IST time helpers.

The server typically runs in UTC, but the Indian market operates on
Asia/Kolkata (IST = UTC+5:30).  Every time-of-day comparison in the
trading logic (market-hours guard, morning-filter blackout, force-close)
MUST use IST, otherwise the daemon fires guards 5h 30m off.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(IST)


def hhmm_to_minutes(hhmm: str) -> int:
    """Parse 'HH:MM' to minutes-of-day."""
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def ist_minutes_now() -> int:
    now = now_ist()
    return now.hour * 60 + now.minute


def is_weekday_ist() -> bool:
    return now_ist().weekday() < 5
