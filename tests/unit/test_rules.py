"""Stage 4 rules. The mother-problem prices live only in this test."""

from datetime import date
from decimal import Decimal

from book_move.analytics import NameMark, book_snapshot
from book_move.explain import apply_drafts, draft_problems
from book_move.rules import collect_tickets

AS_OF = date(2024, 1, 3)
MOTHER = (
    NameMark("0700.HK", 200, 380, 390, "0.60"),
    NameMark("0005.HK", 500, 70, 68, "0.60"),
)
LIMIT_DRAFT = (
    "腾讯市值占组合毛市值 69.6%，限额 60%。持仓仍是 200 股，收盘从 380 到 390，"
    "该股日盈亏 +2,000。汇丰 500 股，收盘从 70 到 68，日盈亏 −1,000。"
    "组合日盈亏 +1,000。本说明不包含交易建议。"
)
MISSING_DRAFT = "汇丰当日没有收盘价，汇丰盈亏留空，不沿用前收。本说明不包含交易建议。"


def test_mother_limit_opens_only_for_tencent():
    tickets = collect_tickets(AS_OF, *_book(MOTHER), {})
    limits = [ticket for ticket in tickets if ticket.rule == "limit_breach"]
    assert [ticket.symbol for ticket in limits] == ["0700.HK"]
    tencent = limits[0]
    assert tencent.daily_pnl == Decimal(2000)
    assert tencent.book_pnl == Decimal(1000)
    assert tencent.close == Decimal(390)


def test_weight_equal_to_the_limit_does_not_open():
    marks = (NameMark("AAA", 10, 10, 10, "1"),)
    tickets = collect_tickets(AS_OF, *_book(marks), {})
    assert tickets == ()


def test_missing_close_opens_without_borrowing_the_previous_close():
    marks = (
        NameMark("0700.HK", 200, 380, 390, "0.60"),
        NameMark("0005.HK", 500, 70, None, "0.60"),
    )
    tickets = collect_tickets(AS_OF, *_book(marks), {})
    missing = [ticket for ticket in tickets if ticket.rule == "missing_close"]
    assert [ticket.symbol for ticket in missing] == ["0005.HK"]
    assert missing[0].close is None
    assert missing[0].daily_pnl is None
    assert missing[0].previous_close == Decimal(70)


def test_abnormal_pnl_needs_twenty_prior_days_and_the_multiple():
    marks = (NameMark("0700.HK", 1, 10, 20, "1"),)
    names, book_pnl, gross = _book(marks)
    quiet = collect_tickets(AS_OF, names, book_pnl, gross, {"0700.HK": (Decimal(1),) * 19})
    assert [ticket.rule for ticket in quiet] == []

    prior = (Decimal(1), Decimal(-1)) * 10
    opened = collect_tickets(AS_OF, names, book_pnl, gross, {"0700.HK": prior})
    assert [ticket.rule for ticket in opened] == ["abnormal_pnl"]
    assert opened[0].multiple == Decimal(2)
    assert opened[0].daily_pnl == Decimal(10)

    flat = collect_tickets(
        AS_OF,
        names,
        book_pnl,
        gross,
        {"0700.HK": prior},
        multiple=Decimal(1000),
    )
    assert flat == ()


def test_no_tickets_means_the_drafter_is_not_called():
    drafter = _CountingDrafter()
    assert apply_drafts((), drafter) == ()
    assert drafter.calls == 0


def test_limit_draft_numbers_trace_and_advice_fails():
    ticket = collect_tickets(AS_OF, *_book(MOTHER), {})[0]
    assert draft_problems(LIMIT_DRAFT, ticket) == ()
    assert "trade advice" in draft_problems("建议买入腾讯。", ticket)


def test_missing_draft_rejects_an_invented_close():
    marks = (NameMark("0005.HK", 500, 70, None, "0.60"),)
    ticket = collect_tickets(AS_OF, *_book(marks), {})[0]
    assert draft_problems(MISSING_DRAFT, ticket) == ()
    invented = draft_problems("汇丰收盘 999。", ticket)
    assert any(problem.startswith("untraced") for problem in invented)


def _book(marks: tuple[NameMark, ...]):
    snapshot = book_snapshot(marks)
    return snapshot.names, snapshot.book_pnl, snapshot.gross_market_value


class _CountingDrafter:
    def __init__(self) -> None:
        self.calls = 0

    def draft(self, ticket) -> str:
        self.calls += 1
        return ticket.symbol
