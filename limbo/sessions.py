"""Bounded, process-local server-side sessions and cookie middleware."""
from __future__ import annotations

import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import MutableMapping
from typing import Any, Callable

from .http import Request, Response


_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{43}$")


class SessionStore:
    """Thread-safe in-memory session store.

    Sessions are process-local and disappear on restart. Use a shared persistent
    adapter for multi-process deployments; this store is intentionally not one.
    """
    def __init__(self, *, max_age: int = 3600, max_sessions: int = 10_000) -> None:
        if not isinstance(max_age, int) or not 1 <= max_age <= 31_536_000:
            raise ValueError("max_age must be between 1 second and one year")
        if not isinstance(max_sessions, int) or max_sessions < 1:
            raise ValueError("max_sessions must be positive")
        self.max_age = max_age
        self.max_sessions = max_sessions
        self._items: OrderedDict[str, tuple[dict[str, Any], float]] = OrderedDict()
        self._lock = threading.RLock()

    @staticmethod
    def valid_id(session_id: str | None) -> bool:
        return isinstance(session_id, str) and bool(_SESSION_ID.fullmatch(session_id))

    def load(self, session_id: str | None) -> dict[str, Any] | None:
        if not self.valid_id(session_id):
            return None
        now = time.monotonic()
        with self._lock:
            item = self._items.get(session_id)
            if item is None:
                return None
            data, expires = item
            if expires <= now:
                del self._items[session_id]
                return None
            self._items.move_to_end(session_id)
            return dict(data)

    def save(self, session_id: str, data: dict[str, Any]) -> None:
        if not self.valid_id(session_id):
            raise ValueError("Invalid session identifier")
        if not isinstance(data, dict):
            raise TypeError("Session data must be a dictionary")
        now = time.monotonic()
        with self._lock:
            self._purge_expired(now)
            self._items[session_id] = (dict(data), now + self.max_age)
            self._items.move_to_end(session_id)
            while len(self._items) > self.max_sessions:
                self._items.popitem(last=False)

    def delete(self, session_id: str | None) -> None:
        if not self.valid_id(session_id):
            return
        with self._lock:
            self._items.pop(session_id, None)

    def _purge_expired(self, now: float) -> None:
        for key, (_, expires) in tuple(self._items.items()):
            if expires <= now:
                self._items.pop(key, None)


class Session(MutableMapping[str, Any]):
    """Mutable request session with explicit ID rotation and destruction."""
    def __init__(self, data: dict[str, Any] | None = None, session_id: str | None = None) -> None:
        self._data = dict(data or {})
        self.session_id = session_id
        self._original = dict(self._data)
        self._rotate = False
        self._destroy = False

    @property
    def modified(self) -> bool:
        return self._rotate or self._destroy or self._data != self._original

    def regenerate(self) -> None:
        """Rotate the session identifier while retaining its current data."""
        self._rotate = True

    def destroy(self) -> None:
        """Revoke this session and clear its data."""
        self._data.clear()
        self._destroy = True

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __delitem__(self, key: str) -> None:
        del self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def session_middleware(store: SessionStore | None = None, *, cookie_name: str = "limbo_session",
                       secure: bool = True, same_site: str = "Lax", path: str = "/") -> Callable:
    """Load a server-side session and persist changes after the response.

    The cookie is HttpOnly and contains only a high-entropy opaque identifier.
    Call request.session.regenerate() after login/privilege changes and
    request.session.destroy() when logging out.
    """
    from http.cookies import SimpleCookie, CookieError

    if not isinstance(cookie_name, str) or not cookie_name or any(ch in cookie_name for ch in "\r\n;="):
        raise ValueError("Invalid session cookie name")
    if same_site not in {"Strict", "Lax", "None"}:
        raise ValueError("SameSite must be Strict, Lax or None")
    if not isinstance(path, str) or not path.startswith("/") or any(ch in path for ch in "\r\n;"):
        raise ValueError("Session cookie path must be an absolute safe path")
    if same_site == "None":
        secure = True
    backend = store or SessionStore()

    def middleware(request: Request, next_handler: Callable) -> Response:
        supplied = request.cookies.get(cookie_name)
        valid_cookie = backend.valid_id(supplied)
        loaded = backend.load(supplied) if valid_cookie else None
        session = Session(loaded, supplied if loaded is not None else None)
        request.session = session
        response = next_handler(request)

        if session._destroy:
            backend.delete(session.session_id)
            if supplied:
                response.set_cookie(cookie_name, "", max_age=0, path=path,
                                    secure=secure, httponly=True, samesite=same_site)
            return response

        if session.modified:
            old_id = session.session_id
            if session._rotate and old_id:
                backend.delete(old_id)
                old_id = None
            if session._data:
                new_id = old_id or secrets.token_urlsafe(32)
                backend.save(new_id, session._data)
                session.session_id = new_id
                response.set_cookie(cookie_name, new_id, max_age=backend.max_age, path=path,
                                    secure=secure, httponly=True, samesite=same_site)
            elif supplied:
                backend.delete(old_id or supplied)
                response.set_cookie(cookie_name, "", max_age=0, path=path,
                                    secure=secure, httponly=True, samesite=same_site)
        elif supplied and (not valid_cookie or loaded is None):
            response.set_cookie(cookie_name, "", max_age=0, path=path,
                                secure=secure, httponly=True, samesite=same_site)
        return response

    return middleware
