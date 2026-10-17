"""MCP tools for the same SQLite book the page reads.

MCP (Model Context Protocol) here means another assistant can call a tool.
This process is the tool provider. It does not choose tools, loop, or draft.
book_as_of calls page_view, so the P&L, weights, and tickets are the page's
numbers. It does not call Tiingo and it does not call a model.

Speak newline-delimited JSON-RPC on stdin/stdout. Logs, if any, go to stderr.
"""

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

from book_move import __version__
from book_move.web import page_view

# Versions this server is willing to answer. The client's version is echoed
# when it is one of these. Otherwise the server stays on the oldest one.
PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
TOOL_NAME = "book_as_of"
SERVER_NAME = "book-move"


def book_as_of(connection: sqlite3.Connection, as_of: date) -> dict[str, object]:
    """One session, as the page shows it, without the draft-button flag.

    Step 1. page_view reads SQLite and runs the same report and rules as the page.
    Step 2. Drop model_configured. That flag only tells the page whether Write
    draft can be clicked. It is not a book number, and this tool does not draft.
    """
    view = page_view(connection, as_of)
    view.pop("model_configured", None)
    return view


def dispatch(
    message: dict[str, object],
    connection: sqlite3.Connection,
) -> dict[str, object] | None:
    """Answer one JSON-RPC message. A notification (no id) gets no reply."""
    # Notifications, including notifications/initialized, must not be answered.
    if "id" not in message:
        return None
    request_id = message["id"]
    method = message.get("method")
    params = message.get("params")
    if not isinstance(params, dict):
        params = {}
    if method == "initialize":
        return _result(request_id, _initialize(params))
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": [_tool_definition()]})
    if method == "tools/call":
        return _result(request_id, _call_tool(connection, params))
    return _error(request_id, -32601, "method not found")


def serve_stdio(db_path: str) -> None:
    """Read MCP messages from stdin until the client closes it. One shared database."""
    from book_move.storage import connect, migrate

    # The page and this process open the same file. migrate only adds missing tables.
    connection = connect(Path(db_path))
    migrate(connection)
    try:
        for line in sys.stdin:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                message = json.loads(stripped)
            except json.JSONDecodeError:
                _write(_error(None, -32700, "parse error"))
                continue
            if not isinstance(message, dict):
                _write(_error(None, -32600, "invalid request"))
                continue
            try:
                response = dispatch(message, connection)
            except Exception:
                # Keep the message off stdout. A client treats any other stdout as a broken frame.
                print("mcp request failed", file=sys.stderr)
                response = _error(message.get("id"), -32603, "internal error")
            if response is not None:
                _write(response)
    finally:
        connection.close()


def _initialize(params: dict[str, object]) -> dict[str, object]:
    """Accept the client's protocol version when we know it."""
    requested = params.get("protocolVersion")
    version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": __version__},
        "instructions": (
            "Read one as-of date from the position book. "
            "P&L, weights, and tickets match the page. "
            "This server does not fetch prices and does not draft a note."
        ),
    }


def _tool_definition() -> dict[str, object]:
    """The only tool: one session date in, the page's book out."""
    return {
        "name": TOOL_NAME,
        "description": (
            "Cash equity position dashboard for one session date. "
            "Returns book P&L, gross market value, each name, and open tickets. "
            "Same numbers as the page. Does not fetch prices or write a draft."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "as_of": {
                    "type": "string",
                    "description": "Session date YYYY-MM-DD",
                }
            },
            "required": ["as_of"],
            "additionalProperties": False,
        },
    }


def _call_tool(connection: sqlite3.Connection, params: dict[str, object]) -> dict[str, object]:
    """Run book_as_of. A bad date or an unknown tool name is a tool error, not a new P&L."""
    name = params.get("name")
    if name != TOOL_NAME:
        return _tool_error(f"unknown tool: {name}")
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        return _tool_error("as_of must be YYYY-MM-DD")
    raw = arguments.get("as_of")
    try:
        as_of = date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return _tool_error("as_of must be YYYY-MM-DD")
    # json.dumps keeps Decimal text already produced by page_view. No second formula.
    payload = book_as_of(connection, as_of)
    return {
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "isError": False,
    }


def _tool_error(text: str) -> dict[str, object]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _result(request_id: object, result: dict[str, object]) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: object, code: int, message: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _write(payload: dict[str, object]) -> None:
    """One JSON-RPC message, one line, flushed. Nothing else may use stdout."""
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()
