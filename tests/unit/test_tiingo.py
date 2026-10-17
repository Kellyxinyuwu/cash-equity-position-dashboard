"""Tiingo rows are parsed without a network call. Official close is the stored field."""

from datetime import date
from decimal import Decimal

import pytest

from book_move.marketdata import MarketDataError, TiingoClient, parse_tiingo_prices


def test_parser_uses_official_close_and_ignores_adjusted_close():
    payload = [
        {
            "date": "2024-01-03T00:00:00.000Z",
            "close": Decimal("110.5"),
            "adjClose": Decimal("999"),
        }
    ]
    bars = parse_tiingo_prices("AAPL", payload)
    assert bars == [
        type(bars[0])(symbol="AAPL", session_date=date(2024, 1, 3), close=Decimal("110.5"))
    ]


def test_parser_keeps_split_and_dividend_and_ignores_adjusted_close():
    payload = [
        {
            "date": "2024-01-03T00:00:00.000Z",
            "close": Decimal("38"),
            "adjClose": Decimal("380"),
            "splitFactor": Decimal("10"),
            "divCash": Decimal("2"),
        }
    ]
    bars = parse_tiingo_prices("0700.HK", payload)
    assert bars[0].close == Decimal("38")
    assert bars[0].split_factor == Decimal("10")
    assert bars[0].div_cash == Decimal("2")


def test_missing_corporate_action_fields_mean_no_event():
    payload = [{"date": "2024-01-03T00:00:00.000Z", "close": Decimal("390")}]
    bars = parse_tiingo_prices("0700.HK", payload)
    assert bars[0].split_factor == Decimal(1)
    assert bars[0].div_cash == Decimal(0)


def test_null_split_factor_is_rejected():
    row = {
        "date": "2024-01-03T00:00:00.000Z",
        "close": Decimal("38"),
        "splitFactor": None,
    }
    with pytest.raises(MarketDataError):
        parse_tiingo_prices("0700.HK", [row])


def test_parser_rejects_a_row_without_close():
    row = {"date": "2024-01-03T00:00:00.000Z", "adjClose": Decimal("1")}
    with pytest.raises(MarketDataError):
        parse_tiingo_prices("AAPL", [row])


def test_client_sends_the_key_and_parses_the_body():
    opener = _Opener(
        b'[{"date":"2024-01-02T00:00:00.000Z","close":100.25,"adjClose":90}]'
    )
    bars = TiingoClient("test-key", timeout_seconds=3, opener=opener).daily_closes(
        "AAPL", date(2024, 1, 2), date(2024, 1, 3)
    )
    assert opener.timeout == 3
    assert opener.request.get_header("Authorization") == "Token test-key"
    assert "startDate=2024-01-02" in opener.request.full_url
    assert "endDate=2024-01-03" in opener.request.full_url
    assert bars[0].close == Decimal("100.25")


def test_empty_api_key_is_rejected_before_a_request():
    with pytest.raises(MarketDataError):
        TiingoClient("")


class _Opener:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return _Response(self.body)


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False
