"""A small threaded HTTP/1.1 server implemented directly on TCP sockets."""
from __future__ import annotations

import socket
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Callable
from urllib.parse import urlsplit

from .http import HTTPError, Request, Response
from .websocket import WebSocket, handshake_response


class LimboServer:
    def __init__(self, app: Callable[[Request], Response], host: str = "127.0.0.1", port: int = 8000,
                 *, request_timeout: float = 15.0, max_header_bytes: int = 32_768,
                 max_body_bytes: int = 8 * 1024 * 1024, backlog: int = 128,
                 websocket_handler: Callable[[Request, WebSocket], None] | None = None) -> None:
        self.app, self.host, self.port = app, host, port
        self.request_timeout, self.max_header_bytes = request_timeout, max_header_bytes
        self.max_body_bytes, self.backlog = max_body_bytes, backlog
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
                if request.headers.get("upgrade", "").lower() == "websocket":
                    self._serve_websocket(conn, stream, request)
                    break
                keep_alive = request.version == "HTTP/1.1" and request.headers.get("connection", "").lower() != "close"
                if request.headers.get("connection", "").lower() == "keep-alive":
                    keep_alive = True
                try:
                    response = self.app(request)
                    if not isinstance(response, Response):
                        response = Response.text("Internal Server Error", 500)
                except HTTPError as exc:
                    response = Response.text(exc.message, exc.status)
                except Exception:
                    response = Response.text("Internal Server Error", 500)
                self._write_response(conn, response, request.method, keep_alive)
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

    def _readline(self, stream, limit: int) -> bytes:
        line = stream.readline(limit + 1)
        if len(line) > limit:
            raise HTTPError(431, "Request headers too large")
        return line

    def _read_request(self, stream, address: tuple) -> Request | None:
        line = self._readline(stream, self.max_header_bytes)
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            raise HTTPError(400, "Unexpected blank request line")
        try:
            parts = line.decode("ascii").rstrip("\r\n").split(" ")
            if len(parts) != 3 or parts[2] not in ("HTTP/1.0", "HTTP/1.1"):
                raise ValueError
            method, target, version = parts
            if not method.isalpha() or not target.startswith("/"):
                raise ValueError
        except (UnicodeDecodeError, ValueError):
            raise HTTPError(400, "Malformed request line")
        headers: dict[str, str] = {}
        total = len(line)
        while True:
            header = self._readline(stream, self.max_header_bytes - total)
            total += len(header)
            if total > self.max_header_bytes:
                raise HTTPError(431, "Request headers too large")
            if header in (b"\r\n", b"\n"):
                break
            if header[:1] in (b" ", b"\t") or b":" not in header:
                raise HTTPError(400, "Malformed header")
            key, value = header.split(b":", 1)
            try:
                key_text = key.decode("ascii").strip().lower()
                value_text = value.decode("latin-1").strip()
            except UnicodeDecodeError:
                raise HTTPError(400, "Malformed header")
            if not key_text or any(c not in "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyz" for c in key_text):
                raise HTTPError(400, "Invalid header name")
            if key_text in headers:
                if key_text == "content-length":
                    raise HTTPError(400, "Duplicate Content-Length")
                headers[key_text] += ", " + value_text
            else:
                headers[key_text] = value_text
        if version == "HTTP/1.1" and "host" not in headers:
            raise HTTPError(400, "Host header required")
        if "transfer-encoding" in headers and "content-length" in headers:
            raise HTTPError(400, "Conflicting body framing headers")
        if headers.get("transfer-encoding", "").lower() not in ("", "chunked"):
            raise HTTPError(501, "Transfer encoding not supported")
        if headers.get("transfer-encoding", "").lower() == "chunked":
            body = self._read_chunked(stream)
        else:
            try:
                length = int(headers.get("content-length", "0"))
            except ValueError:
                raise HTTPError(400, "Invalid Content-Length")
            if length < 0 or length > self.max_body_bytes:
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
        while True:
            line = self._readline(stream, 128)
            try:
                size = int(line.split(b";", 1)[0].strip(), 16)
            except ValueError:
                raise HTTPError(400, "Invalid chunk size")
            if size < 0 or size_total + size > self.max_body_bytes:
                raise HTTPError(413, "Request body too large")
            if size == 0:
                # Consume trailers, bounded by the same header budget.
                consumed = 0
                while True:
                    trailer = self._readline(stream, self.max_header_bytes - consumed)
                    consumed += len(trailer)
                    if consumed > self.max_header_bytes:
                        raise HTTPError(431, "Trailers too large")
                    if trailer in (b"\r\n", b"\n"):
                        return b"".join(chunks)
                    if b":" not in trailer:
                        raise HTTPError(400, "Malformed trailer")
            data = stream.read(size + 2)
            if len(data) != size + 2 or not data.endswith(b"\r\n"):
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
        chunked = stream_body and "content-length" not in headers and status not in (204, 304) and not 100 <= status < 200
        if chunked:
            headers.setdefault("transfer-encoding", "chunked")
        elif not stream_body and status not in (204, 304) and not 100 <= status < 200:
            headers.setdefault("content-length", str(len(response.body)))
        for key, value in headers.items():
            if not key or any(ch in key for ch in "\r\n:") or any(ch in value for ch in "\r\n"):
                raise ValueError("Invalid response header")
        head = f"HTTP/1.1 {status} {phrase}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
        conn.sendall(head.encode("latin-1", "replace"))
        if method == "HEAD" or status in (204, 304) or 100 <= status < 200:
            return
        if not stream_body:
            if response.body:
                conn.sendall(response.body)
            return
        for chunk in response.body:
            if not isinstance(chunk, bytes):
                raise TypeError("Streaming response chunks must be bytes")
            if chunk:
                conn.sendall((f"{len(chunk):X}\r\n".encode("ascii") + chunk + b"\r\n") if chunked else chunk)
        if chunked:
            conn.sendall(b"0\r\n\r\n")
