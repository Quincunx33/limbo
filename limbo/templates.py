"""Simple local HTML templates with escaped substitutions."""
from __future__ import annotations

import html
import re
from pathlib import Path
from string import Template
from typing import Any


class TemplateEngine:
    def __init__(self, directory: str | Path = "templates") -> None:
        self.directory = Path(directory).expanduser().resolve()

    def render(self, name: str, context: dict[str, Any] | None = None) -> str:
        if not name or "\\" in name or "\x00" in name:
            raise ValueError("Invalid template name")
        path = (self.directory / name).resolve()
        try:
            path.relative_to(self.directory)
        except ValueError as exc:
            raise ValueError("Template path escapes the template directory") from exc
        if not path.is_file():
            raise FileNotFoundError(name)
        text = path.read_text(encoding="utf-8")
        values = {key: html.escape(str(value), quote=True) for key, value in (context or {}).items()}
        return Template(text).safe_substitute(values)
