"""Draft gate. The model is not called from here, and not called when no ticket exists.

apply_drafts is the only way a drafter runs. An empty ticket list returns
before the drafter is touched. draft_problems checks a finished paragraph
against the ticket fields: every number has to match a field, a missing-close
ticket has to say the close is missing, and trade advice fails the draft.
"""

import re
from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

from book_move.rules import Ticket

_ADVICE = re.compile(r"建议买入|建议卖出|建议加仓|建议减仓|\bbuy\b|\bsell\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


class Drafter(Protocol):
    def draft(self, ticket: Ticket) -> str:
        """Write from this ticket's fields only."""


def apply_drafts(tickets: Sequence[Ticket], drafter: Drafter) -> tuple[str, ...]:
    """Draft each ticket. Zero tickets means zero calls."""
    if not tickets:
        return ()
    return tuple(drafter.draft(ticket) for ticket in tickets)


def model_configured(api_key: str, url: str = "") -> bool:
    """A paid key, or a local server such as Ollama, can draft.

    The OpenAI URL with a blank key does not. Tickets still show either way.
    """
    if api_key.strip():
        return True
    return _is_local(url)


def _is_local(url: str) -> bool:
    lowered = url.lower()
    # host.docker.internal is the laptop, as seen from the page container.
    # 127.0.0.1 inside that container is the container itself, not Ollama.
    return (
        "127.0.0.1" in lowered
        or "localhost" in lowered
        or "host.docker.internal" in lowered
    )


def draft_problems(draft: str, ticket: Ticket) -> tuple[str, ...]:
    """Reasons a draft fails review. An empty tuple means the checks passed."""
    problems: list[str] = []
    if _ADVICE.search(draft):
        problems.append("trade advice")
    if ticket.rule == "missing_close" and not _says_missing(draft):
        problems.append("missing-close sentence absent")
    allowed = _allowed_numbers(ticket)
    for raw in _NUMBER.findall(draft.replace(",", "")):
        if not _matches(Decimal(raw), allowed):
            problems.append(f"untraced {raw}")
    return tuple(problems)


def _says_missing(draft: str) -> bool:
    lowered = draft.lower()
    return "没有收盘" in draft or "missing close" in lowered


def _allowed_numbers(ticket: Ticket) -> set[Decimal]:
    """Field values a draft is allowed to quote, plus weight and limit as percents."""
    allowed: set[Decimal] = set()
    for name in ticket.names:
        for value in (
            name.shares,
            name.previous_close,
            name.close,
            name.market_value,
            name.daily_pnl,
        ):
            _add_amount(allowed, value)
        _add_ratio(allowed, name.weight)
        _add_ratio(allowed, name.limit)
    for value in (ticket.book_pnl, ticket.gross_market_value, ticket.pnl_stdev, ticket.multiple):
        _add_amount(allowed, value)
    return allowed


def _add_amount(allowed: set[Decimal], value: Decimal | None) -> None:
    if value is None:
        return
    allowed.add(value)
    allowed.add(abs(value))


def _add_ratio(allowed: set[Decimal], value: Decimal | None) -> None:
    if value is None:
        return
    allowed.add(value)
    allowed.add((value * 100).quantize(Decimal("0.1")))


def _matches(number: Decimal, allowed: set[Decimal]) -> bool:
    return any(number == candidate for candidate in allowed)
