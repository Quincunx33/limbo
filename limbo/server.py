"""A small threaded HTTP/1.1 server implemented directly on TCP sockets."""
from __future__ import annotations

import socket
import threading
import re
import ipaddress
import time
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Callable
from urllib.parse import urlsplit

from .http import HTTPError, Request, Response
from .websocket import WebSocket, handshake_response


class _FixedBodyReader:
    def __init__(self, server, stream, deadline, length):
        self.server, self.stream, self.deadline, self.remaining = server, stream, deadline, length

    def read(self, size=-1):
        if self.remaining <= 0:
            return b""
        amount = self.remaining if size is None or size < 0 else min(size, self.remaining)
        self.server._remaining(self.stream, self.deadline)
        data = self.stream.read(amount)
        self.remaining -= len(data)
        if not data and self.remaining:
            raise HTTPError(400, "Incomplete request body")
        return data


class _ChunkedBodyReader:
    def __init__(self, server, stream, deadline, maximum):
        self.server, self.stream, self.deadline, self.maximum = server, stream, deadline, maximum
        self.remaining = 0
        self.total = 0
        self.finished = False

    def read(self, size=-1):
        if self.finished:
            return b""
        wanted = 64 * 1024 if size is None or size < 0 else size
        output = bytearray()
        while len(output) < wanted and not self.finished:
            if self.remaining == 0:
                line = self.server._readline(self.stream, 1024, status=400, deadline=self.deadline)
                if not line:
                    raise HTTPError(400, "Incomplete chunked body")
                token = line[:-2].split(b";", 1)[0]
                if not token or not re.fullmatch(rb"[0-9A-Fa-f]+", token):
                    raise HTTPError(400, "Invalid chunk size")
                amount = int(token, 16)
                if self.total + amount > self.maximum:
                    raise HTTPError(413, "Request body too large")
                if amount == 0:
                    trailer_bytes = 0
                    trailer_count = 0
                    forbidden = {"content-length", "transfer-encoding", "host", "trailer"}
                    while True:
                        trailer = self.server._readline(
                            self.stream, min(8192, self.server.max_header_bytes - trailer_bytes),
                            status=431, deadline=self.deadline
                        )
                        if not trailer:
                            raise HTTPError(400, "Incomplete chunked trailers")
                        trailer_bytes += len(trailer)
                        if trailer_bytes > self.server.max_header_bytes:
                            raise HTTPError(431, "Trailers too large")
                        if trailer == b"\r\n":
                            self.finished = True
                            break
                        trailer_count += 1
                        if trailer_count > self.server.max_header_count:
                            raise HTTPError(431, "Too many trailer fields")
                        if trailer[:1] in (b" ", b"\t") or b":" not in trailer:
                            raise HTTPError(400, "Malformed trailer")
                        name, value = trailer[:-2].split(b":", 1)
                        try:
                            name_text = name.decode("ascii").lower()
                            value_text = value.decode("latin-1").strip(" \t")
                        except UnicodeDecodeError as exc:
                            raise HTTPError(400, "Malformed trailer") from exc
                        if not name_text or any(ch not in "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyz" for ch in name_text):
                            raise HTTPError(400, "Invalid trailer name")
                        if name_text in forbidden:
                            raise HTTPError(400, "Forbidden framing trailer")
                        if any(ord(ch) < 0x20 and ch != "\t" or ord(ch) == 0x7f for ch in value_text):
                            raise HTTPError(400, "Invalid trailer value")
                    continue
                self.remaining = amount
            take = min(wanted - len(output), self.remaining)
            self.server._remaining(self.stream, self.deadline)
            data = self.stream.read(take)
            if len(data) != take:
                raise HTTPError(400, "Incomplete chunked body")
            output.extend(data)
            self.remaining -= take
            self.total += take
            if self.remaining == 0:
                if self.stream.read(2) != b"\r\n":
                    raise HTTPError(400, "Malformed chunk")
        return bytes(output)


class LimboServer:
    def __init__(self, app: Callable[[Request], Response], host: str = "127.0.0.1", port: int = 8000,
                 *, request_timeout: float = 15.0, max_header_bytes: int = 32_768,
                 max_body_bytes: int = 8 * 1024 * 1024, max_header_count: int = 100,
                 max_requests_per_connection: int = 100, backlog: int = 128,
                 trusted_hosts: tuple[str, ...] | list[str] | None = None,
                 websocket_handler: Callable[[Request, WebSocket], None] | None = None,
                 websocket_origin_check: Callable[[Request], bool] | None = None,
                 websocket_options: Callable[[Request], dict[str, object]] | None = None,
                 upload_limits: dict[str, int] | None = None) -> None:
        self.app, self.host, self.port = app, host, port
        self.request_timeout, self.max_header_bytes = request_timeout, max_header_bytes
        if request_timeout <= 0 or max_header_bytes < 1024 or max_body_bytes < 0:
            raise ValueError("Invalid HTTP server limits")
        if max_header_count < 1 or max_requests_per_connection < 1 or backlog < 1:
            raise ValueError("HTTP count limits and backlog must be positive")
        self.max_body_bytes, self.backlog = max_body_bytes, backlog
        self.max_header_count = max_header_count
        self.max_requests_per_connection = max_requests_per_connection
        self.trusted_hosts = {item.lower() for item in trusted_hosts} if trusted_hosts is not None else None
        self.websocket_handler = websocket_handler
        self.websocket_origin_check = websocket_origin_check
        self.websocket_options = websocket_options
        self.upload_limits = dict(upload_limits or {"max_body_bytes": max_body_bytes, "max_files": 20, "max_fields": 100, "max_file_bytes": max_body_bytes})
        self._socket: socket.socket | None = None
        self._stopping = threading.Event()
        self._threads: set[threading.Thread] = set()
        self._connections: set[socket.socket] = set()
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
                    self._connections.add(conn)
                    self._threads.add(thread)
                thread.start()
        finally:
            listener.close()
            with self._lock:
                threads = list(self._threads)
            for thread in threads:
                thread.join(timeout=1.0)

    def shutdown(self) -> None:
        """Stop accepting work and actively unblock every client connection."""
        self._stopping.set()
        listener = self._socket
        if listener:
            try:
                listener.close()
            except OSError:
                pass
        # Idle keep-alive workers otherwise remain blocked until request_timeout.
        with self._lock:
            connections = tuple(self._connections)
        for conn in connections:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass

    def _client(self, conn: socket.socket, address: tuple) -> None:
        conn.settimeout(self.request_timeout)
        stream = conn.makefile("rb")
        try:
            request_count = 0
            while not self._stopping.is_set():
                try:
                    deadline = time.monotonic() + self.request_timeout
                    request = self._read_request(stream, address, deadline=deadline)
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
                if request.version == "HTTP/1.0" and not isinstance(response.body, bytes) and "content-length" not in {k.lower() for k in response.headers} and response.status not in (204, 205, 304):
                    keep_alive = False
                try:
                    self._write_response(conn, response, request.method, keep_alive, protocol_version=request.version)
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
                self._connections.discard(conn)
                self._threads.discard(threading.current_thread())

    def _serve_websocket(self, conn: socket.socket, stream, request: Request) -> None:
        if request.method != "GET":
            self._write_response(conn, Response.text("WebSocket requires GET", 400), request.method, False)
            return
        if self.websocket_origin_check is not None:
            try:
                origin_allowed = self.websocket_origin_check(request)
            except Exception:
                origin_allowed = False
            if not origin_allowed:
                self._write_response(conn, Response.text("WebSocket Origin rejected", 403), request.method, False)
                return
        status, headers = (handshake_response(request.headers) if request.version == "HTTP/1.1" else (400, {}))
        if status == 101 and self.websocket_handler is None:
            status = 404
        if status != 101:
            self._write_response(conn, Response.text("WebSocket upgrade rejected", status, headers), request.method, False)
            return
        raw = "HTTP/1.1 101 Switching Protocols\r\n" + "".join(f"{key}: {value}\r\n" for key, value in headers.items()) + "\r\n"
        conn.sendall(raw.encode("ascii"))
        options = self.websocket_options(request) if self.websocket_options is not None else {}
        max_message_bytes = options.get("max_message_bytes", 8 * 1024 * 1024)
        idle_timeout = options.get("idle_timeout", self.request_timeout)
        conn.settimeout(None if idle_timeout is None else float(idle_timeout))
        websocket = WebSocket(conn, reader=stream, max_message_bytes=max_message_bytes)
        try:
            self.websocket_handler(request, websocket)
        except Exception:
            try: websocket.close(1011, "Internal error")
            except (OSError, ValueError): pass
        finally:
            if not websocket.closed:
                try: websocket.close()
                except OSError: pass

    def _remaining(self, stream, deadline: float | None) -> None:
        if deadline is None:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HTTPError(408, "Request deadline exceeded")
        raw = getattr(stream, "raw", None)
        sock = getattr(raw, "_sock", None)
        if sock is not None:
            sock.settimeout(remaining)

    def _readline(self, stream, limit: int, *, status: int = 431, deadline: float | None = None) -> bytes:
        self._remaining(stream, deadline)
        if limit < 1:
            raise HTTPError(status, "HTTP line exceeds configured limit")
        line = stream.readline(limit + 1)
        if len(line) > limit:
            raise HTTPError(status, "HTTP line exceeds configured limit")
        if line and not line.endswith(b"\r\n"):
            raise HTTPError(400, "HTTP lines must end with CRLF")
        return line

    def _read_request(self, stream, address: tuple, *, deadline: float | None = None) -> Request | None:
        # RFC 9112 request lines are bounded independently from the aggregate
        # header section. A blank line is never silently skipped.
        line = self._readline(stream, min(8192, self.max_header_bytes), deadline=deadline)
        if not line:
            return None
        try:
            raw = line[:-2]
            if any(byte < 0x20 or byte > 0x7e for byte in raw):
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
            header = self._readline(stream, min(8192, self.max_header_bytes - total), deadline=deadline)
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
        if "host" in headers:
            host = headers["host"]
            if not host or any(ch.isspace() for ch in host) or any(ch in host for ch in "/\\\\?#@"):
                raise HTTPError(400, "Invalid Host header")
            try:
                authority = urlsplit("//" + host)
                if not authority.hostname or authority.username is not None or authority.password is not None:
                    raise ValueError
                port = authority.port
                if port is not None and not 1 <= port <= 65535:
                    raise ValueError
                hostname = authority.hostname.rstrip(".").lower()
                try:
                    ipaddress.ip_address(hostname)
                except ValueError:
                    label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                    if not re.fullmatch(r"(?=.{1,253}$)" + label + r"(?:\." + label + r")*", hostname):
                        raise ValueError
                if self.trusted_hosts is not None and hostname not in self.trusted_hosts:
                    raise HTTPError(400, "Host not allowed")
            except HTTPError:
                raise
            except ValueError:
                raise HTTPError(400, "Invalid Host header")
        if "transfer-encoding" in headers and "content-length" in headers:
            raise HTTPError(400, "Conflicting body framing headers")

        transfer_encoding = headers.get("transfer-encoding", "").strip().lower()
        if transfer_encoding and transfer_encoding != "chunked":
            raise HTTPError(501, "Transfer encoding not supported")
        raw_length = headers.get("content-length", "0")
        if transfer_encoding != "chunked":
            if not re.fullmatch(r"[0-9]+", raw_length):
                raise HTTPError(400, "Invalid Content-Length")
            if len(raw_length) > len(str(self.max_body_bytes)):
                raise HTTPError(413, "Request body too large")
            length = int(raw_length)
            if length > self.max_body_bytes:
                raise HTTPError(413, "Request body too large")
        is_multipart = headers.get("content-type", "").lower().startswith("multipart/form-data")
        if is_multipart:
            from .uploads import UploadLimitError, parse_multipart_stream
            if transfer_encoding == "chunked":
                source = _ChunkedBodyReader(self, stream, deadline, self.max_body_bytes)
            else:
                source = _FixedBodyReader(self, stream, deadline, int(raw_length))
            try:
                parsed, tempdir = parse_multipart_stream(
                    headers["content-type"], source,
                    max_body_bytes=self.upload_limits["max_body_bytes"],
                    max_files=self.upload_limits["max_files"],
                    max_fields=self.upload_limits["max_fields"],
                    max_file_bytes=self.upload_limits["max_file_bytes"],
                )
            except UploadLimitError as exc:
                raise HTTPError(413, str(exc)) from exc
            except (ValueError, UnicodeError) as exc:
                raise HTTPError(400, "Malformed multipart body") from exc
            request = Request(method.upper(), target, headers, b"", (address[0], address[1]))
            request._multipart_cache = parsed
            request._upload_tempdir = tempdir
        else:
            if transfer_encoding == "chunked":
                body = self._read_chunked(stream, deadline=deadline)
            else:
                self._remaining(stream, deadline)
                body = stream.read(int(raw_length))
                if len(body) != int(raw_length):
                    raise HTTPError(400, "Incomplete request body")
            request = Request(method.upper(), target, headers, body, (address[0], address[1]))
        request.version = version
        return request

    def _read_chunked(self, stream, *, deadline: float | None = None) -> bytes:
        chunks: list[bytes] = []
        size_total = 0
        trailer_bytes = 0
        trailer_count = 0
        forbidden_trailers = {"content-length", "transfer-encoding", "host", "trailer"}
        while True:
            line = self._readline(stream, 1024, status=400, deadline=deadline)
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
                    trailer = self._readline(stream, min(8192, self.max_header_bytes - trailer_bytes), status=431, deadline=deadline)
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
            self._remaining(stream, deadline)
            data = stream.read(size + 2)
            if len(data) != size + 2 or data[-2:] != b"\r\n":
                raise HTTPError(400, "Malformed chunk")
            chunks.append(data[:-2])
            size_total += size

    def _write_response(self, conn: socket.socket, response: Response, method: str, keep_alive: bool, *, protocol_version: str = "HTTP/1.1") -> None:
        status = response.status if isinstance(response.status, int) and 100 <= response.status <= 599 else 500
        phrase = HTTPStatus(status).phrase
        headers: dict[str, str] = {}
        for key, value in response.headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("Response headers must be strings")
            normalized = key.lower()
            token_chars = "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyz"
            if not normalized or any(ch not in token_chars for ch in normalized):
                raise ValueError("Invalid response header name")
            if normalized in headers:
                raise ValueError("Duplicate response header")
            if any(ord(ch) < 0x20 and ch != "\t" or ord(ch) == 0x7f for ch in value):
                raise ValueError("Invalid response header value")
            headers[normalized] = value
        headers.setdefault("date", datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT"))
        headers.setdefault("server", "Limbo/0.1")
        headers["connection"] = "keep-alive" if keep_alive else "close"
        stream_body = not isinstance(response.body, bytes)
        no_body = method == "HEAD" or status in (204, 205, 304) or 100 <= status < 200
        if "content-length" in headers and "transfer-encoding" in headers:
            raise ValueError("Conflicting response framing headers")
        if "transfer-encoding" in headers and (headers["transfer-encoding"].strip().lower() != "chunked" or not stream_body or no_body or protocol_version == "HTTP/1.0"):
            raise ValueError("Unsupported response Transfer-Encoding")
        expected = None
        if "content-length" in headers:
            raw_length = headers["content-length"]
            if not re.fullmatch(r"[0-9]+", raw_length) or len(raw_length) > 20:
                raise ValueError("Invalid response Content-Length")
            expected = int(raw_length)
        if status in (204, 304) or 100 <= status < 200:
            headers.pop("content-length", None); headers.pop("transfer-encoding", None); expected = None
        elif status == 205:
            headers.pop("transfer-encoding", None); headers["content-length"] = "0"; expected = 0
        elif not stream_body:
            actual = len(response.body)
            # A HEAD response carries the representation length even though
            # its body is intentionally suppressed on the wire.
            if expected is not None and expected != actual and method != "HEAD":
                raise ValueError("Response Content-Length does not match body")
            if expected is None:
                headers["content-length"] = str(actual)
                expected = actual
        chunked = stream_body and not no_body and headers.get("transfer-encoding", "").lower() == "chunked"
        if stream_body and not no_body and expected is None and not chunked and protocol_version == "HTTP/1.1":
            headers["transfer-encoding"] = "chunked"; chunked = True
        head = f"{protocol_version} {status} {phrase}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "".join(f"set-cookie: {cookie}\r\n" for cookie in response.cookies) + "\r\n"
        conn.sendall(head.encode("latin-1", "strict"))
        if no_body: return
        if not stream_body:
            if response.body: conn.sendall(response.body)
            return
        sent = 0
        for chunk in response.body:
            if not isinstance(chunk, bytes): raise TypeError("Streaming response chunks must be bytes")
            if not chunk: continue
            sent += len(chunk)
            if expected is not None and sent > expected: raise ValueError("Streaming response exceeds Content-Length")
            conn.sendall((f"{len(chunk):X}\r\n".encode("ascii") + chunk + b"\r\n") if chunked else chunk)
        if expected is not None and sent != expected: raise ValueError("Streaming response length does not match Content-Length")
        if chunked: conn.sendall(b"0\r\n\r\n")
