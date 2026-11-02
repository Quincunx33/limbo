"""Bounded multipart/form-data parsing and safe upload persistence."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from email.parser import BytesParser
from email.policy import default
from email.message import Message


@dataclass(frozen=True)
class UploadFile:
    filename: str
    content_type: str
    content: bytes

    @property
    def size(self) -> int:
        return len(self.content)

    def save(self, directory: str | os.PathLike[str], filename: str | None = None) -> Path:
        root = Path(directory).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Upload destination must be a directory")
        chosen = _safe_name(filename or self.filename)
        destination = root / chosen
        # Exclusive creation avoids silently overwriting existing user data.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(destination, flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(self.content)
        except Exception:
            try: destination.unlink()
            except OSError: pass
            raise
        return destination


def _safe_name(name: str) -> str:
    # Treat both slash conventions as separators regardless of host OS.
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    if name in ("", ".", ".."):
        raise ValueError("Upload filename is empty or unsafe")
    return name[:180]


def parse_multipart(content_type: str, body: bytes) -> dict[str, list[str | UploadFile]]:
    if len(body) > 8 * 1024 * 1024:
        raise ValueError("Multipart body exceeds the parser limit")
    envelope = Message()
    envelope["Content-Type"] = content_type
    if not envelope.get_content_type().lower() == "multipart/form-data":
        raise ValueError("Content-Type is not multipart/form-data")
    boundary = envelope.get_boundary()
    if not boundary or len(boundary.encode("ascii", "ignore")) > 200:
        raise ValueError("Invalid multipart boundary")
    message = BytesParser(policy=default).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1", "replace") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )
    if not message.is_multipart():
        raise ValueError("Malformed multipart body")
    result: dict[str, list[str | UploadFile]] = {}
    for part in message.iter_parts():
        disposition = part.get_content_disposition()
        if disposition != "form-data":
            continue
        field_name = part.get_param("name", header="content-disposition")
        if not field_name or len(field_name) > 256:
            raise ValueError("Multipart field is missing a valid name")
        raw = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename is not None:
            value: str | UploadFile = UploadFile(
                _safe_name(filename), part.get_content_type() or "application/octet-stream", raw
            )
        else:
            charset = part.get_content_charset() or "utf-8"
            try:
                value = raw.decode(charset)
            except (LookupError, UnicodeDecodeError) as exc:
                raise ValueError("Invalid multipart text field encoding") from exc
        result.setdefault(field_name, []).append(value)
    return result
