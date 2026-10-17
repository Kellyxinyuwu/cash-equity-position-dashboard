"""The agent sends one ticket. A blank key and a missing ticket make no request."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

from book_move.agent import SYSTEM_PROMPT, TicketDrafter, write_draft
from book_move.rules import Ticket
from book_move.storage import connect, list_reviews, migrate, upsert_position, upsert_price

AS_OF = date(2024, 1, 3)
FETCHED = datetime(2024, 1, 3, 21, 0, tzinfo=UTC)


def test_prompt_does_not_contain_mother_problem_numbers():
    assert "2000" not in SYSTEM_PROMPT
    assert "69.6" not in SYSTEM_PROMPT
    assert "380" not in SYSTEM_PROMPT


def test_drafter_posts_only_the_ticket_json():
    opener = _Opener("腾讯权重 69.6%。本说明不包含交易建议。")
    drafter = TicketDrafter("secret", opener=opener)
    text = drafter.draft(_ticket())
    assert text.startswith("腾讯")
    sent = opener.request
    assert sent.get_header("Authorization") == "Bearer secret"
    body = json.loads(sent.data.decode())
    user = body["messages"][1]["content"]
    assert "0700.HK" in user
    assert "tiingo" not in user.lower()
    assert "2000" not in body["messages"][0]["content"]


def test_blank_key_and_a_missing_ticket_do_not_call(tmp_path):
    connection = _book(tmp_path)
    opener = _Opener("should not be sent")
    missing = write_draft(connection, AS_OF, "NOPE", "limit_breach", "secret", opener=opener)
    blank = write_draft(connection, AS_OF, "AAPL", "limit_breach", "  ", opener=opener)
    assert missing["called"] is False
    assert blank["called"] is False
    assert opener.request is None


def test_a_blank_key_is_allowed_only_for_a_local_model():
    from book_move.explain import model_configured

    docker = "http://host.docker.internal:11434/v1/chat/completions"
    assert model_configured("", docker) is True
    assert model_configured("", "http://127.0.0.1:11434/v1/chat/completions") is True
    assert model_configured("", "https://api.openai.com/v1/chat/completions") is False


def test_ollama_on_localhost_does_not_need_a_key(tmp_path):
    connection = _book(tmp_path)
    opener = _Opener("AAPL 当日没有收盘价。本说明不包含交易建议。")
    url = "http://127.0.0.1:11434/v1/chat/completions"
    stored = write_draft(
        connection,
        AS_OF,
        "MSFT",
        "missing_close",
        "",
        model="qwen2.5:0.5b",
        url=url,
        opener=opener,
    )
    assert stored["called"] is True
    assert opener.request.get_header("Authorization") is None
    assert opener.request.full_url == url


def test_a_traced_draft_is_stored_and_advice_is_not(tmp_path):
    connection = _book(tmp_path)
    opener = _Opener(
        "AAPL 权重 100.0%，限额 60.0%。持仓 10 股，收盘从 100 到 110，"
        "日盈亏 100。组合日盈亏 100。本说明不包含交易建议。"
    )
    stored = write_draft(connection, AS_OF, "AAPL", "limit_breach", "secret", opener=opener)
    connection.commit()
    assert stored["called"] is True
    assert stored["problems"] == []
    review = list_reviews(connection, AS_OF)[("AAPL", "limit_breach")]
    assert "100.0%" in review.draft

    refused = write_draft(
        connection,
        AS_OF,
        "AAPL",
        "limit_breach",
        "secret",
        opener=_Opener("建议买入 AAPL。"),
    )
    connection.commit()
    assert refused["called"] is True
    assert "trade advice" in refused["problems"]
    assert "建议买入" not in list_reviews(connection, AS_OF)[("AAPL", "limit_breach")].draft


def _book(tmp_path):
    connection = connect(tmp_path / "book.sqlite")
    migrate(connection)
    upsert_position(connection, "AAPL", 10, "0.60")
    upsert_position(connection, "MSFT", 10, "0.60")
    upsert_price(connection, "AAPL", date(2024, 1, 2), Decimal("100"), FETCHED)
    upsert_price(connection, "AAPL", date(2024, 1, 3), Decimal("110"), FETCHED)
    upsert_price(connection, "MSFT", date(2024, 1, 2), Decimal("50"), FETCHED)
    connection.commit()
    return connection


def _ticket() -> Ticket:
    from book_move.analytics import NameMark, book_snapshot
    from book_move.rules import collect_tickets

    marks = (
        NameMark("0700.HK", 200, 380, 390, "0.60"),
        NameMark("0005.HK", 500, 70, 68, "0.60"),
    )
    snapshot = book_snapshot(marks)
    tickets = collect_tickets(
        AS_OF,
        snapshot.names,
        snapshot.book_pnl,
        snapshot.gross_market_value,
        {},
    )
    return tickets[0]


class _Opener:
    def __init__(self, text: str) -> None:
        self.text = text
        self.request = None

    def open(self, request, timeout: float):
        assert timeout == 30.0
        self.request = request
        payload = json.dumps({"choices": [{"message": {"content": self.text}}]}).encode()
        return _Body(payload)


class _Body:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> bool:
        return False
