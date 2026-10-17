"""As-of report. Reads positions and prices already in SQLite.

Does not call Tiingo. A missing close on the as-of date stays blank. The
previous close is the latest official close strictly before that date.
Book P&L uses the share count currently in positions, so editing shares
changes P&L without another download.
"""

import sqlite3
from datetime import date
from decimal import Decimal

from book_move.analytics import (
    BenchmarkMark,
    BookSnapshot,
    NameMark,
    OptionMark,
    TradeFill,
    book_snapshot,
)
from book_move.storage import (
    BENCHMARK_SYMBOL,
    Position,
    StoredPrice,
    list_benchmark_prices,
    list_lasts,
    list_options,
    list_positions,
    list_prices,
    list_trades,
)


def report_as_of(connection: sqlite3.Connection, as_of: date) -> BookSnapshot:
    """Mark the book on as_of. A name with no close that session stays blank."""
    # Step 1. Positions are the current share counts. Prices stop at as_of.
    positions = list_positions(connection)
    prices = list_prices(connection, as_of)
    by_symbol = _prices_by_symbol(prices)
    # Step 2. Walk every stored session and keep the days the book could be marked.
    # That history is what published VaR needs. Today's missing P&L is not a zero.
    sessions = sorted({row.session_date for row in prices})
    book_pnls = [
        pnl
        for session in sessions
        if (pnl := _book_pnl(positions, by_symbol, session)) is not None
    ]
    # Step 3. The as-of snapshot splits today's fills and, when the benchmark
    # printed, splits clean P&L into market and residual. VaR history above
    # stays on total P&L. There is no position history.
    return book_snapshot(
        _marks(
            positions,
            by_symbol,
            as_of,
            _fills_on(connection, as_of),
            _lasts_on(connection, as_of),
        ),
        book_daily_pnls=book_pnls,
        benchmark=_benchmark_mark(list_benchmark_prices(connection, as_of), as_of),
        benchmark_symbol=BENCHMARK_SYMBOL,
        options=_option_marks(connection),
    )


def prior_name_pnls(
    connection: sqlite3.Connection,
    as_of: date,
) -> dict[str, tuple[Decimal, ...]]:
    """Each name's daily P&L on sessions strictly before as_of. Missing days are left out.

    Uses the share count currently in positions, same as the as-of report. Today is
    not included, so an abnormal-P&L check does not measure today's move against itself.
    """
    positions = list_positions(connection)
    prices = list_prices(connection, as_of)
    by_symbol = _prices_by_symbol(prices)
    sessions = sorted(day for day in {row.session_date for row in prices} if day < as_of)
    history = {position.symbol: [] for position in positions}
    for session in sessions:
        marked = book_snapshot(_marks(positions, by_symbol, session))
        for name in marked.names:
            if name.daily_pnl is not None:
                history[name.symbol].append(name.daily_pnl)
    return {symbol: tuple(pnls) for symbol, pnls in history.items()}


def _option_marks(connection: sqlite3.Connection) -> tuple[OptionMark, ...]:
    """Stored options. They are not stock positions, so they stay out of market value."""
    return tuple(
        OptionMark(row.symbol, row.quantity, row.delta, row.vega)
        for row in list_options(connection)
    )


def _benchmark_mark(
    rows: tuple[StoredPrice, ...],
    session: date,
) -> BenchmarkMark | None:
    """Yesterday's benchmark close and today's. No rows means there is no series yet."""
    if not rows:
        return None
    # Today's factor stays on today's row. A missing today is not yesterday's factor.
    previous_rows = [row for row in rows if row.session_date < session]
    today = next((row for row in rows if row.session_date == session), None)
    if today is None and not previous_rows:
        return None
    previous = previous_rows[-1] if previous_rows else None
    return BenchmarkMark(
        previous_close=None if previous is None else previous.close,
        close=None if today is None else today.close,
        split_factor=Decimal(1) if today is None else today.split_factor,
        div_cash=Decimal(0) if today is None else today.div_cash,
    )


def _book_pnl(
    positions: tuple[Position, ...],
    by_symbol: dict[str, dict[date, StoredPrice]],
    session: date,
) -> Decimal | None:
    """Book P&L on one session, using today's share counts against that day's closes."""
    return book_snapshot(_marks(positions, by_symbol, session)).book_pnl


def _lasts_on(connection: sqlite3.Connection, session: date) -> dict[str, Decimal]:
    """Today's last prices. A missing symbol is not filled from the official close."""
    return {row.symbol: row.last for row in list_lasts(connection, session)}


def _fills_on(connection: sqlite3.Connection, session: date) -> dict[str, tuple[TradeFill, ...]]:
    """Today's fills grouped by symbol. An empty dict means clean P&L equals actual P&L."""
    # Step 1. Read only this session. A fill on another day must not change today.
    grouped: dict[str, list[TradeFill]] = {}
    for trade in list_trades(connection, session):
        # Step 2. Keep the sign. A sell stays negative so yesterday's shares increase.
        grouped.setdefault(trade.symbol, []).append(TradeFill(trade.shares, trade.price))
    return {symbol: tuple(fills) for symbol, fills in grouped.items()}


def _marks(
    positions: tuple[Position, ...],
    by_symbol: dict[str, dict[date, StoredPrice]],
    session: date,
    fills: dict[str, tuple[TradeFill, ...]] | None = None,
    lasts: dict[str, Decimal] | None = None,
) -> list[NameMark]:
    """Pair each position with that session's close and the latest close before it.

    series.get(session) is None when the pull never stored that day. The previous
    close is not copied forward into the close field. Split factor and dividend
    come from today's row only. A missing row is factor 1 and dividend 0, and
    the P&L stays blank because the close is missing.
    """
    marks: list[NameMark] = []
    for position in positions:
        series = by_symbol.get(position.symbol, {})
        # Strictly before this session. The same day's close is not its own previous.
        previous_dates = [day for day in series if day < session]
        previous_row = series[max(previous_dates)] if previous_dates else None
        today = series.get(session)
        marks.append(
            NameMark(
                symbol=position.symbol,
                shares=position.shares,
                previous_close=None if previous_row is None else previous_row.close,
                # Missing key means missing close, not the prior day's price.
                close=None if today is None else today.close,
                limit=position.weight_limit,
                trades=() if fills is None else fills.get(position.symbol, ()),
                # Step 1. Today's corporate action. Do not reuse yesterday's factor.
                split_factor=Decimal(1) if today is None else today.split_factor,
                div_cash=Decimal(0) if today is None else today.div_cash,
                last=None if lasts is None else lasts.get(position.symbol),
            )
        )
    return marks


def _prices_by_symbol(prices: tuple[StoredPrice, ...]) -> dict[str, dict[date, StoredPrice]]:
    """symbol -> session_date -> stored close, split, and dividend."""
    by_symbol: dict[str, dict[date, StoredPrice]] = {}
    for row in prices:
        by_symbol.setdefault(row.symbol, {})[row.session_date] = row
    return by_symbol
