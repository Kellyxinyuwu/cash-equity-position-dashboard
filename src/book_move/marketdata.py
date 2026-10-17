"""Tiingo HTTP client. This is the only file that knows the vendor URL.

Tiingo is a market-data company. daily_closes requests official end-of-day
prices for one symbol and a date range. The JSON fields we keep are close,
splitFactor, and divCash. adjClose is a split-and-dividend adjusted series
and is ignored. A missing splitFactor means 1. A missing divCash means 0.

There is no method here for share counts. A vendor like Tiingo does not
publish how many shares this book holds.
"""

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from book_move.analytics import as_decimal

TIINGO_PRICES_URL = "https://api.tiingo.com/tiingo/daily/{symbol}/prices"


class MarketDataError(Exception):
    """The vendor response could not be used. Callers must not invent a close."""


@dataclass(frozen=True)
class DailyClose:
    symbol: str
    session_date: date
    close: Decimal
    # 1 means the share count did not split. div_cash is cash per pre-split share.
    split_factor: Decimal = Decimal(1)
    div_cash: Decimal = Decimal(0)


class MarketDataClient(Protocol):
    def daily_closes(self, symbol: str, start: date, end: date) -> list[DailyClose]:
        """Official daily closes from start through end, inclusive."""


class TiingoClient:
    """One HTTP client for official daily closes. Tests pass a fake opener instead of urlopen."""

    def __init__(self, api_key: str, timeout_seconds: float = 10.0, opener: Any = None) -> None:
        # Refuse before any request. An empty key must not look like a successful pull.
        if not api_key:
            raise MarketDataError("MARKET_DATA_API_KEY is empty")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._opener = opener if opener is not None else _DefaultOpener()

    def daily_closes(self, symbol: str, start: date, end: date) -> list[DailyClose]:
        """GET one symbol's daily bars from start through end, inclusive."""
        if end < start:
            raise ValueError("end date is before start date")
        # Step 1. Build the daily-prices URL. Dates are YYYY-MM-DD.
        url = (
            f"{TIINGO_PRICES_URL.format(symbol=symbol)}"
            f"?startDate={start.isoformat()}&endDate={end.isoformat()}"
        )
        # Step 2. Tiingo expects the key in this header, not in the query string.
        request = Request(
            url,
            headers={
                "Authorization": f"Token {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        # Step 3. A timeout, an HTTP error, or a network error becomes MarketDataError.
        # The caller writes nothing when this raises.
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                raw = response.read().decode()
        except (TimeoutError, HTTPError, URLError) as exc:
            raise MarketDataError(f"tiingo request failed for {symbol}") from exc
        # Step 4. Parse numbers as Decimal. The default json parser would use float.
        try:
            payload = json.loads(raw, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            raise MarketDataError(f"tiingo returned non-JSON for {symbol}") from exc
        return parse_tiingo_prices(symbol, payload)


def parse_tiingo_prices(symbol: str, payload: Any) -> list[DailyClose]:
    """Read official close, plus splitFactor and divCash when the vendor sent them.

    adjClose is a different series and is ignored. A null close, a float close,
    or a row without date/close raises. No close is invented. A missing
    splitFactor is 1 and a missing divCash is 0, because that is a day with
    no corporate action. An explicit null is a broken row, not a default.
    """
    if not isinstance(payload, list):
        raise MarketDataError("tiingo prices response must be a list")
    bars: list[DailyClose] = []
    for item in payload:
        if not isinstance(item, dict) or "date" not in item or "close" not in item:
            raise MarketDataError("tiingo price row is missing date or close")
        # A null close is a missing print, not a zero. Leave it for the caller to skip.
        if item["close"] is None:
            raise MarketDataError("tiingo close is null")
        # parse_float=Decimal should have prevented this. Reject it if it still happens.
        if isinstance(item["close"], float):
            raise MarketDataError("close must not be a binary float")
        # Step 1. Official close. The session date is the first 10 characters.
        # Step 2. Corporate action fields. Absent means "no event". Null does not.
        bars.append(
            DailyClose(
                symbol=symbol,
                session_date=_session_date(item["date"]),
                close=as_decimal(item["close"]),
                split_factor=_action_amount(item, "splitFactor", Decimal(1)),
                div_cash=_action_amount(item, "divCash", Decimal(0)),
            )
        )
    return bars


def _action_amount(item: dict[str, Any], key: str, default: Decimal) -> Decimal:
    """A corporate-action amount. Missing uses default. Null, float, or <= 0 for a split fails."""
    if key not in item:
        return default
    value = item[key]
    # The vendor said the field exists and is empty. Do not invent 1 or 0.
    if value is None or isinstance(value, float):
        raise MarketDataError(f"tiingo {key} is missing or not decimal text")
    amount = as_decimal(value)
    if key == "splitFactor" and amount <= 0:
        raise MarketDataError("tiingo splitFactor must be positive")
    if key == "divCash" and amount < 0:
        raise MarketDataError("tiingo divCash must not be negative")
    return amount


def _session_date(value: Any) -> date:
    """Keep the calendar day. The time-of-day on Tiingo's timestamp is not a session."""
    if not isinstance(value, str) or len(value) < 10:
        raise MarketDataError("tiingo date is missing")
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise MarketDataError("tiingo date is not YYYY-MM-DD") from exc


class _DefaultOpener:
    """The real network call. Tests replace this with a fake opener."""

    def open(self, request: Request, timeout: float) -> Any:
        return urlopen(request, timeout=timeout)
