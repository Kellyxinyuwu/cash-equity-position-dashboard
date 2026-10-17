"""Pull job: the only path from Tiingo into the prices table.

pull_closes reads every symbol already stored in positions, asks the client
for that symbol's official closes, then writes them in one transaction. It
does not invent positions. Add a symbol by inserting a positions row first,
then run pull again. One client handles every symbol. There is no per-company
scraper.

A failed request writes nothing. Running pull again for the same session
replaces close, split factor, dividend, and fetched_at. It does not change
how many shares the book holds.
"""

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from book_move.marketdata import MarketDataClient, MarketDataError, TiingoClient
from book_move.sessions import NEW_YORK, session_due
from book_move.storage import (
    BENCHMARK_SYMBOL,
    benchmark_stored,
    has_pull_check,
    list_positions,
    newest_price_session,
    save_pull_check,
    symbols_on_session,
    upsert_benchmark_price,
    upsert_price,
)

# Calendar days before the newest stored close. Enough for the benchmark's previous print.
PULL_LOOKBACK = timedelta(days=10)


def pull_closes(
    connection: sqlite3.Connection,
    client: MarketDataClient,
    start: date,
    end: date,
    fetched_at: datetime | None = None,
    benchmark: str | None = None,
) -> int:
    """Fetch every position's closes, then write them in one transaction.

    A vendor failure writes nothing. fetched_at records when this pull stored the row.
    `benchmark` is an extra symbol, stored without a share count. The command line
    passes SPY. Tests that omit it leave the benchmark table empty.
    """
    # Tests pass a fixed timestamp. A real pull stamps "now" in UTC.
    stamped_at = fetched_at if fetched_at is not None else datetime.now(UTC)
    # Step 1. Inventory comes from positions. A symbol that is not seeded is not pulled.
    symbols = [position.symbol for position in list_positions(connection)]
    # Step 2. The benchmark is not one of those symbols. A blank name means skip it.
    benchmark_symbol = "" if benchmark is None else benchmark.strip()
    # Step 3. Download every series before writing. One failure writes nothing.
    staged = [bar for symbol in symbols for bar in client.daily_closes(symbol, start, end)]
    bench_bars = [] if not benchmark_symbol else client.daily_closes(benchmark_symbol, start, end)
    # Step 4. One transaction. Share counts stay put. The benchmark gets no position row.
    with connection:
        for bar in staged:
            upsert_price(
                connection,
                bar.symbol,
                bar.session_date,
                bar.close,
                stamped_at,
                bar.split_factor,
                bar.div_cash,
            )
        for bar in bench_bars:
            upsert_benchmark_price(
                connection,
                bar.symbol,
                bar.session_date,
                bar.close,
                stamped_at,
                bar.split_factor,
                bar.div_cash,
            )
    return len(staged) + len(bench_bars)


class PullError(Exception):
    """The latest close could not be fetched. Callers must not invent a close."""


@dataclass(frozen=True)
class PullState:
    """Whether the due official close is already stored.

    ready means the button must not call the vendor. fetched means this call did.
    """

    due: date
    stored: date | None
    ready: bool
    fetched: bool
    bars: int

    def to_json(self) -> dict[str, object]:
        return {
            "due": self.due.isoformat(),
            "stored": None if self.stored is None else self.stored.isoformat(),
            "ready": self.ready,
            "fetched": self.fetched,
            "bars": self.bars,
        }


def pull_status(connection: sqlite3.Connection, now: datetime | None = None) -> PullState:
    """Read SQLite only. This does not call Tiingo."""
    return _state(connection, _clock(now), fetched=False, bars=0)


def refresh_closes(connection: sqlite3.Connection, now: datetime | None = None) -> PullState:
    """Pull the due session when it is missing. A ready book does not call the vendor."""
    clock = _clock(now)
    try:
        client = TiingoClient(os.environ.get("MARKET_DATA_API_KEY", ""))
        return pull_if_needed(connection, client, clock)
    except MarketDataError as exc:
        raise PullError(str(exc)) from exc


def pull_if_needed(
    connection: sqlite3.Connection,
    client: MarketDataClient,
    now: datetime,
) -> PullState:
    """Call the vendor only when the due close, or the benchmark, is not stored."""
    # Step 1. Already stored, or a previous pull found the vendor had nothing: stop.
    current = _state(connection, now, fetched=False, bars=0)
    if current.ready:
        return current
    # Step 2. Who already has this session. A pull that adds nobody is not retried.
    due = current.due
    before = _covered(connection, due)
    start, end = _window(current.stored, due, now.astimezone(NEW_YORK).date())
    count = pull_closes(
        connection,
        client,
        start,
        end,
        now,
        benchmark=BENCHMARK_SYMBOL,
    )
    after = _covered(connection, due)
    needed = _needed(connection)
    # Step 3. The vendor published nothing new for this due day. Remember that.
    if not needed <= after and after == before:
        save_pull_check(connection, due, now)
        connection.commit()
    return _state(connection, now, fetched=True, bars=count)


def _clock(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _state(connection: sqlite3.Connection, now: datetime, *, fetched: bool, bars: int) -> PullState:
    """ready when every position and the benchmark have the due close, or the vendor does not."""
    due = session_due(now)
    stored = newest_price_session(connection)
    positions = list_positions(connection)
    # An empty book has nothing to fetch.
    if not positions:
        return PullState(due, stored, True, fetched, bars)
    covered = _covered(connection, due)
    ready = _needed(connection) <= covered or has_pull_check(connection, due)
    return PullState(due, stored, ready, fetched, bars)


def _needed(connection: sqlite3.Connection) -> set[str]:
    return {position.symbol for position in list_positions(connection)} | {BENCHMARK_SYMBOL}


def _covered(connection: sqlite3.Connection, session: date) -> set[str]:
    found = symbols_on_session(connection, session)
    if benchmark_stored(connection, session):
        found.add(BENCHMARK_SYMBOL)
    return found


def _window(stored: date | None, due: date, today: date) -> tuple[date, date]:
    """A short range ending today, starting ten days before the close we already have."""
    anchor = due if stored is None else min(stored, due)
    return anchor - PULL_LOOKBACK, max(due, today)
