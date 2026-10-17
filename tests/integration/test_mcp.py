"""MCP reads the same SQLite book as the page. The mother-problem prices live only here."""

import ast
import json
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from book_move.mcp_server import TOOL_NAME, book_as_of, dispatch
from book_move.storage import connect, migrate, upsert_position, upsert_price
from book_move.web import page_view

FETCHED_AT = datetime(2024, 1, 4, 21, 0, tzinfo=UTC)
LIMIT_DAY = date(2024, 1, 3)
GAP_DAY = date(2024, 1, 4)


def test_mother_limit_day_matches_the_page(tmp_path):
    connection = _mother_book(tmp_path)
    page = page_view(connection, LIMIT_DAY)
    tool = book_as_of(connection, LIMIT_DAY)
    assert "model_configured" not in tool
    assert tool == {key: value for key, value in page.items() if key != "model_configured"}
    assert tool["book_pnl"] == "1000"
    assert tool["gross_market_value"] == "112000"
    names = {name["symbol"]: name for name in tool["names"]}
    assert names["0700.HK"]["daily_pnl"] == "2000"
    assert names["0700.HK"]["market_value"] == "78000"
    assert names["0700.HK"]["close"] == "390"
    assert names["0005.HK"]["daily_pnl"] == "-1000"
    assert names["0005.HK"]["market_value"] == "34000"
    weight = Decimal(names["0700.HK"]["weight"]) * 100
    assert weight.quantize(Decimal("0.1")) == Decimal("69.6")
    limits = [ticket for ticket in tool["tickets"] if ticket["rule"] == "limit_breach"]
    assert [ticket["symbol"] for ticket in limits] == ["0700.HK"]
    assert limits[0]["draft"] == ""
    assert all(ticket["rule"] != "missing_close" for ticket in tool["tickets"])


def test_missing_close_day_stays_blank_on_the_tool_and_the_page(tmp_path):
    connection = _mother_book(tmp_path)
    page = page_view(connection, GAP_DAY)
    tool = book_as_of(connection, GAP_DAY)
    assert tool == {key: value for key, value in page.items() if key != "model_configured"}
    names = {name["symbol"]: name for name in tool["names"]}
    hsbc = names["0005.HK"]
    assert hsbc["close"] is None
    assert hsbc["daily_pnl"] is None
    missing = [ticket for ticket in tool["tickets"] if ticket["rule"] == "missing_close"]
    assert [ticket["symbol"] for ticket in missing] == ["0005.HK"]
    assert missing[0]["close"] is None
    assert missing[0]["previous_close"] == "68"
    # Only Tencent has a close, so its weight is the whole book and the limit rule also hits.
    assert names["0700.HK"]["daily_pnl"] == "200"
    assert tool["book_pnl"] == "200"
    assert names["0700.HK"]["limit_breached"] is True


def test_stdio_call_returns_the_same_book_pnl(tmp_path):
    database = _mother_book(tmp_path)
    database.close()
    payload = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                }
            ),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": TOOL_NAME, "arguments": {"as_of": LIMIT_DAY.isoformat()}},
                }
            ),
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-m", "book_move", "mcp", "--db", str(tmp_path / "book.sqlite")],
        input=payload + "\n",
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    messages = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    assert [message["id"] for message in messages] == [1, 2, 3]
    tool_names = [tool["name"] for tool in messages[1]["result"]["tools"]]
    assert tool_names == [TOOL_NAME]
    result = messages[2]["result"]
    assert result["isError"] is False
    body = json.loads(result["content"][0]["text"])
    assert body["book_pnl"] == "1000"
    assert body["gross_market_value"] == "112000"


def test_a_bad_date_is_a_tool_error(tmp_path):
    connection = _mother_book(tmp_path)
    response = dispatch(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": TOOL_NAME, "arguments": {"as_of": "not-a-date"}},
        },
        connection,
    )
    assert response is not None
    assert response["result"]["isError"] is True
    assert "1000" not in response["result"]["content"][0]["text"]


def test_mcp_module_does_not_import_the_vendor_or_the_drafter():
    source = Path("src/book_move/mcp_server.py").read_text()
    tree = ast.parse(source)
    imported = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "book_move.marketdata" not in imported
    assert "book_move.agent" not in imported
    assert "write_draft" not in source


def _mother_book(tmp_path):
    connection = connect(tmp_path / "book.sqlite")
    migrate(connection)
    upsert_position(connection, "0700.HK", 200, "0.60")
    upsert_position(connection, "0005.HK", 500, "0.60")
    closes = (
        ("0700.HK", date(2024, 1, 2), 380),
        ("0700.HK", date(2024, 1, 3), 390),
        ("0700.HK", date(2024, 1, 4), 391),
        ("0005.HK", date(2024, 1, 2), 70),
        ("0005.HK", date(2024, 1, 3), 68),
    )
    for symbol, session, close in closes:
        upsert_price(connection, symbol, session, close, FETCHED_AT)
    connection.commit()
    return connection
