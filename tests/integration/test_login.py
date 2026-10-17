"""The page stays closed until BOOK_PASSWORD matches. The book numbers are unchanged."""

import json
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from book_move.web import make_server

PASSWORD = "desk-2026"


def test_the_book_stays_closed_until_the_password_matches(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOK_PASSWORD", PASSWORD)
    server = make_server(str(tmp_path / "book.sqlite"), "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        closed = _status(Request(f"{base}/api/book"))
        assert closed == 401
        assert _status(Request(f"{base}/api/pull")) == 401
        assert _status(Request(f"{base}/api/pull", data=b"{}", method="POST")) == 401
        assert (
            _status(Request(f"{base}/api/tickets", data=b"{}", method="POST")) == 401
        )
        session = json.loads(_body(Request(f"{base}/api/session")))
        assert session == {"required": True, "signed_in": False}

        wrong = _status(
            Request(
                f"{base}/api/login",
                data=json.dumps({"password": "nope"}).encode(),
                headers={"Content-Type": "application/json"},
            )
        )
        assert wrong == 401

        with _open(
            Request(
                f"{base}/api/login",
                data=json.dumps({"password": PASSWORD}).encode(),
                headers={"Content-Type": "application/json"},
            )
        ) as response:
            assert response.status == 200
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        assert cookie.startswith("book_session=")
        assert PASSWORD not in cookie

        book = json.loads(_body(Request(f"{base}/api/book", headers={"Cookie": cookie})))
        assert "book_pnl" in book

        with _open(
            Request(
                f"{base}/api/logout",
                data=b"{}",
                headers={"Content-Type": "application/json", "Cookie": cookie},
                method="POST",
            )
        ) as response:
            assert "Max-Age=0" in response.headers["Set-Cookie"]
        assert _status(Request(f"{base}/api/book")) == 401
    finally:
        server.shutdown()
        server.server_close()


def test_an_empty_password_leaves_the_api_open(tmp_path, monkeypatch):
    monkeypatch.delenv("BOOK_PASSWORD", raising=False)
    server = make_server(str(tmp_path / "book.sqlite"), "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        session = json.loads(_body(Request(f"{base}/api/session")))
        assert session == {"required": False, "signed_in": True}
        assert _status(Request(f"{base}/api/book")) == 200
    finally:
        server.shutdown()
        server.server_close()


def _status(request: Request) -> int:
    try:
        with _open(request) as response:
            return response.status
    except HTTPError as error:
        return error.code


def _body(request: Request) -> bytes:
    with _open(request) as response:
        return response.read()


def _open(request: Request):
    last: Exception | None = None
    for _ in range(20):
        try:
            return urlopen(request, timeout=2)
        except HTTPError:
            raise
        except URLError as error:
            last = error
            time.sleep(0.05)
    assert last is not None
    raise last
