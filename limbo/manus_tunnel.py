"""Optional native client for the Limbo Tunnel Manus protocol.

The core Limbo package remains dependency-free. Install the optional
``manus-tunnel`` extra to enable Ed25519 signing with ``cryptography``.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import secrets
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _require_crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:
        raise RuntimeError("Install Limbo's optional manus-tunnel extra for Ed25519 support.") from exc
    return serialization, ed25519


class ManusTunnelError(RuntimeError):
    """The Manus tunnel client could not connect or relay a request."""


class _WebSocketClient:
    def __init__(self, sock: socket.socket, *, max_message_bytes: int = 8 * 1024 * 1024):
        self.sock = sock
        self.max_message_bytes = max_message_bytes
        self.closed = False
        self._send_lock = threading.Lock()

    @classmethod
    def connect(cls, url: str, *, timeout: float = 10.0, max_message_bytes: int = 8 * 1024 * 1024, host_header: str | None = None):
        parsed = urlsplit(url)
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            raise ManusTunnelError("WebSocket URL must use ws:// or wss:// with a hostname")
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        raw = socket.create_connection((parsed.hostname, port), timeout=timeout)
        sock = raw
        if parsed.scheme == "wss":
            context = ssl.create_default_context()
            sock = context.wrap_socket(raw, server_hostname=parsed.hostname)
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        host = host_header or (parsed.hostname if parsed.port is None else f"{parsed.hostname}:{parsed.port}")
        request = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        sock.sendall(request.encode("ascii"))
        header = cls._read_until(sock, b"\r\n\r\n", 65536)
        status_line = header.split(b"\r\n", 1)[0]
        if not status_line.startswith(b"HTTP/1.1 101"):
            sock.close()
            raise ManusTunnelError(f"Tunnel WebSocket upgrade failed: {status_line.decode('latin1', 'replace')}")
        return cls(sock, max_message_bytes=max_message_bytes)

    @staticmethod
    def _read_until(sock: socket.socket, marker: bytes, maximum: int) -> bytes:
        data = bytearray()
        while marker not in data:
            chunk = sock.recv(4096)
            if not chunk:
                raise ManusTunnelError("WebSocket closed during handshake")
            data.extend(chunk)
            if len(data) > maximum:
                raise ManusTunnelError("WebSocket handshake headers too large")
        return bytes(data)

    @staticmethod
    def _read_exact(sock: socket.socket, count: int) -> bytes:
        data = bytearray()
        while len(data) < count:
            chunk = sock.recv(count - len(data))
            if not chunk:
                raise ManusTunnelError("WebSocket connection closed")
            data.extend(chunk)
        return bytes(data)

    def send(self, payload: bytes | str, *, binary: bool = False) -> None:
        if self.closed:
            raise ManusTunnelError("WebSocket is closed")
        body = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        if len(body) > self.max_message_bytes:
            raise ManusTunnelError("WebSocket message too large")
        opcode = 2 if binary else 1
        mask = secrets.token_bytes(4)
        length = len(body)
        if length < 126: header = bytes((0x80 | opcode, 0x80 | length))
        elif length < 65536: header = bytes((0x80 | opcode, 0x80 | 126)) + length.to_bytes(2, "big")
        else: header = bytes((0x80 | opcode, 0x80 | 127)) + length.to_bytes(8, "big")
        encoded = bytes(value ^ mask[index % 4] for index, value in enumerate(body))
        with self._send_lock:
            self.sock.sendall(header + mask + encoded)

    def recv(self) -> tuple[int, bytes] | None:
        fragments = bytearray(); opcode_seen = None
        while not self.closed:
            first, second = self._read_exact(self.sock, 2)
            fin, opcode, masked = bool(first & 0x80), first & 0x0F, bool(second & 0x80)
            length = second & 0x7F
            if length == 126: length = int.from_bytes(self._read_exact(self.sock, 2), "big")
            elif length == 127: length = int.from_bytes(self._read_exact(self.sock, 8), "big")
            if length > self.max_message_bytes or (opcode >= 8 and (not fin or length > 125)):
                self.close(1009, "Frame limit"); return None
            mask = self._read_exact(self.sock, 4) if masked else b""
            payload = bytearray(self._read_exact(self.sock, length))
            if masked:
                for index in range(length): payload[index] ^= mask[index % 4]
            if opcode == 8:
                self.close(1000, "peer closed"); return None
            if opcode == 9:
                self._send_control(10, bytes(payload)); continue
            if opcode == 10: continue
            if opcode in (1, 2):
                if opcode_seen is not None: self.close(1002, "nested frame"); return None
                opcode_seen = opcode
            elif opcode != 0 or opcode_seen is None:
                self.close(1002, "invalid continuation"); return None
            fragments.extend(payload)
            if len(fragments) > self.max_message_bytes: self.close(1009, "message too large"); return None
            if fin: return opcode_seen, bytes(fragments)
        return None

    def _send_control(self, opcode: int, payload: bytes) -> None:
        with self._send_lock:
            self.sock.sendall(bytes((0x80 | opcode, len(payload))) + payload)

    def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed: return
        self.closed = True
        try:
            payload = code.to_bytes(2, "big") + reason.encode("utf-8")[:123]
            self._send_control(8, payload)
        except (OSError, ManusTunnelError):
            pass
        try: self.sock.close()
        except OSError: pass


class ManusTunnelClient:
    """Bridge a local Limbo HTTP/WebSocket server to Manus Tunnel."""

    def __init__(self, tunnel_url: str, client_id: str, private_key, *, local_origin: str = "http://127.0.0.1:8000", reconnect: bool = True, heartbeat_seconds: float = 10.0):
        self.tunnel_url = tunnel_url; self.client_id = client_id; self.private_key = private_key; self.local_origin = local_origin.rstrip("/"); self.reconnect = reconnect; self.heartbeat_seconds = heartbeat_seconds
        self._tunnel: _WebSocketClient | None = None; self._stop = threading.Event(); self._send_lock = threading.Lock(); self._websockets: dict[str, _WebSocketClient] = {}; self._http: dict[str, dict] = {}
        serialization, _ = _require_crypto(); self.public_key = private_key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)

    @classmethod
    def from_private_key(cls, tunnel_url: str, client_id: str, key_path: str | Path, **kwargs):
        serialization, _ = _require_crypto(); key = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None); return cls(tunnel_url, client_id, key, **kwargs)

    @classmethod
    def enroll(cls, enrollment_url: str, tunnel_url: str, token: str, client_id: str, name: str, key_path: str | Path, **kwargs):
        serialization, ed25519 = _require_crypto(); private_key = ed25519.Ed25519PrivateKey.generate(); public = private_key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        body = json.dumps({"token": token, "clientId": client_id, "name": name, "publicKey": _b64url(public)}).encode()
        request = urllib.request.Request(enrollment_url.rstrip("/") + "/api/enroll", data=body, headers={"content-type": "application/json", "user-agent": "limbo-manus-client/1.0"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=15) as response: result = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise ManusTunnelError(f"Enrollment failed with HTTP {exc.code}") from exc
        path = Path(key_path).expanduser(); path.write_bytes(private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())); os.chmod(path, 0o600)
        return cls(tunnel_url, client_id, private_key, **kwargs), result

    def _send(self, message: dict) -> None:
        tunnel = self._tunnel
        if tunnel is None: raise ManusTunnelError("Tunnel is not connected")
        with self._send_lock: tunnel.send(json.dumps(message))

    def _authenticate(self, tunnel: _WebSocketClient) -> None:
        opcode, raw = tunnel.recv(); challenge = json.loads(raw.decode())
        if challenge.get("type") != "challenge": raise ManusTunnelError("Tunnel did not send a challenge")
        timestamp = int(time.time() * 1000); _, ed25519 = _require_crypto(); message = f"limbo-tunnel-v1|{self.client_id}|{challenge['challenge']}|{timestamp}"; signature = self.private_key.sign(message.encode())
        tunnel.send(json.dumps({"version": 1, "type": "authenticate", "clientId": self.client_id, "publicKey": _b64url(self.public_key), "timestamp": timestamp, "signature": _b64url(signature)}))
        _, raw = tunnel.recv(); authenticated = json.loads(raw.decode())
        if authenticated.get("type") != "authenticated": raise ManusTunnelError(authenticated.get("code", "Tunnel authentication failed"))

    def _heartbeat(self, tunnel: _WebSocketClient) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            try: self._send({"version": 1, "type": "heartbeat", "timestamp": int(time.time() * 1000)})
            except ManusTunnelError: return

    def _serve_http(self, request_id: str, state: dict) -> None:
        parsed = urlsplit(self.local_origin); connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection; connection = connection_class(parsed.hostname, parsed.port, timeout=30)
        headers = {key: value for key, value in state["headers"].items() if key.lower() not in {"connection", "transfer-encoding", "content-length"}}; headers["Host"] = state.get("hostname", headers.get("Host", parsed.hostname))
        target = state["path"] + state.get("query", "")
        try:
            connection.request(state["method"], target, body=bytes(state["body"]), headers=headers); response = connection.getresponse(); self._send({"version": 1, "type": "http_response_start", "requestId": request_id, "status": response.status, "headers": {key: value for key, value in response.getheaders() if key.lower() not in {"connection", "transfer-encoding"}}})
            while True:
                chunk = response.read(64 * 1024)
                if not chunk: break
                self._send({"version": 1, "type": "http_response_chunk", "requestId": request_id, "data": _b64url(chunk)})
            self._send({"version": 1, "type": "http_end", "requestId": request_id})
        except Exception as exc:
            self._send({"version": 1, "type": "error", "requestId": request_id, "code": "LOCAL_UPSTREAM_ERROR"})
        finally:
            self._http.pop(request_id, None); connection.close()

    def _open_websocket(self, message: dict) -> None:
        parsed = urlsplit(self.local_origin); target = f"{'wss' if parsed.scheme == 'https' else 'ws'}://{parsed.hostname}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}{message['path']}{message.get('query', '')}"
        local = _WebSocketClient.connect(target); self._websockets[message["connectionId"]] = local; self._send({"version": 1, "type": "ws_opened", "connectionId": message["connectionId"]})
        def relay():
            try:
                while not self._stop.is_set():
                    received = local.recv()
                    if received is None: break
                    opcode, payload = received; self._send({"version": 1, "type": "ws_frame", "connectionId": message["connectionId"], "binary": opcode == 2, "data": _b64url(payload)})
            except (OSError, ManusTunnelError): pass
            finally:
                self._send({"version": 1, "type": "ws_close", "connectionId": message["connectionId"], "code": 1000}); self._websockets.pop(message["connectionId"], None)
        threading.Thread(target=relay, name="limbo-manus-ws", daemon=True).start()

    def run_once(self) -> None:
        tunnel = _WebSocketClient.connect(self.tunnel_url); self._tunnel = tunnel; self._authenticate(tunnel); threading.Thread(target=self._heartbeat, args=(tunnel,), daemon=True).start()
        while not self._stop.is_set():
            received = tunnel.recv()
            if received is None: break
            _, raw = received; message = json.loads(raw.decode()); kind = message.get("type")
            if kind == "heartbeat_ack": continue
            if kind == "http_request": self._http[message["requestId"]] = {**message, "body": bytearray()}
            elif kind == "http_request_chunk" and message.get("requestId") in self._http: self._http[message["requestId"]]["body"].extend(_unb64url(message["data"]))
            elif kind == "http_end" and message.get("requestId") in self._http: self._serve_http(message["requestId"], self._http.pop(message["requestId"]))
            elif kind == "http_cancel": self._http.pop(message.get("requestId"), None)
            elif kind == "ws_open": self._open_websocket(message)
            elif kind == "ws_frame" and message.get("connectionId") in self._websockets: self._websockets[message["connectionId"]].send(_unb64url(message["data"]), binary=bool(message.get("binary")))
            elif kind == "ws_close" and message.get("connectionId") in self._websockets: self._websockets.pop(message["connectionId"]).close(message.get("code", 1000), "upstream closed")

    def run_forever(self, *, backoff_seconds: float = 1.0) -> None:
        while not self._stop.is_set():
            try: self.run_once()
            except (OSError, ManusTunnelError, json.JSONDecodeError):
                if not self.reconnect: raise
            finally:
                if self._tunnel: self._tunnel.close()
                self._tunnel = None
            if self.reconnect: self._stop.wait(backoff_seconds); backoff_seconds = min(backoff_seconds * 2, 30.0)

    def stop(self) -> None:
        self._stop.set()
        for websocket in list(self._websockets.values()): websocket.close()
        self._websockets.clear()
        if self._tunnel: self._tunnel.close()
