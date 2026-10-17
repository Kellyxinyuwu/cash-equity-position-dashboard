"""The button pulls a missing official close and leaves share counts alone."""

from datetime import UTC, date, datetime
from decimal import Decimal

from book_move.jobs import pull_if_needed, pull_status
from book_move.marketdata import DailyClose
from book_move.storage import (
    connect,
    list_positions,
    migrate,
    upsert_benchmark_price,
    upsert_position,
    upsert_price,
)

# Saturday afternoon in New York. The due session is Friday 2026-10-16.
NOW = datetime(2026, 10, 17, 20, 0, tzinfo=UTC)
FETCHED = datetime(2026, 10, 15, 21, 0, tzinfo=UTC)
THURSDAY = date(2026, 10, 15)
FRIDAY = date(2026, 10, 16)


def test_a_stored_close_is_not_fetched_again(tmp_path):
    connection = _book(tmp_path)
    upsert_price(connection, "AAPL", FRIDAY, "343", FETCHED)
    upsert_benchmark_price(connection, "SPY", FRIDAY, "500", FETCHED)
    connection.commit()
    client = _Client([])
    state = pull_if_needed(connection, client, NOW)
    assert state.ready is True
    assert state.fetched is False
    assert client.calls == []
    assert list_positions(connection)[0].shares == Decimal("100")


def test_a_missing_close_is_pulled_once(tmp_path):
    connection = _book(tmp_path)
    client = _Client(
        [
            DailyClose("AAPL", FRIDAY, Decimal("343")),
            DailyClose("SPY", FRIDAY, Decimal("500")),
        ]
    )
    state = pull_if_needed(connection, client, NOW)
    assert state.ready is True
    assert state.fetched is True
    assert state.stored == FRIDAY
    assert client.calls == ["AAPL", "SPY"]
    assert client.window == (date(2026, 10, 5), date(2026, 10, 17))
    assert list_positions(connection)[0].shares == Decimal("100")
    again = pull_if_needed(connection, client, NOW)
    assert again.fetched is False
    assert client.calls == ["AAPL", "SPY"]


def test_a_vendor_with_nothing_newer_is_not_asked_twice(tmp_path):
    connection = _book(tmp_path)
    client = _Client([])
    first = pull_if_needed(connection, client, NOW)
    assert first.ready is True
    assert first.fetched is True
    assert first.stored == THURSDAY
    second = pull_if_needed(connection, client, NOW)
    assert second.fetched is False
    assert client.calls == ["AAPL", "SPY"]


def test_status_does_not_call_the_vendor(tmp_path):
    connection = _book(tmp_path)
    state = pull_status(connection, NOW)
    assert state.ready is False
    assert state.due == FRIDAY
    assert state.stored == THURSDAY
    assert state.fetched is False


def _book(tmp_path):
    connection = connect(tmp_path / "book.sqlite")
    migrate(connection)
    upsert_position(connection, "AAPL", 100, "0.60")
    upsert_price(connection, "AAPL", THURSDAY, "342", FETCHED)
    connection.commit()
    return connection


class _Client:
    def __init__(self, bars: list[DailyClose]) -> None:
        self._bars = bars
        self.calls: list[str] = []
        self.window: tuple[date, date] | None = None

    def daily_closes(self, symbol: str, start: date, end: date) -> list[DailyClose]:
        self.calls.append(symbol)
        self.window = (start, end)
        return [
            bar
            for bar in self._bars
            if bar.symbol == symbol and start <= bar.session_date <= end
        ]
