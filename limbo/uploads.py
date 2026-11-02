"""Bounded multipart parsing and safe upload persistence."""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
from email.message import Message
from pathlib import Path
from typing import Iterator


class UploadLimitError(ValueError):
    """A multipart body, part count or uploaded file exceeded configured limits."""


@dataclass(frozen=True)
class UploadFile:
    filename: str
    content_type: str
    content: bytes = b""
    temporary_path: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.filename, str) or not isinstance(self.content_type, str):
            raise TypeError("Upload filename and content type must be strings")
        if not isinstance(self.content, bytes):
            raise TypeError("Upload content must be bytes")
        if self.temporary_path is not None and not isinstance(self.temporary_path, Path):
            raise TypeError("temporary_path must be a pathlib.Path")

    @property
    def size(self) -> int:
        return self.temporary_path.stat().st_size if self.temporary_path is not None else len(self.content)

    def open(self):
        """Open the uploaded content for reading; temporary uploads live for the request."""
        if self.temporary_path is not None:
            return self.temporary_path.open("rb")
        import io
        return io.BytesIO(self.content)

    def iter_chunks(self, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        """Yield bounded chunks from memory-backed or disk-backed content."""
        if not isinstance(chunk_size, int) or chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        if self.temporary_path is None:
            for offset in range(0, len(self.content), chunk_size):
                yield self.content[offset:offset + chunk_size]
        else:
            with self.temporary_path.open("rb") as source:
                while True:
                    chunk = source.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk

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
        created = False
        use_dir_fd = os.open in getattr(os, "supports_dir_fd", set()) and hasattr(os, "O_DIRECTORY")
        try:
            if use_dir_fd:
                directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                fd = os.open(chosen, flags, 0o600, dir_fd=directory_fd)
            else:
                fd = os.open(destination, flags, 0o600)
            created = True
            with os.fdopen(fd, "wb") as output:
                fd = None
                for chunk in self.iter_chunks(chunk_size):
                    output.write(chunk)
        except Exception:
            if fd is not None:
                os.close(fd)
            if created:
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


class _StreamBuffer:
    def __init__(self, source, chunk_size: int = 64 * 1024) -> None:
        self.source = source
        self.chunk_size = chunk_size
        self.buffer = bytearray()
        self.eof = False

    def fill(self) -> None:
        if not self.eof:
            chunk = self.source.read(self.chunk_size)
            if not chunk:
                self.eof = True
            else:
                self.buffer.extend(chunk)

    def read_exact(self, size: int) -> bytes:
        while len(self.buffer) < size and not self.eof:
            self.fill()
        if len(self.buffer) < size:
            raise ValueError("Malformed multipart body")
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def readline(self, limit: int) -> bytes:
        while True:
            index = self.buffer.find(b"\r\n")
            if index >= 0:
                if index + 2 > limit:
                    raise ValueError("Multipart header line too large")
                return self.read_exact(index + 2)
            if len(self.buffer) >= limit or self.eof:
                raise ValueError("Malformed multipart headers")
            self.fill()

    def part_body(self, delimiter: bytes, output, *, max_bytes: int | None,
                  field_buffer: bytearray | None = None) -> tuple[int, bool]:
        written = 0
        keep = len(delimiter) + 2
        while True:
            index = self.buffer.find(delimiter)
            if index >= 0:
                payload = bytes(self.buffer[:index])
                del self.buffer[:index + len(delimiter)]
                if payload:
                    written += len(payload)
                    if max_bytes is not None and written > max_bytes:
                        raise UploadLimitError("Uploaded file size exceeds the configured limit")
                    if output is not None:
                        output.write(payload)
                    else:
                        field_buffer.extend(payload)
                suffix = self.read_exact(2)
                if suffix == b"--":
                    ending = self.read_exact(2)
                    if ending != b"\r\n":
                        raise ValueError("Malformed multipart closing boundary")
                    return written, True
                if suffix == b"\r\n":
                    return written, False
                raise ValueError("Malformed multipart boundary")
            if self.eof:
                raise ValueError("Malformed multipart body")
            if len(self.buffer) > keep:
                amount = len(self.buffer) - keep
                payload = bytes(self.buffer[:amount])
                del self.buffer[:amount]
                written += len(payload)
                if max_bytes is not None and written > max_bytes:
                    raise UploadLimitError("Uploaded file size exceeds the configured limit")
                if output is not None:
                    output.write(payload)
                else:
                    field_buffer.extend(payload)
            self.fill()


def parse_multipart_stream(content_type: str, source, *, max_body_bytes: int = 8 * 1024 * 1024,
                           max_files: int = 20, max_fields: int = 100,
                           max_file_bytes: int = 8 * 1024 * 1024,
                           chunk_size: int = 64 * 1024):
    """Parse multipart input incrementally; file payloads are spooled to private temp files.

    Returns (fields, TemporaryDirectory). The caller must clean the directory after
    the request handler completes. The source must be a body-limited binary reader.
    """
    limits = (max_body_bytes, max_files, max_fields, max_file_bytes)
    if any(not isinstance(value, int) or value < 1 for value in limits):
        raise ValueError("Multipart limits must be positive integers")
    if not isinstance(chunk_size, int) or chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    envelope = Message()
    envelope["Content-Type"] = content_type
    if envelope.get_content_type().lower() != "multipart/form-data":
        raise ValueError("Content-Type is not multipart/form-data")
    boundary = envelope.get_boundary()
    if not boundary or len(boundary.encode("ascii", "ignore")) > 200:
        raise ValueError("Invalid multipart boundary")
    marker = boundary.encode("ascii")
    reader = _StreamBuffer(source, chunk_size)
    tempdir = tempfile.TemporaryDirectory(prefix="limbo-upload-")
    result: dict[str, list[str | UploadFile]] = {}
    try:
        if reader.read_exact(len(marker) + 4) != b"--" + marker + b"\r\n":
            raise ValueError("Malformed multipart opening boundary")
        delimiter = b"\r\n--" + marker
        file_count = field_count = part_count = 0
        while True:
            header_bytes = bytearray()
            while True:
                line = reader.readline(8192)
                header_bytes.extend(line)
                if len(header_bytes) > 16384:
                    raise UploadLimitError("Multipart part headers exceed the configured limit")
                if line == b"\r\n":
                    break
            part = BytesParser(policy=default).parsebytes(
                bytes(header_bytes[:-2]) + b"\r\n\r\n"
            )
            if part.get_content_disposition() != "form-data":
                raise ValueError("Unsupported multipart disposition")
            field_name = part.get_param("name", header="content-disposition")
            if not field_name or len(field_name) > 256:
                raise ValueError("Multipart field is missing a valid name")
            filename = part.get_filename()
            part_count += 1
            if part_count > max_files + max_fields:
                raise UploadLimitError("Multipart part count exceeds the configured limit")
            if filename is not None:
                file_count += 1
                if file_count > max_files:
                    raise UploadLimitError("Multipart file count exceeds the configured limit")
                safe_name = _safe_name(filename)
                path = Path(tempdir.name) / f"upload-{file_count:04d}.part"
                size = 0
                with path.open("xb") as output:
                    size, final_part = reader.part_body(delimiter, output, max_bytes=max_file_bytes)
                result.setdefault(field_name, []).append(
                    UploadFile(safe_name, part.get_content_type() or "application/octet-stream",
                               b"", path)
                )
            else:
                field_count += 1
                if field_count > max_fields:
                    raise UploadLimitError("Multipart field count exceeds the configured limit")
                raw = bytearray()
                _, final_part = reader.part_body(delimiter, None, max_bytes=max_body_bytes, field_buffer=raw)
                charset = part.get_content_charset() or "utf-8"
                try:
                    value = bytes(raw).decode(charset)
                except (LookupError, UnicodeDecodeError) as exc:
                    raise ValueError("Invalid multipart text field encoding") from exc
                result.setdefault(field_name, []).append(value)
            if final_part:
                if reader.buffer:
                    raise ValueError("Unexpected data after multipart closing boundary")
                return result, tempdir
    except Exception:
        tempdir.cleanup()
        raise


def parse_multipart(content_type: str, body: bytes, *, max_body_bytes: int = 8 * 1024 * 1024,
                    max_files: int = 20, max_fields: int = 100,
                    max_file_bytes: int = 8 * 1024 * 1024) -> dict[str, list[str | UploadFile]]:
    """Parse a bounded in-memory multipart body (used by direct Request construction)."""
    if not isinstance(body, bytes) or len(body) > max_body_bytes:
        raise UploadLimitError("Multipart body exceeds the configured limit")
    import io
    result, tempdir = parse_multipart_stream(
        content_type, io.BytesIO(body), max_body_bytes=max_body_bytes,
        max_files=max_files, max_fields=max_fields, max_file_bytes=max_file_bytes,
    )
    # Preserve the legacy in-memory API for direct Request objects.
    try:
        materialized = {}
        for key, values in result.items():
            materialized[key] = [
                UploadFile(item.filename, item.content_type, b"".join(item.iter_chunks()))
                if isinstance(item, UploadFile) else item for item in values
            ]
        return materialized
    finally:
        tempdir.cleanup()
