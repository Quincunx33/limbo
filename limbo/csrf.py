"""Signed double-submit CSRF protection for browser applications."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from typing import Callable

from .http import Request, Response


_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def csrf_protect(secret: bytes | str, *, cookie_name: str = "limbo_csrf",
                 header_name: str = "x-csrf-token", form_field: str = "_csrf",
                 secure: bool = True, same_site: str = "Lax", path: str = "/") -> Callable:
    """Protect browser requests with a signed double-submit token.

    Safe requests receive a readable CSRF cookie and request.csrf_token for
    server-rendered forms. Unsafe requests must echo the token in the configured
    header or URL-encoded form field. Multipart clients should use the header.
    """
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError("CSRF secret must be at least 32 bytes")
    if not isinstance(cookie_name, str) or not cookie_name or any(ch in cookie_name for ch in "\r\n;="):
        raise ValueError("Invalid CSRF cookie name")
    if not isinstance(header_name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", header_name):
        raise ValueError("Invalid CSRF header name")
    if not isinstance(form_field, str) or not form_field or len(form_field) > 128:
        raise ValueError("Invalid CSRF form field")
    if same_site not in {"Strict", "Lax", "None"}:
        raise ValueError("SameSite must be Strict, Lax or None")
    if not isinstance(path, str) or not path.startswith("/") or any(ch in path for ch in "\r\n;"):
        raise ValueError("CSRF cookie path must be an absolute safe path")
    if same_site == "None":
        secure = True

    header_key = header_name.lower()

    def signature(token: str) -> str:
        return _b64(hmac.new(key, token.encode("ascii"), hashlib.sha256).digest())

    def read_cookie(value: str | None) -> str | None:
        if not isinstance(value, str) or value.count(".") != 1:
            return None
        token, supplied = value.split(".", 1)
        if not _TOKEN.fullmatch(token) or not _TOKEN.fullmatch(supplied):
            return None
        if not hmac.compare_digest(signature(token), supplied):
            return None
        return token

    def middleware(request: Request, next_handler: Callable) -> Response:
        token = read_cookie(request.cookies.get(cookie_name))
        if request.method.upper() not in _SAFE_METHODS:
            supplied = request.headers.get(header_key)
            if not supplied and request.headers.get("content-type", "").lower().startswith(
                "application/x-www-form-urlencoded"
            ):
                try:
                    values = request.form.get(form_field, [])
                    supplied = values[0] if values else None
                except (ValueError, UnicodeError):
                    supplied = None
            if token is None or not isinstance(supplied, str) or not hmac.compare_digest(token, supplied):
                return Response.text("CSRF validation failed", 403)
        if token is None:
            token = secrets.token_urlsafe(32)
            cookie_value = token + "." + signature(token)
            request.csrf_token = token
            response = next_handler(request)
            response.set_cookie(cookie_name, cookie_value, path=path, secure=secure,
                                httponly=False, samesite=same_site)
            return response
        request.csrf_token = token
        return next_handler(request)

    return middleware
