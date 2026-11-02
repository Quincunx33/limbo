"""Project metadata operations used by the Limbo CLI."""
from __future__ import annotations

import json
import re
from pathlib import Path


class ProjectMetadataError(ValueError):
    """Invalid or unavailable frontend project metadata."""


def edit_package_metadata(root: str | Path, action: str, package: str, *, dev: bool = False) -> str:
    """Edit package.json declarations without invoking a package manager."""
    if action not in {"add", "remove"}:
        raise ProjectMetadataError("action must be add or remove")
    if package in {".", ".."} or not re.fullmatch(r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+", package):
        raise ProjectMetadataError(f"invalid package name: {package!r}")
    path = Path(root) / "package.json"
    if not path.is_file():
        raise ProjectMetadataError("package.json not found in the current directory")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectMetadataError(f"cannot read package.json: {exc}") from exc
    if not isinstance(data, dict):
        raise ProjectMetadataError("package.json must contain a JSON object")
    if action == "add":
        section = "devDependencies" if dev else "dependencies"
        target = data.setdefault(section, {})
        if not isinstance(target, dict):
            raise ProjectMetadataError(f"package.json {section} must be an object")
        if package in target:
            return f"{package} is already declared in {section}"
        target[package] = "*"
    else:
        found = False
        for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            target = data.get(section, {})
            if isinstance(target, dict) and package in target:
                del target[package]
                found = True
        if not found:
            raise ProjectMetadataError(f"{package} is not declared in package.json")
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ProjectMetadataError(f"cannot write package.json: {exc}") from exc
    return f"{'Added' if action == 'add' else 'Removed'} {package}; package manager was not run"


def preview_directory(root: str | Path, output: str) -> Path:
    """Resolve a configured preview directory and require it to exist."""
    path = (Path(root) / output).expanduser().resolve()
    if not path.is_dir():
        raise ProjectMetadataError(f"preview directory does not exist: {path}")
    return path
