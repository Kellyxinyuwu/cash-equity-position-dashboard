"""Read-only desk screen. It reads SQLite and serves one as-of date.

Does not call Tiingo, and does not write a second P&L formula. Name P&L,
book P&L, weights, and the limit flag come from report_as_of. Volatility
uses the same published_volatility function as the tests, on closes already
stored. A session with no close stays null on /api/book. The React page in
ui/ only displays that JSON. Loading the page does not call the model.
POST /api/tickets/draft is the only path that does, and only for one open ticket.
When BOOK_PASSWORD is set, /api/book and the ticket posts require the desk cookie.
The login screen itself does not read the book.
"""

import html
import json
import os
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from book_move.agent import DraftError, write_draft
from book_move.analytics import (
    PUBLISHED_VAR_WINDOW,
    PUBLISHED_VOLATILITY_WINDOW,
    VAR_CONFIDENCE,
    BookSnapshot,
    NameSnapshot,
    published_volatility,
    simple_returns,
)
from book_move.auth import (
    configured_password,
    password_matches,
    session_cookie,
    session_token,
    signed_in,
)
from book_move.explain import model_configured
from book_move.jobs import PullError, pull_status, refresh_closes
from book_move.reporting import report_as_of
from book_move.rules import RULES
from book_move.storage import StoredPrice, list_prices, save_review
from book_move.tickets import tickets_as_of

MISSING_CLOSE = "missing close"
SHORT_VOLATILITY = f"fewer than {PUBLISHED_VOLATILITY_WINDOW} returns"
SHORT_VAR = f"fewer than {PUBLISHED_VAR_WINDOW} book P&Ls"
UI_DIST = Path("ui/dist")


def page_view(connection: sqlite3.Connection, as_of: date) -> dict[str, object]:
    """JSON for one session. Null means missing. The React page must not recompute P&L."""
    snapshot = report_as_of(connection, as_of)
    prices = list_prices(connection, as_of)
    fetched = {(row.symbol, row.session_date): row.fetched_at for row in prices}
    series = _bars_by_symbol(prices)
    var_loss = _plain(snapshot.historical_var)
    return {
        "as_of": as_of.isoformat(),
        "book_pnl": _plain(snapshot.book_pnl),
        "book_clean_pnl": _plain(snapshot.book_clean_pnl),
        "book_market_pnl": _plain(snapshot.book_market_pnl),
        "book_residual_pnl": _plain(snapshot.book_residual_pnl),
        "book_intraday_pnl": _plain(snapshot.book_intraday_pnl),
        "book_dollar_pnl": _plain(snapshot.book_dollar_pnl),
        "book_vega_pnl": _plain(snapshot.book_vega_pnl),
        "gross_market_value": _plain(snapshot.gross_market_value),
        "historical_var": None
        if var_loss is None
        else {
            "loss": var_loss,
            "confidence": _plain(VAR_CONFIDENCE),
            "window": snapshot.historical_var_window,
        },
        "volatility_window": PUBLISHED_VOLATILITY_WINDOW,
        "var_window": PUBLISHED_VAR_WINDOW,
        "benchmark": _benchmark_json(snapshot),
        "options": [option.to_json() for option in snapshot.options],
        "names": [
            _name_json(name, fetched.get((name.symbol, as_of)), series.get(name.symbol, {}))
            for name in snapshot.names
        ],
        # Rules only. Loading the page does not call a model, so every draft is empty.
        "tickets": list(tickets_as_of(connection, as_of)),
        "model_configured": model_configured(
            os.environ.get("MODEL_API_KEY", ""),
            os.environ.get("MODEL_CHAT_URL", ""),
        ),
    }


def render_page(connection: sqlite3.Connection, as_of: date) -> str:
    """HTML for one session. Positions stay on the page even when every close is missing."""
    # P&L, weight, and the limit flag come from the same function as `report`.
    snapshot = report_as_of(connection, as_of)
    # fetched_at is not on the snapshot, so read the price rows a second time.
    prices = list_prices(connection, as_of)
    fetched = {(row.symbol, row.session_date): row.fetched_at for row in prices}
    series = _bars_by_symbol(prices)
    rows = [
        _name_row(name, fetched.get((name.symbol, as_of)), series.get(name.symbol, {}))
        for name in snapshot.names
    ]
    return _document(
        as_of,
        snapshot.book_pnl,
        snapshot.book_clean_pnl,
        snapshot.book_market_pnl,
        snapshot.book_residual_pnl,
        snapshot.book_intraday_pnl,
        snapshot.book_dollar_pnl,
        snapshot.book_vega_pnl,
        snapshot.gross_market_value,
        snapshot.historical_var,
        rows,
        list(tickets_as_of(connection, as_of)),
    )


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Serve /api/book and, when ui/dist exists, the React page. No vendor calls."""
    server = make_server(db_path, host, port)
    print(f"http://{host}:{port}", flush=True)
    server.serve_forever()


def make_server(db_path: str, host: str, port: int) -> ThreadingHTTPServer:
    """The page server. Tests bind port 0 and stop the returned server."""
    from book_move.storage import connect, migrate

    database = Path(db_path)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
            parsed = urlparse(self.path)
            # The React screen asks this before it fetches the book.
            if parsed.path == "/api/session":
                self._send_session()
                return
            # Stored or not. This read does not call Tiingo.
            if parsed.path == "/api/pull":
                if not self._allowed():
                    return
                self._send_pull_status()
                return
            # The React app reads this. It is the same book as `report`, plus vol and fetched_at.
            if parsed.path == "/api/book":
                if not self._allowed():
                    return
                self._send_book(parse_qs(parsed.query))
                return
            # A built React app replaces the plain HTML. `make ui` is the dev server.
            # Scripts and styles stay public so the sign-in card can render.
            static = _read_ui(parsed.path)
            if static is not None:
                body, content_type = static
                self._send(200, body, content_type)
                return
            if not signed_in(configured_password(), self.headers.get("Cookie")):
                self._send(200, _login_document().encode(), "text/html; charset=utf-8")
                return
            self._send_plain_page(parse_qs(parsed.query))

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
            path = urlparse(self.path).path
            if path == "/api/login":
                self._login()
                return
            if path == "/api/logout":
                self._logout()
                return
            # Pull the due close only when it is not already stored.
            if path == "/api/pull":
                if not self._allowed():
                    return
                self._pull_latest()
                return
            # Writing a draft is the only request that can call the model.
            if path == "/api/tickets/draft":
                if not self._allowed():
                    return
                self._write_draft()
                return
            # Confirm or reject. This does not draft and does not call a model.
            if path != "/api/tickets":
                self._send(404, b'{"error":"not found"}', "application/json")
                return
            if not self._allowed():
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            connection = connect(database)
            migrate(connection)
            try:
                payload = json.loads(raw)
                as_of = date.fromisoformat(payload["as_of"])
                symbol = str(payload["symbol"])
                rule = str(payload["rule"])
                status = str(payload["status"])
                if rule not in RULES or status not in {"open", "confirmed", "rejected"}:
                    raise ValueError
                if not symbol:
                    raise ValueError
                save_review(connection, as_of, symbol, rule, status)
                connection.commit()
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                body = b'{"error":"bad ticket review"}'
                code = 400
            else:
                body = json.dumps({"status": status}).encode()
                code = 200
            finally:
                connection.close()
            self._send(code, body, "application/json")

        def _write_draft(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            connection = connect(database)
            migrate(connection)
            try:
                payload = json.loads(raw)
                as_of = date.fromisoformat(payload["as_of"])
                symbol = str(payload["symbol"])
                rule = str(payload["rule"])
                if rule not in RULES or not symbol:
                    raise ValueError
                result = write_draft(
                    connection,
                    as_of,
                    symbol,
                    rule,
                    os.environ.get("MODEL_API_KEY", ""),
                    model=os.environ.get("MODEL_NAME", "gpt-4o-mini"),
                    url=os.environ.get(
                        "MODEL_CHAT_URL",
                        "https://api.openai.com/v1/chat/completions",
                    ),
                )
                connection.commit()
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                body = b'{"error":"bad ticket"}'
                code = 400
            except DraftError:
                body = b'{"draft":"","problems":["model request failed"],"called":true}'
                code = 502
            else:
                body = json.dumps(result).encode()
                code = 200
            finally:
                connection.close()
            self._send(code, body, "application/json")

        def _send_pull_status(self) -> None:
            connection = connect(database)
            migrate(connection)
            try:
                body = json.dumps(pull_status(connection).to_json()).encode()
            finally:
                connection.close()
            self._send(200, body, "application/json")

        def _pull_latest(self) -> None:
            connection = connect(database)
            migrate(connection)
            try:
                state = refresh_closes(connection)
            except PullError as exc:
                body = json.dumps({"error": str(exc)}).encode()
                code = 502
            else:
                body = json.dumps(state.to_json()).encode()
                code = 200
            finally:
                connection.close()
            self._send(code, body, "application/json")

        def _allowed(self) -> bool:
            """Book reads need the desk cookie once BOOK_PASSWORD is set."""
            if signed_in(configured_password(), self.headers.get("Cookie")):
                return True
            self._send(401, b'{"error":"sign in required"}', "application/json")
            return False

        def _send_session(self) -> None:
            password = configured_password()
            opened = signed_in(password, self.headers.get("Cookie"))
            body = json.dumps({"required": bool(password), "signed_in": opened}).encode()
            self._send(200, body, "application/json")

        def _login(self) -> None:
            # Step 1. Read a short body. The password is checked and then dropped.
            try:
                raw = self._read_body()
                content_type = self.headers.get("Content-Type", "")
                password = _posted_password(raw, content_type)
            except (ValueError, json.JSONDecodeError, UnicodeError):
                self._send(400, b'{"error":"bad login"}', "application/json")
                return
            expected = configured_password()
            wants_json = "application/json" in content_type
            # Step 2. Empty config is not a login. Wrong password does not set a cookie.
            if not password_matches(expected, password):
                if wants_json:
                    self._send(401, b'{"error":"wrong password"}', "application/json")
                    return
                page = _login_document("That password does not open this book.")
                self._send(401, page.encode(), "text/html; charset=utf-8")
                return
            # Step 3. The cookie is the HMAC, not the password.
            header = ("Set-Cookie", session_cookie(session_token(expected)))
            if wants_json:
                self._send(200, b'{"signed_in":true}', "application/json", (header,))
                return
            self._redirect("/", header)

        def _logout(self) -> None:
            header = ("Set-Cookie", session_cookie("", clear=True))
            self._send(200, b'{"signed_in":false}', "application/json", (header,))

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 4096:
                raise ValueError
            return self.rfile.read(length)

        def _redirect(self, location: str, cookie: tuple[str, str]) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.send_header(*cookie)
            self.end_headers()

        def _send_book(self, query: dict[str, list[str]]) -> None:
            raw = query.get("as_of", [None])[0]
            connection = connect(database)
            migrate(connection)
            try:
                chosen = _as_of(connection, raw)
                body = json.dumps(page_view(connection, chosen)).encode()
            except ValueError:
                body = b'{"error":"as_of must be YYYY-MM-DD"}'
                status = 400
            else:
                status = 200
            finally:
                connection.close()
            self._send(status, body, "application/json")

        def _send_plain_page(self, query: dict[str, list[str]]) -> None:
            # Fallback when `ui/dist` has not been built. Same as-of rules as the API.
            raw = query.get("as_of", [None])[0]
            connection = connect(database)
            migrate(connection)
            try:
                chosen = _as_of(connection, raw)
                body = render_page(connection, chosen).encode()
            except ValueError:
                body = b"as_of must be YYYY-MM-DD"
                status = 400
            else:
                status = 200
            finally:
                connection.close()
            self._send(status, body, "text/html; charset=utf-8")

        def _send(
            self,
            status: int,
            body: bytes,
            content_type: str,
            extra: tuple[tuple[str, str], ...] = (),
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # The Vite dev server on 5173 calls this API. The data is read-only.
            self.send_header("Access-Control-Allow-Origin", "*")
            for key, value in extra:
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            # The stdlib server logs every GET. The page has nothing to add to that.
            return

    return ThreadingHTTPServer((host, port), Handler)


def _posted_password(raw: bytes, content_type: str) -> str:
    """Password from JSON or from a plain form. Anything else is refused."""
    if "application/json" in content_type:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError
        return str(payload.get("password", ""))
    if "application/x-www-form-urlencoded" in content_type:
        values = parse_qs(raw.decode(), keep_blank_values=True).get("password", [])
        return values[0] if values else ""
    raise ValueError


def _login_document(message: str = "") -> str:
    """Plain-page sign-in, used only when the React build is absent."""
    note = f"<p>{html.escape(message)}</p>" if message else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Sign in</title></head>
<body>
<h1>Sign in</h1>
{note}
<form method="post" action="/api/login">
<label>Password <input type="password" name="password" autocomplete="current-password"></label>
<button type="submit">Open the book</button>
</form>
</body>
</html>
"""


def _as_of(connection: sqlite3.Connection, raw: str | None) -> date:
    """Parse the query date, or fall back to the newest session already stored."""
    if raw:
        return date.fromisoformat(raw)
    # date.max asks list_prices for every stored session.
    prices = list_prices(connection, date.max)
    if not prices:
        return date.today()
    return max(row.session_date for row in prices)


def _name_json(
    name: NameSnapshot,
    fetched_at: datetime | None,
    series: dict[date, StoredPrice],
) -> dict[str, object]:
    """One position for the React table. Null fields are missing, not zero."""
    bars = [series[day] for day in sorted(series)]
    vol = published_volatility(
        simple_returns(
            [bar.close for bar in bars],
            [bar.split_factor for bar in bars],
            [bar.div_cash for bar in bars],
        )
    )
    return {
        "symbol": name.symbol,
        "shares": _plain(name.shares),
        "close": _plain(name.close),
        "last": _plain(name.last),
        "intraday_pnl": _plain(name.intraday_pnl),
        "dollar_pnl": _plain(name.dollar_pnl),
        "vega_pnl": _plain(name.vega_pnl),
        "fetched_at": None if fetched_at is None else fetched_at.isoformat(),
        "daily_pnl": _plain(name.daily_pnl),
        "clean_pnl": _plain(name.clean_pnl),
        "market_pnl": _plain(name.market_pnl),
        "residual_pnl": _plain(name.residual_pnl),
        "market_value": _plain(name.market_value),
        "weight": _plain(name.weight),
        "limit": _plain(name.limit),
        # A missing weight is not "inside the limit".
        "limit_breached": None if name.weight is None else name.limit_breached,
        "split_factor": _plain(name.split_factor),
        "div_cash": _plain(name.div_cash),
        "dividend_cash": _plain(name.dividend_cash),
        "volatility": None
        if vol is None
        else {
            "daily": _plain(vol.daily),
            "annualized": _plain(vol.annualized),
            "observations": vol.observations,
        },
    }


def _benchmark_json(snapshot: BookSnapshot) -> dict[str, object] | None:
    """The benchmark print, when both closes exist. Null leaves the split blank."""
    if snapshot.benchmark_simple_return is None:
        return None
    return {
        "symbol": snapshot.benchmark_symbol,
        "previous_close": _plain(snapshot.benchmark_previous),
        "close": _plain(snapshot.benchmark_close),
        "simple_return": _plain(snapshot.benchmark_simple_return),
    }


def _plain(value: Decimal | None) -> str | None:
    """Decimal text for JSON. The React page formats it and does not parse a float from SQLite."""
    if value is None:
        return None
    return format(value, "f")


def _read_ui(url_path: str) -> tuple[bytes, str] | None:
    """A file from ui/dist, or the React index for a client-side path. None if not built."""
    if not UI_DIST.is_dir():
        return None
    root = UI_DIST.resolve()
    relative = url_path.lstrip("/") or "index.html"
    if relative.startswith("api/"):
        return None
    candidate = (root / relative).resolve()
    if root not in candidate.parents and candidate != root:
        return None
    if candidate.is_file():
        return candidate.read_bytes(), _content_type(candidate)
    index = root / "index.html"
    # Asset paths that miss a file should 404 via the plain fallback, not the app shell.
    if index.is_file() and "." not in Path(relative).name:
        return index.read_bytes(), "text/html; charset=utf-8"
    return None


def _content_type(path: Path) -> str:
    return {
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".svg": "image/svg+xml",
        ".json": "application/json",
    }.get(path.suffix, "application/octet-stream")


def _name_row(
    name: NameSnapshot,
    fetched_at: datetime | None,
    series: dict[date, StoredPrice],
) -> dict[str, str]:
    """Turn one marked name into display strings. Missing numbers stay words, not 0."""
    # Volatility is computed here from stored closes. A split scales that day's close
    # before the return, so a 10-for-1 print is not a 90% crash.
    bars = [series[day] for day in sorted(series)]
    vol = published_volatility(
        simple_returns(
            [bar.close for bar in bars],
            [bar.split_factor for bar in bars],
            [bar.div_cash for bar in bars],
        )
    )
    close_missing = name.close is None
    missing = MISSING_CLOSE
    return {
        "symbol": name.symbol,
        "shares": _shares(name.shares),
        "close": missing if close_missing else _money(name.close),
        "last": "—" if name.last is None else _money(name.last),
        "intraday_pnl": "—" if name.intraday_pnl is None else _money(name.intraday_pnl),
        "dollar_pnl": "—" if name.dollar_pnl is None else _money(name.dollar_pnl),
        "vega_pnl": "—" if name.vega_pnl is None else _money(name.vega_pnl),
        "split_factor": _factor(name.split_factor),
        "dividend_cash": "—" if name.dividend_cash is None else _money(name.dividend_cash),
        "fetched_at": missing if fetched_at is None else fetched_at.isoformat(),
        "daily_pnl": missing if name.daily_pnl is None else _money(name.daily_pnl),
        "clean_pnl": missing if name.clean_pnl is None else _money(name.clean_pnl),
        "market_pnl": "—" if name.market_pnl is None else _money(name.market_pnl),
        "residual_pnl": "—" if name.residual_pnl is None else _money(name.residual_pnl),
        "market_value": missing if name.market_value is None else _money(name.market_value),
        "weight": "—" if name.weight is None else _percent(name.weight),
        "limit": _percent(name.limit),
        # A missing weight is not "inside the limit". Leave the flag blank.
        "limit_breached": "—" if name.weight is None else ("yes" if name.limit_breached else "no"),
        "volatility": SHORT_VOLATILITY if vol is None else _percent(vol.daily),
        "annualized": "—" if vol is None else _percent(vol.annualized),
    }


def _document(
    as_of: date,
    book_pnl: Decimal | None,
    book_clean: Decimal | None,
    book_market: Decimal | None,
    book_residual: Decimal | None,
    book_intraday: Decimal | None,
    book_dollar: Decimal | None,
    book_vega: Decimal | None,
    gross: Decimal | None,
    var_loss: Decimal | None,
    rows: list[dict[str, str]],
    tickets: list[dict[str, object]],
) -> str:
    """Page shell: the date form, book totals, the position table, and the tickets."""
    body = "".join(_row(row) for row in rows)
    ticket_html = _ticket_list(tickets)
    book = MISSING_CLOSE if book_pnl is None else _money(book_pnl)
    clean = MISSING_CLOSE if book_clean is None else _money(book_clean)
    market_pnl = "—" if book_market is None else _money(book_market)
    residual = "—" if book_residual is None else _money(book_residual)
    intraday = "—" if book_intraday is None else _money(book_intraday)
    dollar = "—" if book_dollar is None else _money(book_dollar)
    vega = "—" if book_vega is None else _money(book_vega)
    market = MISSING_CLOSE if gross is None else _money(gross)
    var = SHORT_VAR if var_loss is None else _money(var_loss)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Cash Equity Position Dashboard {as_of.isoformat()}</title>
<style>
body {{ font-family: sans-serif; margin: 2rem; }}
table {{ border-collapse: collapse; }}
th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: right; }}
th {{ background: #f4f4f4; }}
td:first-child, th:first-child {{ text-align: left; }}
.missing {{ color: #8a4b08; }}
</style>
</head>
<body>
<h1>Cash Equity Position Dashboard {as_of.isoformat()}</h1>
<form method="get">
<label>Session date <input name="as_of" value="{as_of.isoformat()}"></label>
<button type="submit">Show</button>
</form>
<p>Actual P&amp;L {html.escape(book)}. Clean P&amp;L {html.escape(clean)}.
Market P&amp;L {html.escape(market_pnl)}. Residual P&amp;L {html.escape(residual)}.
Intraday P&amp;L {html.escape(intraday)}.
Up 1 {html.escape(dollar)}. Vega {html.escape(vega)}.
Gross market value {html.escape(market)}.
Historical VaR loss {html.escape(var)} (95%, window {PUBLISHED_VAR_WINDOW}).</p>
<table>
<thead><tr>
<th>Symbol</th><th>Shares</th><th>Close</th><th>Last</th>
<th>Split</th><th>Dividend</th><th>Fetched at</th>
<th>Actual P&amp;L</th><th>Clean P&amp;L</th><th>Intraday P&amp;L</th>
<th>Up 1</th><th>Vega</th>
<th>Market P&amp;L</th><th>Residual P&amp;L</th>
<th>Market value</th><th>Weight</th><th>Limit</th>
<th>Limit breached</th><th>Daily vol</th><th>Annualized vol</th>
</tr></thead>
<tbody>
{body}
</tbody>
</table>
<h2>Exception tickets</h2>
{ticket_html}
</body>
</html>
"""


def _ticket_list(tickets: list[dict[str, object]]) -> str:
    if not tickets:
        return "<p>None.</p>"
    items = []
    for ticket in tickets:
        rule = html.escape(str(ticket["rule"]))
        symbol = html.escape(str(ticket["symbol"]))
        close = ticket["close"]
        close_text = "missing close" if close is None else html.escape(str(close))
        items.append(f"<li>{symbol} {rule}. Close {close_text}. Draft is empty.</li>")
    return "<ul>" + "".join(items) + "</ul>"


def _row(cells: dict[str, str]) -> str:
    keys = (
        "symbol",
        "shares",
        "close",
        "last",
        "split_factor",
        "dividend_cash",
        "fetched_at",
        "daily_pnl",
        "clean_pnl",
        "intraday_pnl",
        "dollar_pnl",
        "vega_pnl",
        "market_pnl",
        "residual_pnl",
        "market_value",
        "weight",
        "limit",
        "limit_breached",
        "volatility",
        "annualized",
    )
    rendered = []
    for key in keys:
        text = html.escape(cells[key])
        if cells[key] in {MISSING_CLOSE, SHORT_VOLATILITY}:
            text = f'<span class="missing">{text}</span>'
        rendered.append(f"<td>{text}</td>")
    return "<tr>" + "".join(rendered) + "</tr>\n"


def _bars_by_symbol(prices: tuple[StoredPrice, ...]) -> dict[str, dict[date, StoredPrice]]:
    """Closes plus the corporate action on that session, for the volatility chain."""
    by_symbol: dict[str, dict[date, StoredPrice]] = {}
    for row in prices:
        by_symbol.setdefault(row.symbol, {})[row.session_date] = row
    return by_symbol


def _factor(value: Decimal) -> str:
    """10 stays 10. A fractional split keeps its digits."""
    if value == value.to_integral_value():
        return str(int(value))
    return format(value, "f")


def _money(value: Decimal) -> str:
    """Show two decimal places. The value stored in SQLite is left unchanged."""
    return f"{value.quantize(Decimal('0.01')):,.2f}"


def _shares(value: Decimal) -> str:
    if value == value.to_integral_value():
        return f"{int(value):,}"
    return format(value, "f")


def _percent(value: Decimal) -> str:
    points = (value * 100).quantize(Decimal("0.1"))
    return f"{points}%"
