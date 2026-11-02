"""A deliberately small RFC 6455 server-side WebSocket implementation."""
from __future__ import annotations

import base64
import hashlib
import hmac
import socket
import struct
from typing import Any

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def handshake_response(headers: dict[str, str]) -> tuple[int, dict[str, str]]:
    connection_tokens = {x.strip().lower() for x in headers.get("connection", "").split(",")}
    if "upgrade" not in connection_tokens or headers.get("upgrade", "").lower() != "websocket":
        return 400, {}
    if headers.get("sec-websocket-version") != "13":
        return 426, {"Sec-WebSocket-Version": "13"}
    key = headers.get("sec-websocket-key", "")
    try:
        decoded = base64.b64decode(key, validate=True)
    except (ValueError, TypeError):
        return 400, {}
    if len(decoded) != 16:
        return 400, {}
    accept = base64.b64encode(hashlib.sha1((key + _GUID).encode("ascii")).digest()).decode("ascii")
    return 101, {"Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Accept": accept}


def _read_exact(sock: Any, count: int) -> bytes:
    parts = bytearray()
    while len(parts) < count:
        chunk = sock.read(count - len(parts)) if hasattr(sock, "read") else sock.recv(count - len(parts))
        if not chunk:
            raise EOFError("WebSocket connection closed")
        parts.extend(chunk)
    return bytes(parts)


class WebSocket:
    def __init__(self, sock: socket.socket, *, reader: Any | None = None,
                 max_message_bytes: int = 8 * 1024 * 1024) -> None:
        if not isinstance(max_message_bytes, int) or max_message_bytes < 1:
            raise ValueError("max_message_bytes must be a positive integer")
        self.socket = sock
        self.reader = reader or sock
        self.max_message_bytes = max_message_bytes
        self.closed = False

    def send(self, message: str | bytes) -> None:
        if self.closed: raise ConnectionError("WebSocket is closed")
        opcode = 1 if isinstance(message, str) else 2
        payload = message.encode("utf-8") if isinstance(message, str) else bytes(message)
        if len(payload) > self.max_message_bytes: raise ValueError("WebSocket message too large")
        self._send_frame(opcode, payload)

    def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed: return
        reason_bytes = reason.encode("utf-8")
        if len(reason_bytes) > 123: raise ValueError("Close reason exceeds 123 bytes")
        if not (1000 <= code <= 4999) or code in (1004, 1005, 1006, 1015):
            raise ValueError("Invalid WebSocket close code")
        try: self._send_frame(8, struct.pack("!H", code) + reason_bytes)
        finally: self.closed = True

    def receive(self) -> str | bytes | None:
        fragments = bytearray()
        message_opcode: int | None = None
        while not self.closed:
            try:
                first, second = _read_exact(self.reader, 2)
            except (socket.timeout, TimeoutError):
                self.close(1001, "Idle timeout")
                return None
            fin, rsv, opcode, masked = bool(first & 0x80), first & 0x70, first & 0x0F, bool(second & 0x80)
            if rsv or not masked:
                self.close(1002, "Protocol error"); return None
            length = second & 0x7F
            if length == 126: length = struct.unpack("!H", _read_exact(self.reader, 2))[0]
            elif length == 127:
                raw = _read_exact(self.reader, 8)
                if raw[0] & 0x80: self.close(1002, "Invalid length"); return None
                length = struct.unpack("!Q", raw)[0]
            if length > self.max_message_bytes or (opcode >= 8 and (not fin or length > 125)):
                self.close(1009 if length > self.max_message_bytes else 1002, "Frame limit"); return None
            mask = _read_exact(self.reader, 4)
            payload = bytearray(_read_exact(self.reader, length))
            for index in range(length): payload[index] ^= mask[index % 4]
            if opcode == 8:
                if length == 1: self.close(1002, "Invalid close frame"); return None
                if not self.closed:
                    try: self._send_frame(8, bytes(payload))
                    finally: self.closed = True
                return None
            if opcode == 9:
                self._send_frame(10, bytes(payload)); continue
            if opcode == 10: continue
            if opcode in (1, 2):
                if message_opcode is not None:
                    self.close(1002, "Nested data frame"); return None
                message_opcode = opcode
                fragments.extend(payload)
            elif opcode == 0:
                if message_opcode is None:
                    self.close(1002, "Unexpected continuation"); return None
                fragments.extend(payload)
            else:
                self.close(1002, "Unknown opcode"); return None
            if len(fragments) > self.max_message_bytes:
                self.close(1009, "Message too large"); return None
            if fin:
                try:
                    return fragments.decode("utf-8") if message_opcode == 1 else bytes(fragments)
                except UnicodeDecodeError:
                    self.close(1007, "Invalid UTF-8"); return None
                finally:
                    message_opcode = None
        return None

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        length = len(payload)
        if length < 126: header = bytes((0x80 | opcode, length))
        elif length < 65536: header = bytes((0x80 | opcode, 126)) + struct.pack("!H", length)
        else: header = bytes((0x80 | opcode, 127)) + struct.pack("!Q", length)
        self.socket.sendall(header + payload)
