"""Persistent content-addressed cache for deterministic frontend transforms."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


class TransformCache:
    """Store compiler outputs by source, options and tool identity.

    Cache files are private implementation details under the project root and
    are never part of the frontend public module tree.
    """

    def __init__(self, project_root: str | Path, *, directory: str = ".limbo/cache/frontend") -> None:
        self.root = Path(project_root).expanduser().resolve(strict=True)
        self.directory = (self.root / directory).resolve()
        try:
            self.directory.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Frontend cache directory must stay inside the project root") from exc
        self.directory.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def key(self, source: bytes, *, compiler: str, options: dict[str, object] | None = None) -> str:
        payload = json.dumps(
            {"compiler": compiler, "options": options or {}},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("utf-8")
        digest = hashlib.sha256()
        digest.update(b"limbo-transform-cache-v1\0")
        digest.update(payload)
        digest.update(b"\0")
        digest.update(source)
        return digest.hexdigest()

    def get(self, key: str) -> str | None:
        if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
            raise ValueError("Invalid transform cache key")
        path = self.directory / key[:2] / (key[2:] + ".json")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            value = payload.get("code")
            if payload.get("schema") != 1 or not isinstance(value, str):
                raise ValueError("invalid cache record")
        except FileNotFoundError:
            self.misses += 1
            return None
        except (OSError, json.JSONDecodeError, ValueError, AttributeError):
            self.misses += 1
            return None
        self.hits += 1
        return value

    def put(self, key: str, code: str) -> None:
        if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
            raise ValueError("Invalid transform cache key")
        target = self.directory / key[:2] / (key[2:] + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"schema": 1, "code": code}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def clear(self) -> int:
        removed = 0
        if not self.directory.exists():
            return removed
        for path in self.directory.glob("*/*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed
