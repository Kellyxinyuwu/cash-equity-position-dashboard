"""The agent. One model call, and only for one ticket that a rule already opened.

The request body is that ticket's fields. There is no price tool and no call
to Tiingo. apply_drafts refuses to run when the ticket list is empty. A draft
that cites an unknown number, invents a close, or gives trade advice is not
stored. An empty MODEL_API_KEY stores nothing and makes no request.
"""

import json
import sqlite3
from datetime import date
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from book_move.explain import _is_local, apply_drafts, draft_problems, model_configured
from book_move.rules import Ticket
from book_move.storage import save_draft
from book_move.tickets import open_tickets

# No sample figures in this prompt. A number written here could leak into a draft.
SYSTEM_PROMPT = """You draft one short note in Chinese for a cash-equity position book.
You receive one JSON ticket. Use only that JSON.
Every number you write must appear in the JSON.
If close is null, say the name has 没有收盘价 and do not supply a close.
Do not recommend buying, selling, or changing the position.
End with exactly: 本说明不包含交易建议。
Do not mention data that is not in the JSON."""

DEFAULT_CHAT_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"


class DraftError(Exception):
    """The model response could not be used. The stored draft stays as it was."""


class TicketDrafter:
    """Sends one ticket to an OpenAI-compatible chat endpoint. Tests pass a fake opener."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        url: str = DEFAULT_CHAT_URL,
        timeout_seconds: float = 30.0,
        opener: Any = None,
    ) -> None:
        # Ollama on this machine does not need a key. The public OpenAI URL does.
        if not api_key.strip() and not _is_local(url):
            raise DraftError("MODEL_API_KEY is empty")
        self._api_key = api_key.strip()
        self._model = model
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._opener = opener if opener is not None else _DefaultOpener()

    def draft(self, ticket: Ticket) -> str:
        """POST the ticket JSON. The user message has no text besides that JSON."""
        # Drop status and draft so the model cannot treat an old paragraph as a source.
        fields = ticket.to_json(status="open", draft="")
        request = Request(
            self._url,
            data=json.dumps(
                {
                    "model": self._model,
                    "temperature": 0,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": json.dumps(fields, ensure_ascii=False)},
                    ],
                }
            ).encode(),
            headers=_headers(self._api_key),
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                raw = response.read().decode()
        except (TimeoutError, HTTPError, URLError) as exc:
            raise DraftError("model request failed") from exc
        return _message_text(raw)


def write_draft(
    connection: sqlite3.Connection,
    as_of: date,
    symbol: str,
    rule: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    url: str = DEFAULT_CHAT_URL,
    opener: Any = None,
) -> dict[str, object]:
    """Draft one open ticket. No ticket, or no configured model, makes no request."""
    # Step 1. The rule has to be open right now. A stale symbol is not sent anywhere.
    ticket = _find(connection, as_of, symbol, rule)
    if ticket is None:
        return {"draft": "", "problems": ["no ticket"], "called": False}
    # Step 2. OpenAI with a blank key stops here. A local Ollama URL does not need a key.
    if not model_configured(api_key, url):
        return {"draft": "", "problems": [], "called": False}
    # Step 3. One ticket goes through the same gate as a list. The list length is 1.
    drafter = TicketDrafter(api_key, model=model, url=url, opener=opener)
    text = apply_drafts((ticket,), drafter)[0]
    problems = draft_problems(text, ticket)
    if problems:
        # Keep the text so the page can show what was rejected. Do not store it.
        return {"draft": "", "problems": list(problems), "rejected": text, "called": True}
    save_draft(connection, as_of, symbol, rule, text)
    return {"draft": text, "problems": [], "called": True}


def _find(connection: sqlite3.Connection, as_of: date, symbol: str, rule: str) -> Ticket | None:
    for ticket in open_tickets(connection, as_of):
        if ticket.symbol == symbol and ticket.rule == rule:
            return ticket
    return None


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _message_text(raw: str) -> str:
    try:
        payload = json.loads(raw)
        return str(payload["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise DraftError("model response has no draft") from exc


class _DefaultOpener:
    def open(self, request: Request, timeout: float) -> Any:
        return urlopen(request, timeout=timeout)
