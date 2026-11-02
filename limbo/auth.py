"""Authentication and authorization middleware extension points."""
from __future__ import annotations

from typing import Any, Callable

from .http import Request, Response


def authenticate(resolve_user: Callable[[Request], Any | None], *,
                 unauthorized: Callable[[Request], Response] | None = None) -> Callable:
    """Resolve a user for each request and attach it as request.user.

    The resolver owns credential verification (for example, session lookup or
    an API token). This helper deliberately does not prescribe an identity
    provider, password database, or authentication protocol.
    """
    if not callable(resolve_user):
        raise TypeError("resolve_user must be callable")

    def middleware(request: Request, next_handler: Callable) -> Response:
        user = resolve_user(request)
        if user is None:
            return unauthorized(request) if unauthorized else Response.text("Unauthorized", 401)
        request.user = user
        return next_handler(request)
    return middleware


def authorize(check: Callable[[Request, Any], bool], *,
              forbidden: Callable[[Request], Response] | None = None) -> Callable:
    """Apply an application-defined authorization predicate to request.user."""
    if not callable(check):
        raise TypeError("authorization check must be callable")

    def middleware(request: Request, next_handler: Callable) -> Response:
        user = getattr(request, "user", None)
        if user is None:
            return Response.text("Unauthorized", 401)
        if not check(request, user):
            return forbidden(request) if forbidden else Response.text("Forbidden", 403)
        return next_handler(request)
    return middleware
