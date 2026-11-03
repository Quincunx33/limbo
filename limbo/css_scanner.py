"""CSS dependency scanner for @import and url() references."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CSSReference:
    specifier: str
    kind: str
    start: int = 0
    end: int = 0


_COMMENT = re.compile(r"/\*[\s\S]*?\*/")
_REFERENCE = re.compile(
    r"""@import\s+(?:url\(\s*)?["']([^"']+)["']\s*\)?|url\(\s*["']([^"']+)["']\s*\)""",
    re.IGNORECASE,
)


def scan_css_dependencies(source: str) -> tuple[CSSReference, ...]:
    """Find local CSS imports and URL assets, ignoring CSS comments and data URLs."""
    masked = _COMMENT.sub(lambda match: " " * len(match.group(0)), source)
    found: list[CSSReference] = []
    seen: set[tuple[str, str, int]] = set()
    for match in _REFERENCE.finditer(masked):
        group = 1 if match.group(1) else 2
        specifier = match.group(group)
        if not specifier or specifier.startswith(("data:", "http://", "https://", "#")):
            continue
        kind = "import" if match.group(1) else "asset"
        key = (specifier, kind, match.start(group))
        if key not in seen:
            seen.add(key)
            found.append(CSSReference(specifier, kind, match.start(group), match.end(group)))
    return tuple(found)
