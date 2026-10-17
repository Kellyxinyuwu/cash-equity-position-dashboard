"""Open the day's tickets from SQLite. Does not call Tiingo or a model.

The rules see the as-of snapshot plus each name's daily P&L on earlier
sessions. A stored review (confirmed or rejected) is joined back on
(as_of, symbol, rule). Refreshing the page recomputes the fields and does
not ask a model to write the draft.
"""

import sqlite3
from datetime import date

from book_move.reporting import prior_name_pnls, report_as_of
from book_move.rules import Ticket, collect_tickets
from book_move.storage import list_reviews


def open_tickets(connection: sqlite3.Connection, as_of: date) -> tuple[Ticket, ...]:
    """Rule hits for this session. The agent drafts from one of these, not from the database."""
    snapshot = report_as_of(connection, as_of)
    return collect_tickets(
        as_of,
        snapshot.names,
        snapshot.book_pnl,
        snapshot.gross_market_value,
        prior_name_pnls(connection, as_of),
    )


def tickets_as_of(connection: sqlite3.Connection, as_of: date) -> tuple[dict[str, object], ...]:
    """Tickets whose rules still hit, plus any draft already stored. Does not call a model."""
    reviews = list_reviews(connection, as_of)
    rendered = []
    for ticket in open_tickets(connection, as_of):
        review = reviews.get((ticket.symbol, ticket.rule))
        rendered.append(
            ticket.to_json(
                status="open" if review is None else review.status,
                draft="" if review is None else review.draft,
            )
        )
    return tuple(rendered)
