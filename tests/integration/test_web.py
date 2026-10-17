"""The page reads SQLite. A day the pull never stored is labeled missing close."""

import ast
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from book_move.jobs import pull_closes
from book_move.marketdata import DailyClose
from book_move.storage import connect, migrate, upsert_position
from book_move.web import page_view, render_page

FETCHED_AT = datetime(2024, 1, 3, 21, 0, tzinfo=UTC)


def test_page_shows_the_stored_close_and_leaves_a_gap_blank(tmp_path):
    page = render_page(_loaded(tmp_path), date(2024, 1, 3))
    assert "110.00" in page
    assert "100.00" in page
    assert "1,100.00" in page
    assert "2024-01-03T21:00:00+00:00" in page
    assert page.count("missing close") >= 1
    assert "999" not in page
    assert "fewer than 20 returns" in page
    assert "yes" in page


def test_page_view_leaves_a_missing_close_null(tmp_path):
    view = page_view(_loaded(tmp_path), date(2024, 1, 3))
    apple, microsoft = view["names"]
    assert apple["close"] == "110"
    assert apple["daily_pnl"] == "100"
    assert apple["limit_breached"] is True
    assert microsoft["close"] is None
    assert microsoft["daily_pnl"] is None
    assert microsoft["limit_breached"] is None
    assert microsoft["volatility"] is None
    assert view["book_pnl"] == "100"
    assert "999" not in str(view)


def test_page_opens_a_limit_ticket_and_a_missing_close_ticket(tmp_path):
    view = page_view(_loaded(tmp_path), date(2024, 1, 3))
    opened = {(ticket["symbol"], ticket["rule"]) for ticket in view["tickets"]}
    assert ("AAPL", "limit_breach") in opened
    assert ("MSFT", "missing_close") in opened
    missing = next(ticket for ticket in view["tickets"] if ticket["symbol"] == "MSFT")
    assert missing["close"] is None
    assert missing["draft"] == ""
    assert view["model_configured"] is False


def test_a_day_with_no_pull_still_lists_the_book(tmp_path):
    page = render_page(_loaded(tmp_path), date(2024, 1, 4))
    assert "AAPL" in page
    assert "MSFT" in page
    assert "110.00" not in page
    assert "missing close" in page


def test_page_module_does_not_import_the_vendor_client():
    source = Path("src/book_move/web.py").read_text()
    tree = ast.parse(source)
    imported = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "book_move.marketdata" not in imported


def _loaded(tmp_path):
    connection = connect(tmp_path / "book.sqlite")
    migrate(connection)
    upsert_position(connection, "AAPL", 10, "0.60")
    upsert_position(connection, "MSFT", 10, "0.60")
    connection.commit()
    pull_closes(connection, _FakeClient(), date(2024, 1, 2), date(2024, 1, 3), FETCHED_AT)
    return connection


class _FakeClient:
    def daily_closes(self, symbol: str, start: date, end: date) -> list[DailyClose]:
        series = {
            "AAPL": {date(2024, 1, 2): "100", date(2024, 1, 3): "110"},
            "MSFT": {date(2024, 1, 2): "50"},
        }
        return [
            DailyClose(symbol, session, Decimal(close))
            for session, close in series[symbol].items()
            if start <= session <= end
        ]
