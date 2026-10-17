"""The due session is the New York trading day whose official close should exist."""

from datetime import datetime
from zoneinfo import ZoneInfo

from book_move.sessions import session_due

NEW_YORK = ZoneInfo("America/New_York")


def test_saturday_is_still_friday():
    # 2026-10-17 is a Saturday. The cash close that is due is Friday 2026-10-16.
    now = datetime(2026, 10, 17, 12, 0, tzinfo=NEW_YORK)
    assert session_due(now).isoformat() == "2026-10-16"


def test_monday_morning_is_still_friday():
    now = datetime(2026, 10, 19, 10, 0, tzinfo=NEW_YORK)
    assert session_due(now).isoformat() == "2026-10-16"


def test_the_close_is_due_at_half_past_four():
    before = datetime(2026, 10, 16, 16, 29, tzinfo=NEW_YORK)
    after = datetime(2026, 10, 16, 16, 30, tzinfo=NEW_YORK)
    assert session_due(before).isoformat() == "2026-10-15"
    assert session_due(after).isoformat() == "2026-10-16"
