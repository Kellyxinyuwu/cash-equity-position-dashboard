"""Which official session should already be in the book.

The session date is the New York trading day. A weekend has no new close.
On a weekday the official print is treated as due at 16:30 New York time,
half an hour after the cash close. This file does not call a vendor and
does not know market holidays.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
# 16:00 is the cash close. 16:30 gives the official print time to show up.
CLOSE_READY = (16, 30)


def session_due(now: datetime) -> date:
    """The newest official close the book should already hold."""
    local = now.astimezone(NEW_YORK)
    day = local.date()
    # Step 1. After 16:30 on a weekday, today's close is the one that is due.
    if day.weekday() < 5 and (local.hour, local.minute) >= CLOSE_READY:
        return day
    # Step 2. Before the close, and on Saturday or Sunday, the previous weekday.
    return previous_weekday(day)


def previous_weekday(day: date) -> date:
    """The weekday before day. Saturday and Sunday are skipped."""
    cursor = day - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor
