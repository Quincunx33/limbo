"""Composable, dependency-free HTTP middleware for Limbo."""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from collections import OrderedDict
from typing import Callable
from urllib.parse import urlsplit

from .http import Request, Response


def _origin_is_valid(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        return (parsed.scheme in {"http", "https"} and bool(parsed.netloc)
                and parsed.hostname is not None and parsed.username is None and (parsed.port is None or 1 <= parsed.port <= 65535)
                and parsed.password is None and not parsed.path and not parsed.query
                and not parsed.fragment)
    except ValueError:
        return False


def _merge_vary(response: Response, *names: str) -> None:
    response.add_vary(*names)


def cors(*, allow_origins: tuple[str, ...] | list[str] = ("*",),
         allow_methods: tuple[str, ...] | list[str] = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"),
         allow_headers: tuple[str, ...] | list[str] = (),
         expose_headers: tuple[str, ...] | list[str] = (),
         allow_credentials: bool = False, max_age: int | None = None) -> Callable:
    """Create strict CORS middleware; origins are exact matches unless '*' is used."""
    origins = tuple(allow_origins)
    methods = tuple(method.upper() for method in allow_methods)
    headers = tuple(allow_headers)
    exposed = tuple(expose_headers)
    if not origins or any(origin != "*" and not _origin_is_valid(origin) for origin in origins):
        raise ValueError("CORS origins must be '*' or valid HTTP(S) origins")
    if allow_credentials and "*" in origins:
        raise ValueError("Credentialed CORS cannot use a wildcard origin")
    if not methods or any(not re.fullmatch(r"[A-Z][A-Z0-9-]*", method) for method in methods):
        raise ValueError("Invalid CORS method")
    token = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
    if any(name != "*" and not token.fullmatch(name) for name in (*headers, *exposed)):
        raise ValueError("Invalid CORS header name")
    if max_age is not None and max_age < 0:
        raise ValueError("CORS max_age cannot be negative")

    def middleware(request: Request, next_handler: Callable) -> Response:
        origin = request.headers.get("origin")
        allowed = origin is not None and ("*" in origins or origin in origins)
        preflight = request.method == "OPTIONS" and "access-control-request-method" in request.headers
        if preflight:
            requested_method = request.headers["access-control-request-method"].upper()
            if not allowed or requested_method not in methods:
                return Response(b"", 403)
            requested_headers = [item.strip().lower() for item in
                                 request.headers.get("access-control-request-headers", "").split(",") if item.strip()]
            allowed_headers = {item.lower() for item in headers}
            if requested_headers and ("*" not in allowed_headers and
                                      any(item not in allowed_headers for item in requested_headers)):
                return Response(b"", 403)
            response = Response(b"", 204, {
                "Access-Control-Allow-Methods": ", ".join(methods),
            })
            if headers:
                response.headers["Access-Control-Allow-Headers"] = ", ".join(headers)
            if max_age is not None:
                response.headers["Access-Control-Max-Age"] = str(max_age)
        else:
            response = next_handler(request)
        if allowed:
            response.headers["Access-Control-Allow-Origin"] = "*" if "*" in origins else origin
            if allow_credentials:
                response.headers["Access-Control-Allow-Credentials"] = "true"
            if exposed:
                response.headers["Access-Control-Expose-Headers"] = ", ".join(exposed)
            if "*" not in origins:
                _merge_vary(response, "Origin")
        return response
    return middleware


def security_headers(*, content_security_policy: str | None = None,
                     strict_transport_security: str | None = None,
                     frame_options: str = "DENY",
                     referrer_policy: str = "strict-origin-when-cross-origin") -> Callable:
    """Add conservative browser security headers without overriding app choices."""
    if frame_options not in {"DENY", "SAMEORIGIN"}:
        raise ValueError("frame_options must be DENY or SAMEORIGIN")
    def middleware(request: Request, next_handler: Callable) -> Response:
        response = next_handler(request)
        defaults = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": frame_options,
            "Referrer-Policy": referrer_policy,
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        }
        if content_security_policy:
            defaults["Content-Security-Policy"] = content_security_policy
        if strict_transport_security:
            defaults["Strict-Transport-Security"] = strict_transport_security
        existing = {key.lower() for key in response.headers}
        for key, value in defaults.items():
            if key.lower() not in existing:
                response.headers[key] = value
        return response
    return middleware


def request_id(*, header: str = "X-Request-ID") -> Callable:
    """Attach a validated caller request ID or generate a UUID."""
    if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", header):
        raise ValueError("Invalid request ID header name")
    def middleware(request: Request, next_handler: Callable) -> Response:
        supplied = request.headers.get(header.lower(), "")
        identifier = supplied if len(supplied) <= 128 and re.fullmatch(r"[A-Za-z0-9._-]+", supplied) else str(uuid.uuid4())
        request.state["request_id"] = identifier
        response = next_handler(request)
        response.set_header(header, identifier)
        return response
    return middleware


def access_log(*, logger: logging.Logger | None = None,
               sink: Callable[[dict], None] | None = None) -> Callable:
    """Log one bounded structured record after each request."""
    target = logger or logging.getLogger("limbo.access")
    def middleware(request: Request, next_handler: Callable) -> Response:
        started = time.perf_counter()
        response = None
        try:
            response = next_handler(request)
            return response
        finally:
            record = {
                "method": request.method,
                "path": request.path,
                "status": response.status if response is not None else 500,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "client": request.client[0] if request.client else None,
                "request_id": request.state.get("request_id"),
            }
            if sink is not None:
                sink(record)
            else:
                target.info("request", extra={"limbo": record})
    return middleware


def rate_limit(*, capacity: int = 60, refill_rate: float = 1.0,
               key: Callable[[Request], str] | None = None,
               max_keys: int = 10_000) -> Callable:
    """Create a process-local token bucket limiter with bounded key storage."""
    if capacity < 1 or refill_rate <= 0 or max_keys < 1:
        raise ValueError("Invalid rate limit configuration")
    buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
    lock = threading.Lock()
    identity = key or (lambda request: request.client[0] if request.client else "unknown")
    def middleware(request: Request, next_handler: Callable) -> Response:
        now = time.monotonic()
        bucket_key = str(identity(request))[:256]
        with lock:
            tokens, updated = buckets.get(bucket_key, (float(capacity), now))
            tokens = min(float(capacity), tokens + max(0.0, now - updated) * refill_rate)
            if tokens < 1.0:
                retry = max(1, int((1.0 - tokens) / refill_rate + 0.999))
                buckets[bucket_key] = (tokens, now)
                buckets.move_to_end(bucket_key)
                response = Response.text("Too Many Requests", 429, {"Retry-After": str(retry)})
                return response
            buckets[bucket_key] = (tokens - 1.0, now)
            buckets.move_to_end(bucket_key)
            while len(buckets) > max_keys:
                buckets.popitem(last=False)
        return next_handler(request)
    return middleware



def gzip_compress(*, minimum_size: int = 500, level: int = 6) -> Callable:
    """Negotiate gzip for compressible responses using the standard library."""
    import zlib
    if minimum_size < 0 or not 0 <= level <= 9:
        raise ValueError("Invalid gzip compression configuration")

    def gzip_quality(header: str) -> float:
        wildcard = None
        explicit = None
        for item in header.split(","):
            pieces = [part.strip() for part in item.split(";")]
            coding = pieces[0].lower()
            quality = 1.0
            for parameter in pieces[1:]:
                if parameter.lower().startswith("q="):
                    try: quality = float(parameter[2:])
                    except ValueError: quality = 0.0
            if not 0.0 <= quality <= 1.0: quality = 0.0
            if coding == "gzip": explicit = quality
            elif coding == "*": wildcard = quality
        return explicit if explicit is not None else (wildcard or 0.0)

    def middleware(request: Request, next_handler: Callable) -> Response:
        response = next_handler(request)
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        compressible = (content_type.startswith("text/") or content_type in {
            "application/json", "application/javascript", "application/xml",
            "application/xhtml+xml", "image/svg+xml", "application/manifest+json",
        })
        _merge_vary(response, "Accept-Encoding")
        if (not compressible or request.method == "HEAD" or response.status < 200
                or response.status in {204, 205, 206, 304}
                or "Content-Encoding" in response.headers
                or "Content-Range" in response.headers
                or "no-transform" in response.headers.get("Cache-Control", "").lower()
                or gzip_quality(request.headers.get("accept-encoding", "")) <= 0):
            return response
        if isinstance(response.body, bytes):
            if len(response.body) < minimum_size:
                return response
            compressor = zlib.compressobj(level, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
            response.body = compressor.compress(response.body) + compressor.flush()
        else:
            def compressed_chunks():
                compressor = zlib.compressobj(level, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
                for chunk in response.body:
                    if not isinstance(chunk, bytes):
                        raise TypeError("Streaming response chunks must be bytes")
                    output = compressor.compress(chunk)
                    if output:
                        yield output
                tail = compressor.flush()
                if tail:
                    yield tail
            response.body = compressed_chunks()
        response.headers["Content-Encoding"] = "gzip"
        for key in tuple(response.headers):
            if key.lower() == "content-length":
                del response.headers[key]
        return response
    return middleware
