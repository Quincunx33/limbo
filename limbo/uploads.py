"""Bounded multipart parsing and safe upload persistence."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from email.parser import BytesParser
from email.policy import default
from email.message import Message
from typing import Iterator


@dataclass(frozen=True)
class UploadFile:
    filename: str
    content_type: str
    content: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.filename, str) or not isinstance(self.content_type, str):
            raise TypeError("Upload filename and content type must be strings")
        if not isinstance(self.content, bytes):
            raise TypeError("Upload content must be bytes")

    @property
    def size(self) -> int:
        return len(self.content)

    def iter_chunks(self, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        """Iterate over bounded byte chunks without copying the full upload."""
        if not isinstance(chunk_size, int) or chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset:offset + chunk_size]

    def save(self, directory: str | os.PathLike[str], filename: str | None = None, *,
             chunk_size: int = 64 * 1024, max_bytes: int | None = None) -> Path:
        """Persist without overwrite using exclusive, no-follow file creation."""
        if not isinstance(chunk_size, int) or chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        if max_bytes is not None and (not isinstance(max_bytes, int) or max_bytes < 0):
            raise ValueError("max_bytes must be a non-negative integer")
        if max_bytes is not None and self.size > max_bytes:
            raise ValueError("Upload exceeds the configured file size limit")
        root = Path(directory).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Upload destination must be a directory")
        chosen = _safe_name(filename or self.filename)
        destination = root / chosen
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        directory_fd = None
        fd = None
        use_dir_fd = os.open in getattr(os, "supports_dir_fd", set()) and hasattr(os, "O_DIRECTORY")
        try:
            if use_dir_fd:
                directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                fd = os.open(chosen, flags, 0o600, dir_fd=directory_fd)
            else:
                fd = os.open(destination, flags, 0o600)
            with os.fdopen(fd, "wb") as output:
                fd = None
                for chunk in self.iter_chunks(chunk_size):
                    output.write(chunk)
        except Exception:
            if fd is not None:
                os.close(fd)
            try:
                if use_dir_fd and directory_fd is not None:
                    os.unlink(chosen, dir_fd=directory_fd)
                else:
                    destination.unlink()
            except OSError:
                pass
            raise
        finally:
            if directory_fd is not None:
                os.close(directory_fd)
        return destination


def _safe_name(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    if name in ("", ".", ".."):
        raise ValueError("Upload filename is empty or unsafe")
    return name[:180]


def parse_multipart(content_type: str, body: bytes, *, max_body_bytes: int = 8 * 1024 * 1024,
                    max_files: int = 20, max_fields: int = 100,
                    max_file_bytes: int = 8 * 1024 * 1024) -> dict[str, list[str | UploadFile]]:
    """Parse a bounded multipart body with independent part and file limits."""
    limits = (max_body_bytes, max_files, max_fields, max_file_bytes)
    if any(not isinstance(value, int) or value < 0 for value in limits):
        raise ValueError("Multipart limits must be non-negative integers")
    if max_body_bytes < 1 or max_files < 1 or max_fields < 1 or max_file_bytes < 1:
        raise ValueError("Multipart limits must be positive")
    if not isinstance(body, bytes) or len(body) > max_body_bytes:
        raise ValueError("Multipart body exceeds the configured limit")
    envelope = Message()
    envelope["Content-Type"] = content_type
    if envelope.get_content_type().lower() != "multipart/form-data":
        raise ValueError("Content-Type is not multipart/form-data")
    boundary = envelope.get_boundary()
    if not boundary or len(boundary.encode("ascii", "ignore")) > 200:
        raise ValueError("Invalid multipart boundary")
    message = BytesParser(policy=default).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1", "replace")
        + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )
    if not message.is_multipart():
        raise ValueError("Malformed multipart body")
    result: dict[str, list[str | UploadFile]] = {}
    file_count = 0
    field_count = 0
    part_count = 0
    for part in message.iter_parts():
        part_count += 1
        if part_count > max_files + max_fields:
            raise ValueError("Multipart part count exceeds the configured limit")
        if part.get_content_disposition() != "form-data":
            continue
        field_name = part.get_param("name", header="content-disposition")
        if not field_name or len(field_name) > 256:
            raise ValueError("Multipart field is missing a valid name")
        raw = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename is not None:
            file_count += 1
            if file_count > max_files:
                raise ValueError("Multipart file count exceeds the configured limit")
            if len(raw) > max_file_bytes:
                raise ValueError("Uploaded file exceeds the configured size limit")
            value: str | UploadFile = UploadFile(
                _safe_name(filename), part.get_content_type() or "application/octet-stream", raw
            )
        else:
            field_count += 1
            if field_count > max_fields:
                raise ValueError("Multipart field count exceeds the configured limit")
            charset = part.get_content_charset() or "utf-8"
            try:
                value = raw.decode(charset)
            except (LookupError, UnicodeDecodeError) as exc:
                raise ValueError("Invalid multipart text field encoding") from exc
        result.setdefault(field_name, []).append(value)
    return result
