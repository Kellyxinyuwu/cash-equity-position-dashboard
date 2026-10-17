"""SQLite schema and read/write helpers. This file does not call Tiingo.

positions holds the book: symbol, shares, weight_limit. Those rows are written
by seed (or by a later position update), never by the price vendor.

prices holds one official close per symbol per session_date, plus fetched_at,
split_factor, and div_cash. The pull job is the writer. A missing event is
stored as split 1 and dividend 0. list_prices hides any session after as_of.
The pull does not change positions.shares.

Decimal values are stored as text so SQLite does not turn them into binary
floats. prices.symbol is a foreign key to positions.symbol.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from book_move.analytics import as_decimal

# The market series. It is not a position and it has no share count.
BENCHMARK_SYMBOL = "SPY"

# positions: what this book holds. Written by seed, not by Tiingo.
# prices: one official close per symbol per session. Written by the pull job.
# Money is TEXT so SQLite does not store a binary float.
SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    shares TEXT NOT NULL,
    weight_limit TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    close TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    split_factor TEXT NOT NULL DEFAULT '1',
    div_cash TEXT NOT NULL DEFAULT '0',
    PRIMARY KEY (symbol, session_date),
    FOREIGN KEY (symbol) REFERENCES positions (symbol)
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    shares TEXT NOT NULL,
    price TEXT NOT NULL,
    FOREIGN KEY (symbol) REFERENCES positions (symbol)
);

CREATE TABLE IF NOT EXISTS last_prices (
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    last TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (symbol, session_date),
    FOREIGN KEY (symbol) REFERENCES positions (symbol)
);

CREATE TABLE IF NOT EXISTS options (
    symbol TEXT PRIMARY KEY,
    quantity TEXT NOT NULL,
    delta TEXT NOT NULL,
    vega TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS benchmark_prices (
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    close TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    split_factor TEXT NOT NULL DEFAULT '1',
    div_cash TEXT NOT NULL DEFAULT '0',
    PRIMARY KEY (symbol, session_date)
);

CREATE TABLE IF NOT EXISTS ticket_reviews (
    as_of TEXT NOT NULL,
    symbol TEXT NOT NULL,
    rule TEXT NOT NULL,
    status TEXT NOT NULL,
    draft TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (as_of, symbol, rule)
);

CREATE TABLE IF NOT EXISTS pull_checks (
    expected_session TEXT PRIMARY KEY,
    checked_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Position:
    """One row of the positions table, already parsed back into Decimal."""

    symbol: str
    shares: Decimal
    weight_limit: Decimal


@dataclass(frozen=True)
class StoredPrice:
    """One row of the prices table. session_date is the trading day, not fetched_at."""

    symbol: str
    session_date: date
    close: Decimal
    fetched_at: datetime
    split_factor: Decimal = Decimal(1)
    div_cash: Decimal = Decimal(0)


def connect(path: Path) -> sqlite3.Connection:
    """Open the SQLite file, creating the parent directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    # Rows come back as dict-like objects, so later code can use row["close"].
    connection.row_factory = sqlite3.Row
    # Without this pragma, the prices -> positions foreign key is not enforced.
    connection.execute("PRAGMA foreign_keys = ON")
    # The page and the container can read this file at the same time.
    # WAL keeps a writer from blocking those readers. Five seconds is the wait
    # before a second writer reports the file as busy instead of failing at once.
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def migrate(connection: sqlite3.Connection) -> None:
    """Create tables if they are not there yet. Existing rows stay."""
    connection.executescript(SCHEMA)
    # Databases created before drafts existed have ticket_reviews without this column.
    columns = {row[1] for row in connection.execute("PRAGMA table_info(ticket_reviews)")}
    if "draft" not in columns:
        connection.execute("ALTER TABLE ticket_reviews ADD COLUMN draft TEXT NOT NULL DEFAULT ''")
    # Prices created before corporate actions have no split or dividend column.
    # Default 1 and 0 leaves every stored day as "no event", so P&L does not change.
    price_columns = {row[1] for row in connection.execute("PRAGMA table_info(prices)")}
    if "split_factor" not in price_columns:
        connection.execute("ALTER TABLE prices ADD COLUMN split_factor TEXT NOT NULL DEFAULT '1'")
    if "div_cash" not in price_columns:
        connection.execute("ALTER TABLE prices ADD COLUMN div_cash TEXT NOT NULL DEFAULT '0'")
    connection.commit()


def upsert_position(
    connection: sqlite3.Connection,
    symbol: str,
    shares: int | str | Decimal,
    weight_limit: int | str | Decimal,
) -> None:
    """Insert a position, or replace shares and the limit when the symbol already exists."""
    # ON CONFLICT updates the book in place. Prices for that symbol are left alone.
    connection.execute(
        """
        INSERT INTO positions (symbol, shares, weight_limit)
        VALUES (?, ?, ?)
        ON CONFLICT (symbol) DO UPDATE SET
            shares = excluded.shares,
            weight_limit = excluded.weight_limit
        """,
        (symbol, _decimal_text(shares), _decimal_text(weight_limit)),
    )


def upsert_price(
    connection: sqlite3.Connection,
    symbol: str,
    session_date: date,
    close: int | str | Decimal,
    fetched_at: datetime,
    split_factor: int | str | Decimal = 1,
    div_cash: int | str | Decimal = 0,
) -> None:
    """Insert one official close, or replace that session's close and corporate action."""
    # Step 1. A split of 1 and a dividend of 0 mean the day had no corporate action.
    # Step 2. The primary key is (symbol, session_date). A second pull overwrites.
    # This does not multiply positions.shares. Doing that on every read would
    # apply the same split again the next time the page opened.
    connection.execute(
        """
        INSERT INTO prices (symbol, session_date, close, fetched_at, split_factor, div_cash)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (symbol, session_date) DO UPDATE SET
            close = excluded.close,
            fetched_at = excluded.fetched_at,
            split_factor = excluded.split_factor,
            div_cash = excluded.div_cash
        """,
        (
            symbol,
            session_date.isoformat(),
            _decimal_text(close),
            fetched_at.isoformat(),
            _decimal_text(split_factor),
            _decimal_text(div_cash),
        ),
    )


def symbols_on_session(connection: sqlite3.Connection, session: date) -> set[str]:
    """Position symbols that already have an official close on this session."""
    rows = connection.execute(
        "SELECT symbol FROM prices WHERE session_date = ?",
        (session.isoformat(),),
    ).fetchall()
    return {row["symbol"] for row in rows}


def newest_price_session(connection: sqlite3.Connection) -> date | None:
    """The newest official close stored for any position. None when prices is empty."""
    row = connection.execute("SELECT MAX(session_date) AS session_date FROM prices").fetchone()
    if row is None or row["session_date"] is None:
        return None
    return date.fromisoformat(row["session_date"])


def benchmark_stored(
    connection: sqlite3.Connection,
    session: date,
    symbol: str = BENCHMARK_SYMBOL,
) -> bool:
    """True when this benchmark session is already in benchmark_prices."""
    row = connection.execute(
        """
        SELECT 1 FROM benchmark_prices
        WHERE symbol = ? AND session_date = ?
        """,
        (symbol, session.isoformat()),
    ).fetchone()
    return row is not None


def save_pull_check(connection: sqlite3.Connection, expected: date, checked_at: datetime) -> None:
    """Remember that the vendor had nothing for this due session.

    The next click must not call the vendor again until the due session changes.
    """
    connection.execute(
        """
        INSERT INTO pull_checks (expected_session, checked_at)
        VALUES (?, ?)
        ON CONFLICT (expected_session) DO UPDATE SET
            checked_at = excluded.checked_at
        """,
        (expected.isoformat(), checked_at.isoformat()),
    )


def has_pull_check(connection: sqlite3.Connection, expected: date) -> bool:
    """True after a pull already found no close for this due session."""
    row = connection.execute(
        "SELECT 1 FROM pull_checks WHERE expected_session = ?",
        (expected.isoformat(),),
    ).fetchone()
    return row is not None


def list_positions(connection: sqlite3.Connection) -> tuple[Position, ...]:
    """Every position, ordered by symbol. This is the book's inventory, not a price."""
    rows = connection.execute(
        "SELECT symbol, shares, weight_limit FROM positions ORDER BY symbol"
    ).fetchall()
    return tuple(
        Position(
            symbol=row["symbol"],
            shares=Decimal(row["shares"]),
            weight_limit=Decimal(row["weight_limit"]),
        )
        for row in rows
    )


@dataclass(frozen=True)
class StoredTrade:
    """One fill on one session. Positive shares were bought; negative shares were sold."""

    symbol: str
    session_date: date
    shares: Decimal
    price: Decimal


def insert_trade(
    connection: sqlite3.Connection,
    symbol: str,
    session_date: date,
    shares: int | str | Decimal,
    price: int | str | Decimal,
) -> None:
    """Append one fill. The symbol must already be a position. This does not change shares."""
    from decimal import InvalidOperation

    # Step 1. Reject a fill that cannot be marked. A float is refused by as_decimal.
    try:
        share_count = as_decimal(shares)
        trade_price = as_decimal(price)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("shares and price must be decimal text") from exc
    if not symbol.strip():
        raise ValueError("trade symbol is empty")
    if share_count == 0:
        raise ValueError("trade shares must not be zero")
    if trade_price <= 0:
        raise ValueError("trade price must be positive")
    # Step 2. Store the checked numbers. The foreign key rejects an unknown symbol.
    # This statement does not add those shares to positions.
    connection.execute(
        """
        INSERT INTO trades (symbol, session_date, shares, price)
        VALUES (?, ?, ?, ?)
        """,
        (
            symbol.strip(),
            session_date.isoformat(),
            _decimal_text(share_count),
            _decimal_text(trade_price),
        ),
    )


def list_trades(connection: sqlite3.Connection, session: date) -> tuple[StoredTrade, ...]:
    """Fills on this session only. Other days are not part of this split."""
    rows = connection.execute(
        """
        SELECT symbol, session_date, shares, price
        FROM trades
        WHERE session_date = ?
        ORDER BY id
        """,
        (session.isoformat(),),
    ).fetchall()
    return tuple(
        StoredTrade(
            symbol=row["symbol"],
            session_date=date.fromisoformat(row["session_date"]),
            shares=Decimal(row["shares"]),
            price=Decimal(row["price"]),
        )
        for row in rows
    )


def list_prices(
    connection: sqlite3.Connection,
    as_of: date,
) -> tuple[StoredPrice, ...]:
    """Official closes on or before as_of. Later sessions are invisible."""
    rows = connection.execute(
        """
        SELECT symbol, session_date, close, fetched_at, split_factor, div_cash
        FROM prices
        WHERE session_date <= ?
        ORDER BY symbol, session_date
        """,
        (as_of.isoformat(),),
    ).fetchall()
    return tuple(
        StoredPrice(
            symbol=row["symbol"],
            session_date=date.fromisoformat(row["session_date"]),
            close=Decimal(row["close"]),
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            split_factor=Decimal(row["split_factor"]),
            div_cash=Decimal(row["div_cash"]),
        )
        for row in rows
    )


@dataclass(frozen=True)
class StoredLast:
    """One intraday last. It is not an official close."""

    symbol: str
    session_date: date
    last: Decimal
    fetched_at: datetime


def upsert_last(
    connection: sqlite3.Connection,
    symbol: str,
    session_date: date,
    last: int | str | Decimal,
    fetched_at: datetime,
) -> None:
    """Store one session's last price. This does not write or replace the official close."""
    # Step 1. A blank symbol is not a position. The foreign key also requires a real one.
    label = symbol.strip()
    if not label:
        raise ValueError("symbol is empty")
    # Step 2. A non-positive last is not a print. Do not store the official close instead.
    price = as_decimal(last)
    if price <= 0:
        raise ValueError("last must be positive")
    connection.execute(
        """
        INSERT INTO last_prices (symbol, session_date, last, fetched_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (symbol, session_date) DO UPDATE SET
            last = excluded.last,
            fetched_at = excluded.fetched_at
        """,
        (label, session_date.isoformat(), _decimal_text(price), fetched_at.isoformat()),
    )


def list_lasts(connection: sqlite3.Connection, session: date) -> tuple[StoredLast, ...]:
    """Last prices for this session only. Yesterday's last is not today's."""
    rows = connection.execute(
        """
        SELECT symbol, session_date, last, fetched_at
        FROM last_prices
        WHERE session_date = ?
        ORDER BY symbol
        """,
        (session.isoformat(),),
    ).fetchall()
    return tuple(
        StoredLast(
            symbol=row["symbol"],
            session_date=date.fromisoformat(row["session_date"]),
            last=Decimal(row["last"]),
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
        )
        for row in rows
    )


@dataclass(frozen=True)
class StoredOption:
    """An option position. Quantity, delta, and vega are inputs. There is no premium here."""

    symbol: str
    quantity: Decimal
    delta: Decimal
    vega: Decimal


def upsert_option(
    connection: sqlite3.Connection,
    symbol: str,
    quantity: int | str | Decimal,
    delta: int | str | Decimal,
    vega: int | str | Decimal,
) -> None:
    """Store one option. It is not a stock position and it has no official close."""
    # Step 1. A blank name is not a contract.
    label = symbol.strip()
    if not label:
        raise ValueError("option symbol is empty")
    # Step 2. Zero quantity is not a position. Delta and vega must be supplied.
    amount = as_decimal(quantity)
    if amount == 0:
        raise ValueError("option quantity must not be zero")
    connection.execute(
        """
        INSERT INTO options (symbol, quantity, delta, vega)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (symbol) DO UPDATE SET
            quantity = excluded.quantity,
            delta = excluded.delta,
            vega = excluded.vega
        """,
        (label, _decimal_text(amount), _decimal_text(delta), _decimal_text(vega)),
    )


def list_options(connection: sqlite3.Connection) -> tuple[StoredOption, ...]:
    """Every stored option, ordered by symbol. An empty tuple means a cash book."""
    rows = connection.execute(
        "SELECT symbol, quantity, delta, vega FROM options ORDER BY symbol"
    ).fetchall()
    return tuple(
        StoredOption(
            symbol=row["symbol"],
            quantity=Decimal(row["quantity"]),
            delta=Decimal(row["delta"]),
            vega=Decimal(row["vega"]),
        )
        for row in rows
    )


def upsert_benchmark_price(
    connection: sqlite3.Connection,
    symbol: str,
    session_date: date,
    close: int | str | Decimal,
    fetched_at: datetime,
    split_factor: int | str | Decimal = 1,
    div_cash: int | str | Decimal = 0,
) -> None:
    """Store one benchmark close. This does not insert a position or a share count."""
    # Step 1. A blank symbol would look like a holding with no name. Refuse it.
    label = symbol.strip()
    if not label:
        raise ValueError("benchmark symbol is empty")
    # Step 2. A bad corporate action must not be stored as a 0% market day.
    factor = as_decimal(split_factor)
    dividend = as_decimal(div_cash)
    if factor <= 0:
        raise ValueError("split factor must be positive")
    if dividend < 0:
        raise ValueError("dividend must not be negative")
    connection.execute(
        """
        INSERT INTO benchmark_prices (
            symbol, session_date, close, fetched_at, split_factor, div_cash
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (symbol, session_date) DO UPDATE SET
            close = excluded.close,
            fetched_at = excluded.fetched_at,
            split_factor = excluded.split_factor,
            div_cash = excluded.div_cash
        """,
        (
            label,
            session_date.isoformat(),
            _decimal_text(close),
            fetched_at.isoformat(),
            _decimal_text(factor),
            _decimal_text(dividend),
        ),
    )


def list_benchmark_prices(
    connection: sqlite3.Connection,
    as_of: date,
    symbol: str = BENCHMARK_SYMBOL,
) -> tuple[StoredPrice, ...]:
    """Benchmark closes on or before as_of. Later sessions are invisible."""
    rows = connection.execute(
        """
        SELECT symbol, session_date, close, fetched_at, split_factor, div_cash
        FROM benchmark_prices
        WHERE symbol = ? AND session_date <= ?
        ORDER BY session_date
        """,
        (symbol, as_of.isoformat()),
    ).fetchall()
    return tuple(
        StoredPrice(
            symbol=row["symbol"],
            session_date=date.fromisoformat(row["session_date"]),
            close=Decimal(row["close"]),
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            split_factor=Decimal(row["split_factor"]),
            div_cash=Decimal(row["div_cash"]),
        )
        for row in rows
    )


@dataclass(frozen=True)
class TicketReview:
    """A person's decision and the stored draft. The draft is empty until a model writes one."""

    status: str
    draft: str


def list_reviews(
    connection: sqlite3.Connection,
    as_of: date,
) -> dict[tuple[str, str], TicketReview]:
    """Decisions for this session, keyed by (symbol, rule). Missing means still open."""
    rows = connection.execute(
        "SELECT symbol, rule, status, draft FROM ticket_reviews WHERE as_of = ?",
        (as_of.isoformat(),),
    ).fetchall()
    return {
        (row["symbol"], row["rule"]): TicketReview(status=row["status"], draft=row["draft"])
        for row in rows
    }


def save_review(
    connection: sqlite3.Connection,
    as_of: date,
    symbol: str,
    rule: str,
    status: str,
) -> None:
    """Record confirm, reject, or a return to open. Does not change the ticket's numbers."""
    connection.execute(
        """
        INSERT INTO ticket_reviews (as_of, symbol, rule, status)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (as_of, symbol, rule) DO UPDATE SET status = excluded.status
        """,
        (as_of.isoformat(), symbol, rule, status),
    )


def save_draft(
    connection: sqlite3.Connection,
    as_of: date,
    symbol: str,
    rule: str,
    draft: str,
) -> None:
    """Store a draft that already passed the field check. Does not change confirm or reject."""
    connection.execute(
        """
        INSERT INTO ticket_reviews (as_of, symbol, rule, status, draft)
        VALUES (?, ?, ?, 'open', ?)
        ON CONFLICT (as_of, symbol, rule) DO UPDATE SET draft = excluded.draft
        """,
        (as_of.isoformat(), symbol, rule, draft),
    )


def _decimal_text(value: int | str | Decimal) -> str:
    """Store a decimal as text, for example '342.00', never a float."""
    return format(as_decimal(value), "f")
