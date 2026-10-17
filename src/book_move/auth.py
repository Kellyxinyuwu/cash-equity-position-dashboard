"""Shared desk password for the page.

One password in BOOK_PASSWORD. The cookie stores an HMAC of that password,
not the password itself. An empty password leaves the page open, so tests
and a container started without the variable still read the book.
"""

import hashlib
import hmac
import os

COOKIE_NAME = "book_session"
# A week on this laptop. Changing BOOK_PASSWORD invalidates every cookie.
SESSION_MAX_AGE = 7 * 24 * 60 * 60
_TOKEN_LABEL = b"book-move-session-v1"


def configured_password() -> str:
    """The desk password, or empty when the page is left open."""
    return os.environ.get("BOOK_PASSWORD", "").strip()


def session_token(password: str) -> str:
    """Cookie value for this password. A different password produces a different token."""
    return hmac.new(password.encode(), _TOKEN_LABEL, hashlib.sha256).hexdigest()


def read_session(cookie_header: str | None) -> str:
    """The book_session value from a Cookie header. Missing means signed out."""
    if not cookie_header:
        return ""
    for part in cookie_header.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name == COOKIE_NAME:
            return value
    return ""


def password_matches(expected: str, given: str) -> bool:
    """True only when both sides are non-empty and the same password."""
    if not expected or not given:
        return False
    return hmac.compare_digest(session_token(expected), session_token(given))


def signed_in(password: str, cookie_header: str | None) -> bool:
    """True when no password is set, or the cookie matches the current password."""
    # Step 1. No password configured: the book stays open.
    if not password:
        return True
    # Step 2. compare_digest rejects a different length. Check length first.
    presented = read_session(cookie_header)
    expected = session_token(password)
    if len(presented) != len(expected):
        return False
    return hmac.compare_digest(presented, expected)


def session_cookie(token: str, *, clear: bool = False) -> str:
    """Set-Cookie text. HttpOnly so the React page cannot read the token."""
    max_age = 0 if clear else SESSION_MAX_AGE
    value = "" if clear else token
    return (
        f"{COOKIE_NAME}={value}; HttpOnly; SameSite=Lax; Path=/; Max-Age={max_age}"
    )
