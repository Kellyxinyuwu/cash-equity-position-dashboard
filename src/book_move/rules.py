"""Exception rules. They open tickets from numbers already computed.

Does not call Tiingo, a model, or the database. A ticket is fields, not a
paragraph. Three rules: limit breach, abnormal P&L, missing close. Equal to
the limit is not a breach. Fewer than 20 prior daily P&Ls does not open an
abnormal ticket and does not invent a standard deviation.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from book_move.analytics import NameSnapshot, _sample_stdev

# k = 2 lives here so the sentence and the test share one number.
ABNORMAL_PNL_MULTIPLE = Decimal("2")
ABNORMAL_PNL_WINDOW = 20
RULES = ("limit_breach", "abnormal_pnl", "missing_close")


@dataclass(frozen=True)
class Ticket:
    """One exception. `symbol` is the name that tripped the rule. `names` is the whole book."""

    rule: str
    symbol: str
    as_of: date
    shares: Decimal
    previous_close: Decimal | None
    close: Decimal | None
    market_value: Decimal | None
    daily_pnl: Decimal | None
    clean_pnl: Decimal | None
    weight: Decimal | None
    limit: Decimal
    gross_market_value: Decimal | None
    book_pnl: Decimal | None
    pnl_stdev: Decimal | None
    multiple: Decimal | None
    names: tuple[NameSnapshot, ...]

    def to_json(self, status: str = "open", draft: str = "") -> dict[str, object]:
        return {
            "rule": self.rule,
            "symbol": self.symbol,
            "as_of": self.as_of.isoformat(),
            "shares": _plain(self.shares),
            "previous_close": _plain(self.previous_close),
            "close": _plain(self.close),
            "market_value": _plain(self.market_value),
            "daily_pnl": _plain(self.daily_pnl),
            "clean_pnl": _plain(self.clean_pnl),
            "weight": _plain(self.weight),
            "limit": _plain(self.limit),
            "gross_market_value": _plain(self.gross_market_value),
            "book_pnl": _plain(self.book_pnl),
            "pnl_stdev": _plain(self.pnl_stdev),
            "multiple": _plain(self.multiple),
            "names": [_name_fields(name) for name in self.names],
            "status": status,
            "draft": draft,
        }


def collect_tickets(
    as_of: date,
    names: Sequence[NameSnapshot],
    book_pnl: Decimal | None,
    gross_market_value: Decimal | None,
    prior_pnls: Mapping[str, Sequence[Decimal]],
    multiple: Decimal = ABNORMAL_PNL_MULTIPLE,
    window: int = ABNORMAL_PNL_WINDOW,
) -> tuple[Ticket, ...]:
    """Open every rule that hits. One name can produce more than one ticket."""
    opened: list[Ticket] = []
    for name in names:
        # Step 1. Weight strictly above the stored limit. A missing weight is not a breach.
        if name.limit_breached:
            opened.append(
                _ticket(
                    "limit_breach",
                    as_of,
                    name,
                    names,
                    book_pnl,
                    gross_market_value,
                    pnl_stdev=None,
                    multiple=None,
                )
            )
        # Step 2. |today's P&L| > k × stdev of the prior window. Today is not inside that window.
        history = tuple(prior_pnls.get(name.symbol, ()))
        if name.daily_pnl is not None and len(history) >= window:
            stdev = _sample_stdev(history[-window:])
            if stdev is not None and abs(name.daily_pnl) > multiple * stdev:
                opened.append(
                    _ticket(
                        "abnormal_pnl",
                        as_of,
                        name,
                        names,
                        book_pnl,
                        gross_market_value,
                        pnl_stdev=stdev,
                        multiple=multiple,
                    )
                )
        # Step 3. No official close today. The previous close stays in its own field.
        if name.close is None:
            opened.append(
                _ticket(
                    "missing_close",
                    as_of,
                    name,
                    names,
                    book_pnl,
                    gross_market_value,
                    pnl_stdev=None,
                    multiple=None,
                )
            )
    return tuple(opened)


def _ticket(
    rule: str,
    as_of: date,
    name: NameSnapshot,
    names: Sequence[NameSnapshot],
    book_pnl: Decimal | None,
    gross_market_value: Decimal | None,
    pnl_stdev: Decimal | None,
    multiple: Decimal | None,
) -> Ticket:
    return Ticket(
        rule=rule,
        symbol=name.symbol,
        as_of=as_of,
        shares=name.shares,
        previous_close=name.previous_close,
        close=name.close,
        market_value=name.market_value,
        daily_pnl=name.daily_pnl,
        clean_pnl=name.clean_pnl,
        weight=name.weight,
        limit=name.limit,
        gross_market_value=gross_market_value,
        book_pnl=book_pnl,
        pnl_stdev=pnl_stdev,
        multiple=multiple,
        names=tuple(names),
    )


def _name_fields(name: NameSnapshot) -> dict[str, object]:
    return {
        "symbol": name.symbol,
        "shares": _plain(name.shares),
        "previous_close": _plain(name.previous_close),
        "close": _plain(name.close),
        "market_value": _plain(name.market_value),
        "daily_pnl": _plain(name.daily_pnl),
        "clean_pnl": _plain(name.clean_pnl),
        "weight": _plain(name.weight),
        "limit": _plain(name.limit),
    }


def _plain(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")
