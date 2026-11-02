"""A small threaded HTTP/1.1 server implemented directly on TCP sockets."""
from __future__ import annotations

import socket
import threading
import re
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Callable
from urllib.parse import urlsplit

from .http import HTTPError, Request, Response
from .websocket import WebSocket, handshake_response


class LimboServer:
    def __init__(self, app: Callable[[Request], Response], host: str = "127.0.0.1", port: int = 8000,
                 *, request_timeout: float = 15.0, max_header_bytes: int = 32_768,
                 max_body_bytes: int = 8 * 1024 * 1024, max_header_count: int = 100,
                 max_requests_per_connection: int = 100, backlog: int = 128,
                 websocket_handler: Callable[[Request, WebSocket], None] | None = None) -> None:
        self.app, self.host, self.port = app, host, port
        self.request_timeout, self.max_header_bytes = request_timeout, max_header_bytes
        if request_timeout <= 0 or max_header_bytes < 1024 or max_body_bytes < 0:
            raise ValueError("Invalid HTTP server limits")
        if max_header_count < 1 or max_requests_per_connection < 1 or backlog < 1:
            raise ValueError("HTTP count limits and backlog must be positive")
        self.max_body_bytes, self.backlog = max_body_bytes, backlog
        self.max_header_count = max_header_count
        self.max_requests_per_connection = max_requests_per_connection
        self.websocket_handler = websocket_handler
        self._socket: socket.socket | None = None
        self._stopping = threading.Event()
        self._threads: set[threading.Thread] = set()
        self._lock = threading.Lock()

    def serve_forever(self) -> None:
        listener = socket.socket(socket.AF_INET6 if ":" in self.host else socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if listener.family == socket.AF_INET6:
            listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        listener.bind((self.host, self.port))
        listener.listen(self.backlog)
        listener.settimeout(0.5)
        self._socket = listener
        self.port = listener.getsockname()[1]
        try:
            while not self._stopping.is_set():
                try:
                    conn, address = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stopping.is_set():
                        break
                    raise
                thread = threading.Thread(target=self._client, args=(conn, address), daemon=True)
                with self._lock:
                    self._threads.add(thread)
                thread.start()
        finally:
            listener.close()
            with self._lock:
                threads = list(self._threads)
            for thread in threads:
                thread.join(timeout=1.0)

    def shutdown(self) -> None:
        self._stopping.set()
        if self._socket:
            try:
                self._socket.close()
            except OSError:
                pass

    def _client(self, conn: socket.socket, address: tuple) -> None:
        conn.settimeout(self.request_timeout)
        stream = conn.makefile("rb")
        try:
            request_count = 0
            while not self._stopping.is_set():
                try:
                    request = self._read_request(stream, address)
                except EOFError:
                    break
                except HTTPError as exc:
                    self._write_response(conn, Response.text(exc.message, exc.status), "GET", False)
                    break
                except (OSError, TimeoutError):
                    break
                if request is None:
                    break
                request_count += 1
                if request.headers.get("upgrade", "").lower() == "websocket":
                    self._serve_websocket(conn, stream, request)
                    break
                connection_tokens = {item.strip().lower() for item in request.headers.get("connection", "").split(",")}
                keep_alive = (request.version == "HTTP/1.1" and "close" not in connection_tokens)
                if request.version == "HTTP/1.0" and "keep-alive" in connection_tokens:
                    keep_alive = True
                if request_count >= self.max_requests_per_connection:
                    keep_alive = False
                try:
                    response = self.app(request)
                    if not isinstance(response, Response):
                        response = Response.text("Internal Server Error", 500)
                except HTTPError as exc:
                    response = Response.text(exc.message, exc.status)
                except Exception:
                    response = Response.text("Internal Server Error", 500)
                try:
                    self._write_response(conn, response, request.method, keep_alive)
                except (OSError, TypeError, ValueError):
                    break
                if not keep_alive:
                    break
        finally:
            try:
                stream.close()
            except OSError:
                pass
            conn.close()
            with self._lock:
                self._threads.discard(threading.current_thread())

    def _serve_websocket(self, conn: socket.socket, stream, request: Request) -> None:
        if request.method != "GET":
            self._write_response(conn, Response.text("WebSocket requires GET", 400), request.method, False)
            return
        status, headers = (handshake_response(request.headers) if request.version == "HTTP/1.1" else (400, {}))
        if status == 101 and self.websocket_handler is None:
            status = 404
        if status != 101:
            self._write_response(conn, Response.text("WebSocket upgrade rejected", status, headers), request.method, False)
            return
        raw = "HTTP/1.1 101 Switching Protocols\r\n" + "".join(f"{key}: {value}\r\n" for key, value in headers.items()) + "\r\n"
        conn.sendall(raw.encode("ascii"))
        websocket = WebSocket(conn, reader=stream)
        try:
            self.websocket_handler(request, websocket)
        except Exception:
            try: websocket.close(1011, "Internal error")
            except (OSError, ValueError): pass
        finally:
            if not websocket.closed:
                try: websocket.close()
                except OSError: pass

    def _readline(self, stream, limit: int, *, status: int = 431) -> bytes:
        if limit < 1:
            raise HTTPError(status, "HTTP line exceeds configured limit")
        line = stream.readline(limit + 1)
        if len(line) > limit:
            raise HTTPError(status, "HTTP line exceeds configured limit")
        if line and not line.endswith(b"\r\n"):
            raise HTTPError(400, "HTTP lines must end with CRLF")
        return line

    def _read_request(self, stream, address: tuple) -> Request | None:
        # RFC 9112 request lines are bounded independently from the aggregate
        # header section. A blank line is never silently skipped.
        line = self._readline(stream, min(8192, self.max_header_bytes))
        if not line:
            return None
        try:
            raw = line[:-2]
            if any(byte < 0x21 or byte > 0x7e for byte in raw):
                raise ValueError
            parts = raw.decode("ascii").split(" ")
            if len(parts) != 3 or not all(parts):
                raise ValueError
            method, target, version = parts
            token_chars = "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
            if not method or any(char not in token_chars for char in method):
                raise ValueError
            if version not in ("HTTP/1.0", "HTTP/1.1"):
                raise ValueError
            if target == "*":
                if method != "OPTIONS":
                    raise ValueError
            elif not target.startswith("/") or target.startswith("//"):
                # Limbo deliberately accepts origin-form only; proxy absolute-
                # form and authority-form targets are not routed ambiguously.
                raise ValueError
            if "#" in target or any(ord(char) <= 0x20 or ord(char) == 0x7f for char in target):
                raise ValueError
            urlsplit(target)
        except (UnicodeDecodeError, ValueError):
            raise HTTPError(400, "Malformed request line")

        headers: dict[str, str] = {}
        total = len(line)
        count = 0
        while True:
            header = self._readline(stream, min(8192, self.max_header_bytes - total))
            if not header:
                raise HTTPError(400, "Incomplete headers")
            total += len(header)
            if total > self.max_header_bytes:
                raise HTTPError(431, "Request headers too large")
            if header == b"\r\n":
                break
            count += 1
            if count > self.max_header_count:
                raise HTTPError(431, "Too many request headers")
            if header[:1] in (b" ", b"\t") or b":" not in header:
                raise HTTPError(400, "Malformed header")
            key, value = header[:-2].split(b":", 1)
            try:
                key_text = key.decode("ascii").lower()
                value_text = value.decode("latin-1").strip(" \t")
            except UnicodeDecodeError:
                raise HTTPError(400, "Malformed header")
            if not key_text or any(c not in "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyz" for c in key_text):
                raise HTTPError(400, "Invalid header name")
            if any(ord(ch) < 0x20 and ch != "\t" or ord(ch) == 0x7f for ch in value_text):
                raise HTTPError(400, "Invalid header value")
            if key_text in headers:
                # Reject all duplicates for framing/routing-sensitive fields.
                # For other list-valued fields, preserve RFC comma combination.
                if key_text in {"content-length", "transfer-encoding", "host"}:
                    raise HTTPError(400, f"Duplicate {key_text.title()} header")
                headers[key_text] += ", " + value_text
            else:
                headers[key_text] = value_text

        if version == "HTTP/1.1" and not headers.get("host", "").strip():
            raise HTTPError(400, "Host header required")
        if "host" in headers and (not headers["host"] or any(ch.isspace() for ch in headers["host"])):
            raise HTTPError(400, "Invalid Host header")
        if "transfer-encoding" in headers and "content-length" in headers:
            raise HTTPError(400, "Conflicting body framing headers")

        transfer_encoding = headers.get("transfer-encoding", "").strip().lower()
        if transfer_encoding and transfer_encoding != "chunked":
            raise HTTPError(501, "Transfer encoding not supported")
        if transfer_encoding == "chunked":
            body = self._read_chunked(stream)
        else:
            raw_length = headers.get("content-length", "0")
            if not re.fullmatch(r"[0-9]+", raw_length):
                raise HTTPError(400, "Invalid Content-Length")
            length = int(raw_length)
            if length > self.max_body_bytes:
                raise HTTPError(413, "Request body too large")
            body = stream.read(length)
            if len(body) != length:
                raise HTTPError(400, "Incomplete request body")

        request = Request(method.upper(), target, headers, body, (address[0], address[1]))
        request.version = version
        return request

    def _read_chunked(self, stream) -> bytes:
        chunks: list[bytes] = []
        size_total = 0
        trailer_bytes = 0
        trailer_count = 0
        forbidden_trailers = {"content-length", "transfer-encoding", "host", "trailer"}
        while True:
            line = self._readline(stream, 1024, status=400)
            if not line:
                raise HTTPError(400, "Incomplete chunked body")
            size_text = line[:-2].split(b";", 1)[0]
            if not size_text or not re.fullmatch(rb"[0-9A-Fa-f]+", size_text):
                raise HTTPError(400, "Invalid chunk size")
            size = int(size_text, 16)
            if size_total + size > self.max_body_bytes:
                raise HTTPError(413, "Request body too large")
            if size == 0:
                while True:
                    trailer = self._readline(stream, min(8192, self.max_header_bytes - trailer_bytes), status=431)
                    if not trailer:
                        raise HTTPError(400, "Incomplete chunked trailers")
                    trailer_bytes += len(trailer)
                    if trailer_bytes > self.max_header_bytes:
                        raise HTTPError(431, "Trailers too large")
                    if trailer == b"\r\n":
                        return b"".join(chunks)
                    trailer_count += 1
                    if trailer_count > self.max_header_count:
                        raise HTTPError(431, "Too many trailer fields")
                    if trailer[:1] in (b" ", b"\t") or b":" not in trailer:
                        raise HTTPError(400, "Malformed trailer")
                    name, value = trailer[:-2].split(b":", 1)
                    try:
                        name_text = name.decode("ascii").lower()
                        value_text = value.decode("latin-1").strip(" \t")
                    except UnicodeDecodeError:
                        raise HTTPError(400, "Malformed trailer")
                    if not name_text or any(ch not in "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyz" for ch in name_text):
                        raise HTTPError(400, "Invalid trailer name")
                    if name_text in forbidden_trailers:
                        raise HTTPError(400, "Forbidden framing trailer")
                    if any(ord(ch) < 0x20 and ch != "\t" or ord(ch) == 0x7f for ch in value_text):
                        raise HTTPError(400, "Invalid trailer value")
            data = stream.read(size + 2)
            if len(data) != size + 2 or data[-2:] != b"\r\n":
                raise HTTPError(400, "Malformed chunk")
            chunks.append(data[:-2])
            size_total += size

    def _write_response(self, conn: socket.socket, response: Response, method: str, keep_alive: bool) -> None:
        status = response.status if 100 <= response.status <= 599 else 500
        try:
            phrase = HTTPStatus(status).phrase
        except ValueError:
            phrase = "Unknown"
        headers = {k.lower(): v for k, v in response.headers.items()}
        headers.setdefault("date", datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT"))
        headers.setdefault("server", "Limbo/0.1")
        headers.setdefault("connection", "keep-alive" if keep_alive else "close")
        stream_body = not isinstance(response.body, bytes)
        no_body = method == "HEAD" or status in (204, 205, 304) or 100 <= status < 200
        if status in (204, 304) or 100 <= status < 200:
            headers.pop("content-length", None)
            headers.pop("transfer-encoding", None)
        elif status == 205:
            headers.pop("transfer-encoding", None)
            headers["content-length"] = "0"
        chunked = stream_body and not no_body and "content-length" not in headers
        if chunked:
            headers.setdefault("transfer-encoding", "chunked")
        elif not stream_body and status not in (204, 205, 304) and not 100 <= status < 200:
            headers["content-length"] = str(len(response.body))
        for key, value in headers.items():
            if not key or any(ch in key for ch in "\r\n:") or any(ch in value for ch in "\r\n"):
                raise ValueError("Invalid response header")
        head = f"HTTP/1.1 {status} {phrase}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
        conn.sendall(head.encode("latin-1", "replace"))
        if no_body:
            return
        if not stream_body:
            if response.body:
                conn.sendall(response.body)
            return
        sent = 0
        expected = int(headers["content-length"]) if stream_body and "content-length" in headers else None
        for chunk in response.body:
            if not isinstance(chunk, bytes):
                raise TypeError("Streaming response chunks must be bytes")
            if chunk:
                sent += len(chunk)
                conn.sendall((f"{len(chunk):X}\r\n".encode("ascii") + chunk + b"\r\n") if chunked else chunk)
        if expected is not None and sent != expected:
            raise ValueError("Streaming response length does not match Content-Length")
        if chunked:
            conn.sendall(b"0\r\n\r\n")
