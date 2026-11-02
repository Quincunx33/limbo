"""Optional-by-use SQLite convenience layer backed only by sqlite3."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence, Any


class Database:
    """A small SQLite wrapper. Use placeholders for all untrusted values.

    A single connection is serialized with a reentrant lock. It supports
    in-memory databases and is intended for lightweight local applications.
    """
    def __init__(self, path: str | Path = "limbo.sqlite3") -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._closed = False

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            self._ensure_open()
            return self.connection.execute(sql, parameters)

    def executemany(self, sql: str, rows) -> sqlite3.Cursor:
        with self._lock:
            self._ensure_open()
            return self.connection.executemany(sql, rows)

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator["Database"]:
        with self._lock:
            self._ensure_open()
            self.connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield self
            except BaseException:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self.connection.close()
                self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Database is closed")

    def __enter__(self) -> "Database": return self
    def __exit__(self, exc_type, exc, tb) -> None: self.close()
