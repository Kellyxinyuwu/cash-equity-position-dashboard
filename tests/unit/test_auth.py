"""The page cookie is an HMAC of BOOK_PASSWORD. The password is not the cookie."""

from book_move.auth import read_session, session_cookie, session_token, signed_in


def test_a_matching_cookie_opens_the_book():
    token = session_token("desk-2026")
    header = f"other=1; book_session={token}"
    assert signed_in("desk-2026", header) is True
    assert read_session(header) == token


def test_a_wrong_password_stays_out():
    header = f"book_session={session_token('desk-2026')}"
    assert signed_in("other-password", header) is False
    assert signed_in("desk-2026", "book_session=short") is False
    assert signed_in("desk-2026", None) is False


def test_an_empty_password_leaves_the_page_open():
    assert signed_in("", None) is True


def test_clearing_the_cookie_expires_it():
    baked = session_cookie(session_token("desk-2026"))
    assert "HttpOnly" in baked
    assert "Max-Age=604800" in baked
    assert "Max-Age=0" in session_cookie("", clear=True)
