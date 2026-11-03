"""SQLite schema migrations. No third-party driver and no fake external database."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .database import Database

_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_UP = re.compile(r"(?im)^--\s*\+limbo\s+Up\s*$")
_DOWN = re.compile(r"(?im)^--\s*\+limbo\s+Down\s*$")


class MigrationError(RuntimeError):
    """A migration could not be applied, rolled back, or verified."""


class DatabaseURLError(MigrationError):
    """A database URL is outside the tested adapter set."""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    up: str
    down: str | None = None

    def checksum(self) -> str:
        return hashlib.sha256(self.up.encode("utf-8")).hexdigest()


class Migrator:
    """Apply ordered, checksummed migrations on one SQLite database.

    Each migration runs in its own transaction. History lives in
    ``limbo_schema_migrations``. This is not a pool and not a PostgreSQL adapter.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def ensure(self) -> None:
        self.database.execute(
            "CREATE TABLE IF NOT EXISTS limbo_schema_migrations ("
            "version TEXT PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )

    def applied(self) -> dict[str, str]:
        self.ensure()
        rows = self.database.execute(
            "SELECT version, checksum FROM limbo_schema_migrations ORDER BY version"
        ).fetchall()
        return {row["version"]: row["checksum"] for row in rows}

    def apply(self, migrations: list[Migration]) -> list[str]:
        ordered = _validate(migrations)
        existing = self.applied()
        seen_missing = False
        for migration in ordered:
            if migration.version not in existing:
                seen_missing = True
            elif seen_missing:
                raise MigrationError(
                    f"Migration history has {migration.version} applied but is missing an earlier version."
                )
            elif existing[migration.version] != migration.checksum():
                raise MigrationError(
                    f"Checksum changed for applied migration {migration.version}. Refusing to continue."
                )
        applied_now: list[str] = []
        for migration in ordered:
            if migration.version in existing:
                continue
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                with self.database.transaction():
                    for statement in split_sql(migration.up):
                        self.database.execute(statement)
                    self.database.execute(
                        "INSERT INTO limbo_schema_migrations(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                        (migration.version, migration.name, migration.checksum(), stamp),
                    )
            except sqlite3.Error as exc:
                raise MigrationError(f"Migration {migration.version} failed: {exc}") from exc
            applied_now.append(migration.version)
        return applied_now

    def rollback(self, migrations: list[Migration], *, steps: int = 1) -> list[str]:
        if steps < 1:
            raise MigrationError("Rollback steps must be at least 1.")
        ordered = _validate(migrations)
        existing = self.applied()
        applied_ordered = [item for item in ordered if item.version in existing]
        if len(applied_ordered) < steps:
            raise MigrationError("Cannot roll back more migrations than are applied.")
        target = applied_ordered[-steps:]
        for migration in reversed(target):
            if not migration.down or not migration.down.strip():
                raise MigrationError(f"Migration {migration.version} has no down SQL; refusing partial rollback.")
        undone: list[str] = []
        for migration in reversed(target):
            try:
                with self.database.transaction():
                    for statement in split_sql(migration.down or ""):
                        self.database.execute(statement)
                    self.database.execute(
                        "DELETE FROM limbo_schema_migrations WHERE version = ?", (migration.version,)
                    )
            except sqlite3.Error as exc:
                raise MigrationError(f"Rollback of {migration.version} failed: {exc}") from exc
            undone.append(migration.version)
        return undone

    def status(self, migrations: list[Migration]) -> list[dict[str, str | bool]]:
        existing = self.applied()
        rows = []
        for migration in _validate(migrations):
            rows.append({
                "version": migration.version,
                "name": migration.name,
                "applied": migration.version in existing,
                "checksum": migration.checksum(),
            })
        return rows


def load_migration(path: str | Path) -> Migration:
    file = Path(path)
    stem = file.name
    if stem.endswith(".sql"):
        stem = stem[:-4]
    version, _, name = stem.partition("_")
    if not name:
        name = version
        version = stem
    text = file.read_text(encoding="utf-8")
    up_match = _UP.search(text)
    if not up_match:
        raise MigrationError(f"{file.name} is missing a '-- +limbo Up' section.")
    down_match = _DOWN.search(text)
    if down_match and down_match.start() < up_match.start():
        raise MigrationError(f"{file.name} has Down before Up.")
    if down_match:
        up = text[up_match.end():down_match.start()]
        down = text[down_match.end():]
    else:
        up = text[up_match.end():]
        down = None
    return Migration(version, name.replace("_", " "), up.strip(), None if down is None else down.strip())


def load_directory(directory: str | Path) -> list[Migration]:
    root = Path(directory)
    if not root.is_dir():
        raise MigrationError(f"Migration directory does not exist: {root}")
    files = sorted(path for path in root.glob("*.sql") if path.is_file())
    return [load_migration(path) for path in files]


def open_database(url: str) -> Database:
    """Open the built-in SQLite adapter, or reject untested engines."""
    if url in {":memory:", "sqlite://", "sqlite:///:memory:"}:
        return Database(":memory:")
    if url.startswith("sqlite:///"):
        return Database(url[len("sqlite:///"):])
    if url.startswith("file:"):
        return Database(url)
    scheme = url.split(":", 1)[0].lower()
    if scheme in {"postgres", "postgresql", "mysql", "mariadb"}:
        raise DatabaseURLError(
            f"{scheme} is not a supported Limbo database adapter. No driver is bundled and no "
            "integration suite has passed. Use SQLite, or pass your own tested adapter. "
            "Limbo will not pretend a connection pool exists."
        )
    raise DatabaseURLError(f"Unsupported database URL scheme: {scheme}")


def split_sql(script: str) -> list[str]:
    statements: list[str] = []
    buf: list[str] = []
    quote = ""
    i = 0
    length = len(script)
    while i < length:
        char = script[i]
        if quote:
            buf.append(char)
            if char == quote:
                quote = ""
            elif char == "\\":
                if i + 1 < length:
                    buf.append(script[i + 1])
                    i += 2
                    continue
            i += 1
            continue
        if char in {"'", '"'} and not quote:
            quote = char
            buf.append(char)
            i += 1
            continue
        if script.startswith("--", i):
            newline = script.find("\n", i)
            i = length if newline < 0 else newline
            continue
        if char == ";":
            statement = "".join(buf).strip()
            if statement:
                statements.append(statement)
            buf = []
            i += 1
            continue
        buf.append(char)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _validate(migrations: list[Migration]) -> list[Migration]:
    seen: set[str] = set()
    ordered = sorted(migrations, key=lambda item: item.version)
    for migration in ordered:
        if not isinstance(migration, Migration):
            raise MigrationError("Migrations must be Migration instances.")
        if not _VERSION.fullmatch(migration.version):
            raise MigrationError(f"Invalid migration version: {migration.version!r}")
        if migration.version in seen:
            raise MigrationError(f"Duplicate migration version: {migration.version}")
        seen.add(migration.version)
        if not migration.up or not migration.up.strip():
            raise MigrationError(f"Migration {migration.version} has empty up SQL.")
    return ordered
