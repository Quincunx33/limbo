"""Public Limbo application API."""
from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlsplit

from .http import HTTPError, Request, Response
from .frontend import FrontendDevServer
from .router import RouteGroup, Router, compile_path
from .server import LimboServer
from .templates import TemplateEngine
from .tasks import BackgroundTasks


class Limbo:
    def __init__(self, *, name: str = "Limbo", max_body_bytes: int = 8 * 1024 * 1024,
                 max_upload_file_bytes: int = 8 * 1024 * 1024,
                 max_upload_files: int = 20, max_form_fields: int = 100) -> None:
        if not isinstance(max_body_bytes, int) or max_body_bytes < 0:
            raise ValueError("max_body_bytes must be a non-negative integer")
        if not isinstance(max_upload_file_bytes, int) or max_upload_file_bytes < 1:
            raise ValueError("max_upload_file_bytes must be positive")
        if not isinstance(max_upload_files, int) or max_upload_files < 1:
            raise ValueError("max_upload_files must be positive")
        if not isinstance(max_form_fields, int) or max_form_fields < 1:
            raise ValueError("max_form_fields must be positive")
        self.name = name
        self.router = Router()
        self.middlewares: list[Callable] = []
        self.static_mounts: list[tuple[str, Path, str | None, str, str, str | None]] = []
        self.websocket_routes: list[tuple[object, Callable, tuple[str, ...] | None, int, float | None]] = []
        self.templates = TemplateEngine()
        self.frontend_servers: list[FrontendDevServer] = []
        self.max_body_bytes = max_body_bytes
        self.upload_limits = {"max_body_bytes": max_body_bytes, "max_file_bytes": max_upload_file_bytes,
                              "max_files": max_upload_files, "max_fields": max_form_fields}
        self.exception_handlers: dict[object, Callable] = {}
        self.background_tasks = BackgroundTasks()
        self.startup_hooks: list[Callable] = []
        self.shutdown_hooks: list[Callable] = []
        self._server: LimboServer | None = None

    def on_startup(self, function: Callable | None = None):
        """Register a synchronous hook called immediately before serving."""
        def register(fn):
            if not callable(fn):
                raise TypeError("Startup hook must be callable")
            self.startup_hooks.append(fn)
            return fn
        return register(function) if function else register

    def on_shutdown(self, function: Callable | None = None):
        """Register a synchronous hook called in reverse order during shutdown."""
        def register(fn):
            if not callable(fn):
                raise TypeError("Shutdown hook must be callable")
            self.shutdown_hooks.append(fn)
            return fn
        return register(function) if function else register

    def shutdown(self) -> None:
        """Request server shutdown; run() performs orderly hook/task cleanup."""
        if self._server is not None:
            self._server.shutdown()

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

    def websocket(self, path: str, *, origins: tuple[str, ...] | list[str] | None = None,
                  max_message_bytes: int = 8 * 1024 * 1024,
                  idle_timeout: float | None = 60.0):
        """Register a WebSocket route with explicit Origin, message and idle policies."""
        if not isinstance(max_message_bytes, int) or max_message_bytes < 1:
            raise ValueError("max_message_bytes must be a positive integer")
        if idle_timeout is not None and (not isinstance(idle_timeout, (int, float)) or idle_timeout <= 0):
            raise ValueError("idle_timeout must be positive or None")
        allowed_origins = None if origins is None else tuple(origins)
        if allowed_origins is not None:
            if not allowed_origins:
                raise ValueError("WebSocket Origin allowlist cannot be empty")
            for origin in allowed_origins:
                try:
                    parsed = urlsplit(origin)
                    valid = (parsed.scheme in {"http", "https"} and bool(parsed.netloc)
                             and parsed.hostname is not None and parsed.username is None
                             and parsed.password is None and not parsed.path
                             and not parsed.query and not parsed.fragment
                             and (parsed.port is None or 1 <= parsed.port <= 65535))
                except (TypeError, ValueError):
                    valid = False
                if not valid:
                    raise ValueError("WebSocket origins must be exact HTTP(S) origins")
        def decorate(handler):
            pattern = compile_path(path)
            if any(existing.pattern == pattern.pattern for existing, _, _, _, _ in self.websocket_routes):
                raise ValueError(f"Duplicate WebSocket route: {path}")
            self.websocket_routes.append((pattern, handler, allowed_origins, max_message_bytes, idle_timeout))
            return handler
        return decorate

    def websocket_origin_allowed(self, request: Request) -> bool:
        """Check a matching WebSocket route's configured Origin policy."""
        for pattern, _, origins, _, _ in self.websocket_routes:
            if pattern.fullmatch(request.path):
                return origins is None or request.headers.get("origin") in origins
        return True

    def websocket_options(self, request: Request) -> dict[str, object]:
        """Return configured transport limits for the matching route."""
        for pattern, _, _, max_message_bytes, idle_timeout in self.websocket_routes:
            if pattern.fullmatch(request.path):
                return {"max_message_bytes": max_message_bytes, "idle_timeout": idle_timeout}
        return {}

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
        for pattern, handler, _, _, _ in self.websocket_routes:
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

    def static(self, url_prefix: str, directory: str | os.PathLike[str], *,
               index: str | None = None, cache_control: str = "public, max-age=0, must-revalidate",
               symlinks: str = "deny", spa_fallback: str | None = None) -> None:
        """Mount static files with optional safe SPA navigation fallback."""
        prefix = "/" + url_prefix.strip("/")
        if prefix == "/":
            raise ValueError("Static URL prefix cannot be root")
        if symlinks not in {"deny", "within-root"}:
            raise ValueError("symlinks must be 'deny' or 'within-root'")
        if index is not None and (not index or "/" in index or "\\" in index or index in {".", ".."}):
            raise ValueError("Static index must be a single safe filename")
        if spa_fallback is not None:
            fallback_parts = spa_fallback.split("/")
            if not spa_fallback or spa_fallback.startswith("/") or "\\" in spa_fallback or any(part in {"", ".", ".."} for part in fallback_parts):
                raise ValueError("SPA fallback must be a safe relative path")
        if any(existing[0] == prefix for existing in self.static_mounts):
            raise ValueError(f"Static URL prefix already mounted: {prefix}")
        root = Path(directory).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Static directory must be a directory")
        self.static_mounts.append((prefix, root, index, cache_control, symlinks, spa_fallback))

    @staticmethod
    def html(value: str, status: int = 200, headers: dict[str, str] | None = None) -> Response:
        result = {"Content-Type": "text/html; charset=utf-8"}; result.update(headers or {})
        return Response(value, status, result)

    @staticmethod
    def json(value, status: int = 200, headers: dict[str, str] | None = None) -> Response:
        return Response.json(value, status, headers)

    @staticmethod
    def redirect(location: str, status: int = 303) -> Response:
        return Response.redirect(location, status)

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
        if len(request.body) > self.max_body_bytes:
            return Response.text("Request body too large", 413)
        request._multipart_limits = self.upload_limits
        try:
            def endpoint(req: Request) -> Response:
                if req.method == "OPTIONS" and req.path == "*":
                    methods = {route.method for route in self.router.routes}
                    methods.add("OPTIONS")
                    return Response(b"", 204, {"Allow": ", ".join(sorted(methods))})
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
                for prefix, root, index, cache_control, symlinks, spa_fallback in self.static_mounts:
                    if req.path == prefix or req.path.startswith(prefix + "/"):
                        return self._serve_static(req, prefix, root, index, cache_control, symlinks, spa_fallback)
                for frontend in self.frontend_servers:
                    if req.path == frontend.prefix or req.path.startswith(frontend.prefix + "/"):
                        return frontend.serve(req)
                if allowed:
                    response = self._error_response(HTTPError(405, "Method Not Allowed"), 405)
                    response.headers.setdefault("Allow", ", ".join(sorted(allowed | {"OPTIONS"})))
                    return response
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
        finally:
            request.close_uploads()

    @staticmethod
    def _coerce(value) -> Response:
        if isinstance(value, Response): return value
        if isinstance(value, dict) or isinstance(value, list): return Response.json(value)
        if isinstance(value, bytes): return Response(value, 200, {"Content-Type": "application/octet-stream"})
        if isinstance(value, str): return Response.text(value)
        if value is None: return Response(b"", 204)
        raise TypeError(f"Unsupported route return type: {type(value).__name__}")

    @staticmethod
    def _etag_matches(header: str, etag: str) -> bool:
        target = etag.removeprefix("W/")
        return any(item.strip() == "*" or item.strip().removeprefix("W/") == target
                   for item in header.split(","))

    def _serve_static(self, request: Request, prefix: str, root: Path,
                      index: str | None = None,
                      cache_control: str = "public, max-age=0, must-revalidate",
                      symlinks: str = "deny",
                      spa_fallback: str | None = None) -> Response:
        if request.method not in {"GET", "HEAD"}:
            return Response.text("Method Not Allowed", 405, {"Allow": "GET, HEAD, OPTIONS"})
        relative = unquote(request.path[len(prefix):].strip("/"))
        if not relative or "\\" in relative or "\x00" in relative:
            return Response.text("Not Found", 404)
        parts = relative.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            return Response.text("Not Found", 404)
        candidate = root.joinpath(*parts)
        # SPA fallback is restricted to HTML navigation and missing paths.
        if spa_fallback and request.accepts("text/html") and not candidate.exists():
            parts = spa_fallback.split("/")
            candidate = root.joinpath(*parts)
        descriptor = None
        try:
            if symlinks == "deny" and os.open in getattr(os, "supports_dir_fd", set()):
                # Open each path component relative to a trusted directory FD.
                # O_NOFOLLOW on every component prevents symlink-swap traversal.
                nofollow = getattr(os, "O_NOFOLLOW", 0)
                directory_flag = getattr(os, "O_DIRECTORY", 0)
                if not nofollow or not directory_flag:
                    raise OSError("No race-resistant no-follow directory traversal")
                root_fd = os.open(root, os.O_RDONLY | directory_flag | getattr(os, "O_BINARY", 0))
                current_fd = root_fd
                try:
                    for part in parts[:-1]:
                        next_fd = os.open(part, os.O_RDONLY | directory_flag | nofollow |
                                          getattr(os, "O_BINARY", 0), dir_fd=current_fd)
                        if current_fd != root_fd:
                            os.close(current_fd)
                        current_fd = next_fd
                    if index:
                        try:
                            directory_fd = os.open(parts[-1], os.O_RDONLY | directory_flag | nofollow |
                                                    getattr(os, "O_BINARY", 0), dir_fd=current_fd)
                        except NotADirectoryError:
                            directory_fd = None
                        if directory_fd is not None:
                            try:
                                descriptor = os.open(index, os.O_RDONLY | nofollow |
                                                     getattr(os, "O_BINARY", 0), dir_fd=directory_fd)
                            finally:
                                os.close(directory_fd)
                            candidate = candidate / index
                        else:
                            descriptor = os.open(parts[-1], os.O_RDONLY | nofollow |
                                                 getattr(os, "O_BINARY", 0), dir_fd=current_fd)
                    else:
                        descriptor = os.open(parts[-1], os.O_RDONLY | nofollow |
                                             getattr(os, "O_BINARY", 0), dir_fd=current_fd)
                finally:
                    if current_fd != root_fd:
                        os.close(current_fd)
                    os.close(root_fd)
                if index and os.path.isdir(candidate):
                    candidate = candidate / index
            else:
                # Portable fallback: containment checks and final-component
                # O_NOFOLLOW. Platforms without dir_fd traversal cannot offer
                # the same protection against intermediate-component races.
                if symlinks == "deny":
                    current = root
                    for part in parts:
                        current = current / part
                        if current.is_symlink():
                            return Response.text("Not Found", 404)
                    if candidate.is_dir() and index:
                        candidate = candidate / index
                        if candidate.is_symlink():
                            return Response.text("Not Found", 404)
                else:
                    candidate = candidate.resolve(strict=True)
                    candidate.relative_to(root)
                    if candidate.is_dir() and index:
                        candidate = (candidate / index).resolve(strict=True)
                        candidate.relative_to(root)
                candidate.parent.resolve(strict=True).relative_to(root)
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(candidate, flags)
            file_stat = os.fstat(descriptor)
            stat_module = __import__("stat")
            if stat_module.S_ISDIR(file_stat.st_mode) and symlinks == "deny" and os.open in getattr(os, "supports_dir_fd", set()):
                if not index:
                    os.close(descriptor)
                    return Response.text("Not Found", 404)
                directory_fd = descriptor
                try:
                    descriptor = os.open(index, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
                                         getattr(os, "O_BINARY", 0), dir_fd=directory_fd)
                finally:
                    os.close(directory_fd)
                file_stat = os.fstat(descriptor)
            if not stat_module.S_ISREG(file_stat.st_mode):
                os.close(descriptor)
                return Response.text("Not Found", 404)
        except (OSError, ValueError, RuntimeError):
            if descriptor is not None:
                try: os.close(descriptor)
                except OSError: pass
            return Response.text("Not Found", 404)

        size = file_stat.st_size
        from email.utils import formatdate
        etag = f'W/"{file_stat.st_mtime_ns:x}-{size:x}-{getattr(file_stat, "st_ino", 0):x}"'
        headers = {"ETag": etag, "Last-Modified": formatdate(file_stat.st_mtime, usegmt=True),
                   "Cache-Control": cache_control,
                   "Content-Type": mimetypes.guess_type(candidate.name)[0] or "application/octet-stream",
                   "Accept-Ranges": "bytes"}

        def close_fd():
            try: os.close(descriptor)
            except OSError: pass

        inm = request.headers.get("if-none-match")
        if inm is not None:
            if self._etag_matches(inm, etag):
                close_fd()
                return Response(b"", 304, headers)
        else:
            ims = request.headers.get("if-modified-since")
            if ims:
                try:
                    from email.utils import parsedate_to_datetime
                    from datetime import timezone
                    date = parsedate_to_datetime(ims)
                    if date.tzinfo is None: date = date.replace(tzinfo=timezone.utc)
                    if int(file_stat.st_mtime) <= int(date.timestamp()):
                        close_fd()
                        return Response(b"", 304, headers)
                except (TypeError, ValueError, OverflowError):
                    pass

        range_header = request.headers.get("range")
        if_range = request.headers.get("if-range")
        if range_header and if_range:
            if if_range.startswith('"') or if_range.startswith("W/"):
                if if_range != etag or etag.startswith("W/"): range_header = None
            else:
                try:
                    from email.utils import parsedate_to_datetime
                    from datetime import timezone
                    date = parsedate_to_datetime(if_range)
                    if date.tzinfo is None: date = date.replace(tzinfo=timezone.utc)
                    if int(file_stat.st_mtime) > int(date.timestamp()): range_header = None
                except (TypeError, ValueError, OverflowError):
                    range_header = None

        start, end, status = 0, size - 1, 200
        if range_header:
            # Ignore unsupported units, malformed syntax and multi-range requests.
            # Return 416 only for a valid byte range that cannot be satisfied.
            match = __import__("re").fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if match:
                first, last = match.groups()
                if first or last:
                    try:
                        if not first:
                            suffix = int(last)
                            if suffix <= 0 or size == 0:
                                raise IndexError
                            start, end, status = max(0, size - suffix), size - 1, 206
                        else:
                            start = int(first)
                            end = int(last) if last else size - 1
                            if start >= size or end < start or size == 0:
                                raise IndexError
                            end = min(end, size - 1)
                            status = 206
                    except IndexError:
                        close_fd()
                        return Response(b"", 416, {**headers, "Content-Range": f"bytes */{size}"})
                    except (ValueError, OverflowError):
                        # Reject pathological integer fields by ignoring Range.
                        start, end, status = 0, size - 1, 200
            if status == 206:
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"

        length = max(0, end - start + 1)
        headers["Content-Length"] = str(length)
        if status == 206: os.lseek(descriptor, start, os.SEEK_SET)
        def stream_file():
            remaining = length
            try:
                while remaining:
                    chunk = os.read(descriptor, min(64 * 1024, remaining))
                    if not chunk: break
                    remaining -= len(chunk)
                    yield chunk
            finally:
                close_fd()
        if request.method == "HEAD":
            close_fd()
            return Response(b"", status, headers)
        return Response(stream_file(), status, headers)

    def run(self, host: str = "127.0.0.1", port: int = 8000, *, request_timeout: float = 15.0,
            max_header_bytes: int = 32768, trusted_hosts: tuple[str, ...] | list[str] | None = None) -> None:
        if self.background_tasks.closed:
            self.background_tasks = BackgroundTasks()
        server = LimboServer(self.dispatch, host, port, request_timeout=request_timeout,
                             max_header_bytes=max_header_bytes, max_body_bytes=self.max_body_bytes,
                             trusted_hosts=trusted_hosts, websocket_handler=self.dispatch_websocket if self.websocket_routes else None,
                             websocket_origin_check=self.websocket_origin_allowed,
                             websocket_options=self.websocket_options, upload_limits=self.upload_limits)
        self._server = server
        try:
            for hook in self.startup_hooks:
                hook()
            server.serve_forever()
        except KeyboardInterrupt:
            server.shutdown()
        finally:
            server.shutdown()
            try:
                for hook in reversed(self.shutdown_hooks):
                    hook()
            finally:
                self.background_tasks.shutdown(wait=True, cancel_pending=True)
                self._server = None
