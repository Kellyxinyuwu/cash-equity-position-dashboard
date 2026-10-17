"""Command line. This file is how the two tables get their first rows.

seed writes positions. The share counts are the tuples in DEFAULT_POSITIONS,
not a download. pull writes prices by calling Tiingo through jobs.pull_closes.
report and serve only read the database. init creates empty tables.
serve is the local page. It does not call Tiingo.

The package is installed in .venv. The system python3 does not see book_move
until that environment is active, or until you run make, which uses .venv.
"""

import argparse
import json
import os
import sqlite3
from datetime import date
from pathlib import Path

from book_move.jobs import pull_closes
from book_move.marketdata import TiingoClient
from book_move.mcp_server import serve_stdio
from book_move.reporting import report_as_of
from book_move.storage import BENCHMARK_SYMBOL, connect, insert_trade, migrate, upsert_position
from book_move.web import serve

# Sample book written by `seed`. Symbols are real. Share counts and limits are
# ours. Tiingo is not asked for these rows.
DEFAULT_POSITIONS = (
    ("AAPL", "100", "0.60"),
    ("MSFT", "80", "0.60"),
    ("NVDA", "40", "0.60"),
    ("AMZN", "30", "0.60"),
    ("GOOGL", "20", "0.60"),
)


def main(argv: list[str] | None = None) -> None:
    """Run one command. Only `pull` calls Tiingo. The others read or seed SQLite."""
    # Step 1. Load .env so MARKET_DATA_API_KEY is available to `pull`.
    # A key already set in the shell wins. This function never prints the key.
    _load_env_file(Path(".env"))

    # Step 2. Pick a command. `--db` sits on the subcommand
    # (`book_move pull --db ...`), so it is parsed after the verb.
    parser = argparse.ArgumentParser(description="Book move explainer")
    subcommands = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=os.environ.get("BOOK_DB", "data/book.sqlite"))

    subcommands.add_parser("init", parents=[common], help="create tables")
    subcommands.add_parser("seed", parents=[common], help="write the sample US positions")

    pull = subcommands.add_parser("pull", parents=[common], help="store official daily closes")
    pull.add_argument("--start", required=True, type=date.fromisoformat)
    pull.add_argument("--end", type=date.fromisoformat)

    report = subcommands.add_parser(
        "report", parents=[common], help="print the as-of snapshot as JSON"
    )
    report.add_argument("--as-of", required=True, type=date.fromisoformat)

    serve_command = subcommands.add_parser(
        "serve", parents=[common], help="open the read-only page"
    )
    # Inside Docker the process must listen on 0.0.0.0. On a laptop the default stays localhost.
    serve_command.add_argument("--host", default=os.environ.get("BOOK_HOST", "127.0.0.1"))
    serve_command.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("BOOK_PORT", "8000")),
    )
    subcommands.add_parser(
        "mcp",
        parents=[common],
        help="stdio MCP tools for this book; reads the same SQLite file as the page",
    )
    trade = subcommands.add_parser(
        "trade",
        parents=[common],
        help="append one fill; does not change the position share count",
    )
    trade.add_argument("--symbol", required=True)
    trade.add_argument("--session", required=True, type=date.fromisoformat)
    trade.add_argument("--shares", required=True)
    trade.add_argument("--price", required=True)

    args = parser.parse_args(argv)

    # Step 3. Open the book and make sure both tables exist.
    # init stops here: empty positions, empty prices.
    connection = connect(Path(args.db))
    migrate(connection)
    if args.command == "init":
        return
    if args.command == "trade":
        # Step 1. A fill is ours, like a position. Positive shares were bought.
        # This command does not change positions.shares. If the shares are still
        # held at the close, that count must already include this fill.
        try:
            insert_trade(connection, args.symbol, args.session, args.shares, args.price)
            connection.commit()
        except ValueError as exc:
            # A zero share count or a non-positive price is not stored.
            raise SystemExit(str(exc)) from None
        except sqlite3.IntegrityError:
            # The symbol has to be seeded first. Do not create a position from a fill.
            raise SystemExit(f"no position for {args.symbol}") from None
        print(f"stored fill {args.symbol.strip()} {args.session.isoformat()}")
        return
    if args.command == "seed":
        # positions table. Share counts come from DEFAULT_POSITIONS.
        # This loop does not call Tiingo and does not write prices.
        for symbol, shares, limit in DEFAULT_POSITIONS:
            upsert_position(connection, symbol, shares, limit)
        connection.commit()
        return
    if args.command == "pull":
        # prices table, plus the benchmark series. SPY is not a position.
        # One request per symbol already stored in positions, then one for SPY.
        # Omitting --end means a single session: start through start.
        end = args.end if args.end is not None else args.start
        api_key = os.environ.get("MARKET_DATA_API_KEY", "")
        count = pull_closes(
            connection,
            TiingoClient(api_key),
            args.start,
            end,
            benchmark=BENCHMARK_SYMBOL,
        )
        print(f"stored {count} closes")
        return
    if args.command == "serve":
        # Local page. It opens its own connections, so close this one first.
        # Serving does not call Tiingo.
        connection.close()
        serve(args.db, host=args.host, port=args.port)
        return
    if args.command == "mcp":
        # Stdio MCP. Same database as serve. Does not call Tiingo or a model.
        connection.close()
        serve_stdio(args.db)
        return
    # report. JSON for one as-of date, from rows already in SQLite.
    snapshot = report_as_of(connection, args.as_of)
    print(json.dumps(snapshot.to_json(), indent=2))


def _load_env_file(path: Path) -> None:
    """Read KEY=VALUE lines. setdefault keeps a real environment variable."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        stripped = line.strip()
        # Skip blanks, comments, and lines that are not assignments.
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


if __name__ == "__main__":
    main()
