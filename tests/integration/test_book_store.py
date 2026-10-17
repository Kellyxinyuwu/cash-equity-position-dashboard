"""Stage 2: closes land in SQLite through a client. The report reads the database only."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from book_move.jobs import pull_closes
from book_move.marketdata import DailyClose, MarketDataError
from book_move.reporting import report_as_of
from book_move.storage import (
    connect,
    insert_trade,
    list_benchmark_prices,
    list_positions,
    list_prices,
    migrate,
    upsert_benchmark_price,
    upsert_last,
    upsert_option,
    upsert_position,
    upsert_price,
)

FETCHED_AT = datetime(2024, 1, 3, 21, 0, tzinfo=UTC)


def test_as_of_hides_later_closes_and_a_missing_day_stays_blank(tmp_path):
    connection = _database(tmp_path)
    upsert_position(connection, "AAPL", 10, "0.60")
    upsert_position(connection, "MSFT", 10, "0.60")
    connection.commit()
    pull_closes(connection, _FakeClient(), date(2024, 1, 2), date(2024, 1, 3), FETCHED_AT)

    wednesday = report_as_of(connection, date(2024, 1, 3))
    apple, microsoft = wednesday.names
    assert apple.daily_pnl == Decimal(100)
    assert microsoft.daily_pnl is None
    assert microsoft.close is None
    assert wednesday.book_pnl == Decimal(100)
    assert wednesday.gross_market_value == Decimal(1100)
    assert apple.weight == Decimal(1)

    tuesday = report_as_of(connection, date(2024, 1, 2))
    visible = list_prices(connection, date(2024, 1, 2))
    assert all(row.session_date <= date(2024, 1, 2) for row in visible)
    assert tuesday.names[0].close == Decimal(100)
    assert tuesday.names[0].daily_pnl is None
    assert tuesday.gross_market_value == Decimal(1500)
    stored = list_prices(connection, date(2024, 1, 3))
    assert stored[0].fetched_at == FETCHED_AT


def test_changing_shares_changes_pnl_without_touching_prices(tmp_path):
    connection = _database(tmp_path)
    upsert_position(connection, "AAPL", 10, "0.60")
    upsert_position(connection, "MSFT", 10, "0.60")
    connection.commit()
    pull_closes(connection, _FakeClient(), date(2024, 1, 2), date(2024, 1, 3), FETCHED_AT)

    upsert_position(connection, "AAPL", 20, "0.60")
    connection.commit()
    report = report_as_of(connection, date(2024, 1, 3))
    assert report.names[0].daily_pnl == Decimal(200)
    assert report.names[0].close == Decimal(110)


def test_a_stored_fill_splits_clean_pnl_from_actual_pnl(tmp_path):
    connection = _database(tmp_path)
    upsert_position(connection, "0700.HK", 250, "0.60")
    upsert_position(connection, "0005.HK", 500, "0.60")
    upsert_price(connection, "0700.HK", date(2024, 1, 2), 380, FETCHED_AT)
    upsert_price(connection, "0700.HK", date(2024, 1, 3), 390, FETCHED_AT)
    upsert_price(connection, "0005.HK", date(2024, 1, 2), 70, FETCHED_AT)
    upsert_price(connection, "0005.HK", date(2024, 1, 3), 68, FETCHED_AT)
    insert_trade(connection, "0700.HK", date(2024, 1, 3), 50, 385)
    connection.commit()
    report = report_as_of(connection, date(2024, 1, 3))
    names = {name.symbol: name for name in report.names}
    tencent, hsbc = names["0700.HK"], names["0005.HK"]
    assert tencent.clean_pnl == Decimal(2_000)
    assert tencent.daily_pnl == Decimal(2_250)
    assert hsbc.daily_pnl == Decimal(-1_000)
    assert report.book_clean_pnl == Decimal(1_000)
    assert report.book_pnl == Decimal(1_250)


def test_a_zero_share_fill_is_rejected(tmp_path):
    connection = _database(tmp_path)
    upsert_position(connection, "0700.HK", 200, "0.60")
    connection.commit()
    with pytest.raises(ValueError):
        insert_trade(connection, "0700.HK", date(2024, 1, 3), 0, 385)


def test_a_stored_split_marks_flat_and_a_pull_keeps_the_dividend(tmp_path):
    connection = _database(tmp_path)
    upsert_position(connection, "0700.HK", 2_000, "0.60")
    upsert_price(connection, "0700.HK", date(2024, 1, 2), 380, FETCHED_AT)
    upsert_price(
        connection,
        "0700.HK",
        date(2024, 1, 3),
        38,
        FETCHED_AT,
        split_factor=10,
    )
    connection.commit()
    split_day = report_as_of(connection, date(2024, 1, 3))
    assert split_day.names[0].daily_pnl == Decimal(0)
    assert split_day.names[0].market_value == Decimal(76_000)
    assert list_prices(connection, date(2024, 1, 3))[-1].split_factor == Decimal(10)

    dividend = connect(tmp_path / "dividend.sqlite")
    migrate(dividend)
    upsert_position(dividend, "0700.HK", 200, "0.60")
    dividend.commit()
    pull_closes(dividend, _DividendClient(), date(2024, 1, 2), date(2024, 1, 3), FETCHED_AT)
    report = report_as_of(dividend, date(2024, 1, 3))
    assert report.names[0].daily_pnl == Decimal(0)
    assert report.names[0].dividend_cash == Decimal(400)
    assert list_prices(dividend, date(2024, 1, 3))[-1].div_cash == Decimal(2)


def test_an_option_is_stored_outside_the_cash_book(tmp_path):
    connection = _database(tmp_path)
    upsert_position(connection, "0700.HK", 200, "0.60")
    upsert_position(connection, "0005.HK", 500, "0.60")
    upsert_price(connection, "0700.HK", date(2024, 1, 2), 380, FETCHED_AT)
    upsert_price(connection, "0700.HK", date(2024, 1, 3), 390, FETCHED_AT)
    upsert_price(connection, "0005.HK", date(2024, 1, 2), 70, FETCHED_AT)
    upsert_price(connection, "0005.HK", date(2024, 1, 3), 68, FETCHED_AT)
    upsert_option(connection, "CALL", 100, "0.4", 2)
    connection.commit()
    report = report_as_of(connection, date(2024, 1, 3))
    assert [row.symbol for row in list_positions(connection)] == ["0005.HK", "0700.HK"]
    assert report.book_pnl == Decimal(1_000)
    assert report.gross_market_value == Decimal(112_000)
    assert report.book_dollar_pnl == Decimal(700)
    assert report.book_vega_pnl == Decimal(0)
    assert report.options[0].dollar_pnl == Decimal(40)
    assert report.options[0].vega_pnl == Decimal(200)
    with pytest.raises(ValueError):
        upsert_option(connection, "CALL", 0, "0.4", 2)


def test_a_stored_last_stays_off_the_official_pnl(tmp_path):
    connection = _database(tmp_path)
    upsert_position(connection, "0700.HK", 200, "0.60")
    upsert_position(connection, "0005.HK", 500, "0.60")
    upsert_price(connection, "0700.HK", date(2024, 1, 2), 380, FETCHED_AT)
    upsert_price(connection, "0700.HK", date(2024, 1, 3), 390, FETCHED_AT)
    upsert_price(connection, "0005.HK", date(2024, 1, 2), 70, FETCHED_AT)
    upsert_price(connection, "0005.HK", date(2024, 1, 3), 68, FETCHED_AT)
    upsert_last(connection, "0700.HK", date(2024, 1, 2), 395, FETCHED_AT)
    connection.commit()
    before = report_as_of(connection, date(2024, 1, 3))
    tencent = {name.symbol: name for name in before.names}["0700.HK"]
    assert tencent.daily_pnl == Decimal(2_000)
    assert tencent.intraday_pnl is None

    upsert_last(connection, "0700.HK", date(2024, 1, 3), 395, FETCHED_AT)
    connection.commit()
    report = report_as_of(connection, date(2024, 1, 3))
    by_symbol = {name.symbol: name for name in report.names}
    assert by_symbol["0700.HK"].daily_pnl == Decimal(2_000)
    assert by_symbol["0700.HK"].intraday_pnl == Decimal(3_000)
    assert by_symbol["0700.HK"].market_value == Decimal(78_000)
    assert by_symbol["0005.HK"].intraday_pnl is None
    assert report.book_pnl == Decimal(1_000)
    assert report.book_intraday_pnl == Decimal(3_000)
    assert report.gross_market_value == Decimal(112_000)
    with pytest.raises(ValueError):
        upsert_last(connection, "0700.HK", date(2024, 1, 3), 0, FETCHED_AT)


def test_a_benchmark_is_stored_without_becoming_a_position(tmp_path):
    connection = _database(tmp_path)
    upsert_position(connection, "0700.HK", 200, "0.60")
    upsert_position(connection, "0005.HK", 500, "0.60")
    upsert_price(connection, "0700.HK", date(2024, 1, 2), 380, FETCHED_AT)
    upsert_price(connection, "0700.HK", date(2024, 1, 3), 390, FETCHED_AT)
    upsert_price(connection, "0005.HK", date(2024, 1, 2), 70, FETCHED_AT)
    upsert_price(connection, "0005.HK", date(2024, 1, 3), 68, FETCHED_AT)
    upsert_benchmark_price(connection, "SPY", date(2024, 1, 2), 100, FETCHED_AT)
    upsert_benchmark_price(connection, "SPY", date(2024, 1, 3), 101, FETCHED_AT)
    connection.commit()

    report = report_as_of(connection, date(2024, 1, 3))
    by_symbol = {name.symbol: name for name in report.names}
    assert [row.symbol for row in list_positions(connection)] == ["0005.HK", "0700.HK"]
    assert by_symbol["0700.HK"].market_pnl == Decimal(760)
    assert by_symbol["0700.HK"].residual_pnl == Decimal(1_240)
    assert by_symbol["0700.HK"].daily_pnl == Decimal(2_000)
    assert report.book_market_pnl == Decimal(1_110)
    assert report.book_residual_pnl == Decimal(-110)
    assert report.book_pnl == Decimal(1_000)
    assert report.gross_market_value == Decimal(112_000)

    with pytest.raises(ValueError):
        upsert_benchmark_price(connection, " ", date(2024, 1, 3), 100, FETCHED_AT)


def test_a_benchmark_download_failure_writes_nothing(tmp_path):
    connection = _database(tmp_path)
    upsert_position(connection, "AAPL", 10, "0.60")
    connection.commit()
    with pytest.raises(MarketDataError):
        pull_closes(
            connection,
            _BrokenBenchmark(),
            date(2024, 1, 2),
            date(2024, 1, 3),
            FETCHED_AT,
            benchmark="SPY",
        )
    assert list_prices(connection, date(2024, 1, 3)) == ()
    assert list_benchmark_prices(connection, date(2024, 1, 3)) == ()


def test_sixty_stored_sessions_publish_historical_var(tmp_path):
    connection = _database(tmp_path)
    upsert_position(connection, "AAPL", 1, "0.60")
    connection.commit()
    pull_closes(connection, _RisingClient(), date(2024, 1, 1), date(2024, 3, 1), FETCHED_AT)
    report = report_as_of(connection, date(2024, 3, 1))
    assert report.historical_var == Decimal(0)
    assert report.to_json()["historical_var"]["window"] == 60


def _database(tmp_path):
    connection = connect(tmp_path / "book.sqlite")
    migrate(connection)
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


class _DividendClient:
    def daily_closes(self, symbol: str, start: date, end: date) -> list[DailyClose]:
        del start, end
        return [
            DailyClose(symbol, date(2024, 1, 2), Decimal(380)),
            DailyClose(symbol, date(2024, 1, 3), Decimal(378), div_cash=Decimal(2)),
        ]


class _BrokenBenchmark:
    def daily_closes(self, symbol: str, start: date, end: date) -> list[DailyClose]:
        del start, end
        if symbol == "SPY":
            raise MarketDataError("benchmark down")
        return [DailyClose(symbol, date(2024, 1, 2), Decimal(100))]


class _RisingClient:
    def daily_closes(self, symbol: str, start: date, end: date) -> list[DailyClose]:
        del start, end
        first = date(2024, 1, 1).toordinal()
        return [
            DailyClose(symbol, date.fromordinal(first + offset), Decimal(100 + offset))
            for offset in range(61)
        ]
