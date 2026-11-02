"""HTTP request and response primitives for Limbo."""
from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from typing import Any, Iterable
from urllib.parse import parse_qs, urlsplit


@dataclass
class Request:
    method: str
    target: str
    headers: dict[str, str]
    body: bytes = b""
    client: tuple[str, int] | None = None
    scheme: str = "http"
    path_params: dict[str, str] = field(default_factory=dict)
    version: str = "HTTP/1.1"

    def __post_init__(self) -> None:
        parsed = urlsplit(self.target)
        self.path = parsed.path or "/"
        self.query_params = parse_qs(parsed.query, keep_blank_values=True)
        self.host = self.headers.get("host", "")
        self._json_cache: Any = _UNSET
        self._form_cache: dict[str, list[str]] | None = None
        self._cookies_cache: dict[str, str] | None = None
        self._multipart_cache: dict[str, list[Any]] | None = None

    @property
    def json(self) -> Any:
        if self._json_cache is _UNSET:
            try:
                self._json_cache = _json.loads(self.body.decode("utf-8"))
            except (UnicodeDecodeError, _json.JSONDecodeError) as exc:
                raise ValueError("Request body is not valid UTF-8 JSON") from exc
        return self._json_cache

    @property
    def form(self) -> dict[str, list[str]]:
        if self._form_cache is None:
            if self.headers.get("content-type", "").lower().startswith("multipart/form-data"):
                from .uploads import UploadFile, parse_multipart
                self._multipart_cache = parse_multipart(self.headers["content-type"], self.body)
                self._form_cache = {key: [item for item in items if isinstance(item, str)]
                                    for key, items in self._multipart_cache.items()}
            else:
                self._form_cache = parse_qs(self.body.decode("utf-8", "replace"), keep_blank_values=True)
        return self._form_cache

    @property
    def files(self) -> dict[str, list[Any]]:
        if self._multipart_cache is None:
            content_type = self.headers.get("content-type", "")
            if not content_type.lower().startswith("multipart/form-data"):
                self._multipart_cache = {}
            else:
                from .uploads import parse_multipart
                self._multipart_cache = parse_multipart(content_type, self.body)
        from .uploads import UploadFile
        return {key: [item for item in items if isinstance(item, UploadFile)]
                for key, items in self._multipart_cache.items()}

    @property
    def cookies(self) -> dict[str, str]:
        if self._cookies_cache is None:
            jar = SimpleCookie()
            try:
                jar.load(self.headers.get("cookie", ""))
            except Exception:
                jar = SimpleCookie()
            self._cookies_cache = {key: morsel.value for key, morsel in jar.items()}
        return self._cookies_cache


_UNSET = object()


@dataclass
class Response:
    body: bytes | str | Iterable[bytes] = b""
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    cookies: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        normalized = {str(k): str(v) for k, v in self.headers.items()}
        self.headers = normalized
        if isinstance(self.body, str):
            self.body = self.body.encode("utf-8")

    def set_header(self, name: str, value: str) -> "Response":
        token = "!#$%&'*+-.^\u0060|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        value = str(value)
        if not name or any(char not in token for char in name):
            raise ValueError("Invalid response header name")
        if any(ord(char) < 0x20 and char != "\\t" or ord(char) == 0x7f for char in value):
            raise ValueError("Invalid response header value")
        existing = next((key for key in self.headers if key.lower() == name.lower()), None)
        if existing is not None: del self.headers[existing]
        self.headers[name] = value
        return self

    def set_cookie(self, name: str, value: str, *, max_age: int | None = None,
                   expires: str | None = None, path: str = "/", domain: str | None = None,
                   secure: bool = False, httponly: bool = True, samesite: str = "Lax") -> "Response":
        from http.cookies import SimpleCookie, CookieError
        jar = SimpleCookie()
        try:
            jar[name] = value
            morsel = jar[name]
            morsel["path"] = path
            if domain is not None: morsel["domain"] = domain
            if max_age is not None: morsel["max-age"] = str(max_age)
            if expires is not None: morsel["expires"] = expires
            if secure: morsel["secure"] = True
            if httponly: morsel["httponly"] = True
            if samesite not in {"Strict", "Lax", "None"}:
                raise ValueError("SameSite must be Strict, Lax or None")
            morsel["samesite"] = samesite
        except (CookieError, TypeError) as exc:
            raise ValueError("Invalid cookie") from exc
        self.cookies.append(morsel.OutputString())
        return self

    @classmethod
    def json(cls, value: Any, status: int = 200, headers: dict[str, str] | None = None) -> "Response":
        result = {"Content-Type": "application/json; charset=utf-8"}
        result.update(headers or {})
        return cls(_json.dumps(value, ensure_ascii=False, separators=(",", ":")), status, result)

    @classmethod
    def text(cls, value: str, status: int = 200, headers: dict[str, str] | None = None) -> "Response":
        result = {"Content-Type": "text/plain; charset=utf-8"}
        result.update(headers or {})
        return cls(value, status, result)


class HTTPError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message
