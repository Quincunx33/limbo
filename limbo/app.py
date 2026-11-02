"""Public Limbo application API."""
from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

from .http import HTTPError, Request, Response
from .frontend import FrontendDevServer
from .router import RouteGroup, Router, compile_path
from .server import LimboServer
from .templates import TemplateEngine


class Limbo:
    def __init__(self, *, name: str = "Limbo", max_body_bytes: int = 8 * 1024 * 1024) -> None:
        self.name = name
        self.router = Router()
        self.middlewares: list[Callable] = []
        self.static_mounts: list[tuple[str, Path]] = []
        self.websocket_routes: list[tuple[object, Callable]] = []
        self.templates = TemplateEngine()
        self.frontend_servers: list[FrontendDevServer] = []
        self.max_body_bytes = max_body_bytes
        self.exception_handlers: dict[object, Callable] = {}

    @property
    def routes(self) -> list[dict[str, object]]:
        """Return a snapshot of registered HTTP route metadata."""
        return self.router.describe()

    def url_for(self, name: str, **parameters: object) -> str:
        """Build a URL from a named route and its path parameters."""
        return self.router.url_for(name, **parameters)

    def group(self, prefix: str = "", *, name: str | None = None) -> RouteGroup:
        """Create a route group that shares this application's router."""
        return RouteGroup(self.router, prefix=prefix, name=name)

    def route(self, path: str, methods: tuple[str, ...] = ("GET",), *,
              name: str | None = None, priority: int = 0, middleware: tuple[Callable, ...] = ()):
        def decorate(handler):
            for method in methods:
                self.router.add(method, path, handler, name=name, priority=priority, middleware=middleware)
            return handler
        return decorate

    def get(self, path: str, **kwargs): return self.route(path, ("GET",), **kwargs)
    def post(self, path: str, **kwargs): return self.route(path, ("POST",), **kwargs)
    def put(self, path: str, **kwargs): return self.route(path, ("PUT",), **kwargs)
    def patch(self, path: str, **kwargs): return self.route(path, ("PATCH",), **kwargs)
    def delete(self, path: str, **kwargs): return self.route(path, ("DELETE",), **kwargs)

    def websocket(self, path: str):
        def decorate(handler):
            pattern = compile_path(path)
            if any(existing.pattern == pattern.pattern for existing, _ in self.websocket_routes):
                raise ValueError(f"Duplicate WebSocket route: {path}")
            self.websocket_routes.append((pattern, handler))
            return handler
        return decorate

    def render_template(self, template_name: str, **context) -> Response:
        return self.html(self.templates.render(template_name, context))

    def template_directory(self, directory: str | os.PathLike[str]) -> None:
        self.templates = TemplateEngine(directory)

    def enable_frontend(self, directory: str | os.PathLike[str], *, url_prefix: str = "/__limbo") -> FrontendDevServer:
        frontend = FrontendDevServer(directory, url_prefix=url_prefix)
        if any(existing.prefix == frontend.prefix for existing in self.frontend_servers):
            raise ValueError(f"Frontend URL prefix already mounted: {frontend.prefix}")
        self.frontend_servers.append(frontend)
        return frontend

    def frontend_script(self, entry: str, *, mount: int = 0) -> str:
        try:
            frontend = self.frontend_servers[mount]
        except IndexError as exc:
            raise ValueError("Enable a frontend directory before requesting its script tag") from exc
        return frontend.script_tag(entry)

    def dispatch_websocket(self, request: Request, websocket) -> None:
        for pattern, handler in self.websocket_routes:
            match = pattern.fullmatch(request.path)
            if match:
                request.path_params = match.groupdict()
                handler(request, websocket)
                return
        websocket.close(1008, "Route not found")

    def exception_handler(self, exception_or_status):
        """Register an error handler for an HTTP status or exception class."""
        if not (isinstance(exception_or_status, int) and 400 <= exception_or_status <= 599) and not (
            isinstance(exception_or_status, type) and issubclass(exception_or_status, Exception)
        ):
            raise TypeError("Use an HTTP error status or Exception subclass")
        def register(handler):
            self.exception_handlers[exception_or_status] = handler
            return handler
        return register

    def _error_response(self, error: Exception, status: int) -> Response:
        handler = self.exception_handlers.get(status)
        if handler is None:
            for cls in type(error).__mro__:
                if cls in self.exception_handlers:
                    handler = self.exception_handlers[cls]
                    break
        if handler is None:
            return Response.text(str(error) if isinstance(error, HTTPError) else "Internal Server Error", status)
        try:
            return self._coerce(handler(error))
        except Exception:
            return Response.text("Internal Server Error", 500)
    def middleware(self, function: Callable | None = None):
        def register(fn):
            self.middlewares.append(fn)
            return fn
        return register(function) if function else register

    def static(self, url_prefix: str, directory: str | os.PathLike[str]) -> None:
        prefix = "/" + url_prefix.strip("/")
        if prefix == "/":
            raise ValueError("Static URL prefix cannot be root")
        root = Path(directory).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Static directory must be a directory")
        self.static_mounts.append((prefix, root))

    @staticmethod
    def html(value: str, status: int = 200, headers: dict[str, str] | None = None) -> Response:
        result = {"Content-Type": "text/html; charset=utf-8"}; result.update(headers or {})
        return Response(value, status, result)

    @staticmethod
    def json(value, status: int = 200, headers: dict[str, str] | None = None) -> Response:
        return Response.json(value, status, headers)

    @staticmethod
    def redirect(location: str, status: int = 303) -> Response:
        if "\r" in location or "\n" in location:
            raise ValueError("Invalid redirect location")
        return Response(b"", status, {"Location": location})

    @staticmethod
    def send_file(path: str | os.PathLike[str], *, download_name: str | None = None) -> Response:
        file_path = Path(path).resolve(strict=True)
        if not file_path.is_file():
            raise FileNotFoundError(path)
        size = file_path.stat().st_size
        def chunks():
            with file_path.open("rb") as file:
                while True:
                    chunk = file.read(64 * 1024)
                    if not chunk: break
                    yield chunk
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        headers = {"Content-Type": content_type, "Content-Length": str(size)}
        if download_name:
            safe = download_name.replace('"', "")
            headers["Content-Disposition"] = f'attachment; filename="{safe}"'
        return Response(chunks(), 200, headers)

    def dispatch(self, request: Request) -> Response:
        try:
            def endpoint(req: Request) -> Response:
                route, params, allowed = self.router.match(req.method, req.path)
                if route:
                    req.path_params = params
                    endpoint = lambda current: self._coerce(route.handler(current))
                    for middleware in reversed(route.middleware):
                        downstream = endpoint
                        endpoint = lambda current, mw=middleware, nxt=downstream: self._coerce(mw(current, nxt))
                    return endpoint(req)
                if req.method == "OPTIONS" and allowed:
                    return Response(b"", 204, {"Allow": ", ".join(sorted(allowed | {"OPTIONS"}))})
                for prefix, root in self.static_mounts:
                    if req.path == prefix or req.path.startswith(prefix + "/"):
                        return self._serve_static(req, prefix, root)
                for frontend in self.frontend_servers:
                    if req.path == frontend.prefix or req.path.startswith(frontend.prefix + "/"):
                        return frontend.serve(req)
                if allowed:
                    return self._error_response(HTTPError(405, "Method Not Allowed"), 405)
                return self._error_response(HTTPError(404, "Not Found"), 404)

            next_handler = endpoint
            for middleware in reversed(self.middlewares):
                downstream = next_handler
                next_handler = lambda req, mw=middleware, nxt=downstream: self._coerce(mw(req, nxt))
            return next_handler(request)
        except HTTPError as exc:
            return self._error_response(exc, exc.status)
        except Exception as exc:
            return self._error_response(exc, 500)

    @staticmethod
    def _coerce(value) -> Response:
        if isinstance(value, Response): return value
        if isinstance(value, dict) or isinstance(value, list): return Response.json(value)
        if isinstance(value, bytes): return Response(value, 200, {"Content-Type": "application/octet-stream"})
        if isinstance(value, str): return Response.text(value)
        if value is None: return Response(b"", 204)
        raise TypeError(f"Unsupported route return type: {type(value).__name__}")

    def _serve_static(self, request: Request, prefix: str, root: Path) -> Response:
        relative = unquote(request.path[len(prefix):]).lstrip("/")
        if not relative or "\\" in relative or "\x00" in relative:
            return Response.text("Not Found", 404)
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return Response.text("Not Found", 404)
        if not candidate.is_file():
            return Response.text("Not Found", 404)
        stat = candidate.stat()
        etag = '"' + hashlib.sha256(f"{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()[:24] + '"'
        headers = {"ETag": etag, "Last-Modified": __import__("email.utils", fromlist=["formatdate"]).formatdate(stat.st_mtime, usegmt=True),
                   "Cache-Control": "public, max-age=0, must-revalidate",
                   "Content-Type": mimetypes.guess_type(candidate.name)[0] or "application/octet-stream",
                   "Accept-Ranges": "bytes"}
        if request.headers.get("if-none-match") == etag:
            return Response(b"", 304, headers)
        size = stat.st_size
        range_header = request.headers.get("range")
        if range_header:
            if not range_header.startswith("bytes=") or "," in range_header:
                return Response(b"", 416, {**headers, "Content-Range": f"bytes */{size}"})
            try:
                start_s, end_s = range_header[6:].split("-", 1)
                if start_s:
                    start = int(start_s); end = int(end_s) if end_s else size - 1
                else:
                    suffix = int(end_s); start, end = max(0, size - suffix), size - 1
                if start < 0 or end < start or start >= size:
                    raise ValueError
                end = min(end, size - 1)
            except ValueError:
                return Response(b"", 416, {**headers, "Content-Range": f"bytes */{size}"})
            with candidate.open("rb") as file:
                file.seek(start); data = file.read(end - start + 1)
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            return Response(data, 206, headers)
        return Response(candidate.read_bytes(), 200, headers)

    def run(self, host: str = "127.0.0.1", port: int = 8000, *, request_timeout: float = 15.0,
            max_header_bytes: int = 32768) -> None:
        server = LimboServer(self.dispatch, host, port, request_timeout=request_timeout,
                             max_header_bytes=max_header_bytes, max_body_bytes=self.max_body_bytes,
                             websocket_handler=self.dispatch_websocket if self.websocket_routes else None)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.shutdown()
