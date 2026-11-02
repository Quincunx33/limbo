"""Small deterministic decorator-based route table."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    pattern: re.Pattern[str]
    handler: Callable
    name: str | None = None


def compile_path(path: str) -> re.Pattern[str]:
    if not path.startswith("/"):
        raise ValueError("Route paths must start with '/'")
    pieces: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", path):
        pieces.append(re.escape(path[cursor:match.start()]))
        pieces.append(f"(?P<{match.group(1)}>[^/]+)")
        cursor = match.end()
    pieces.append(re.escape(path[cursor:]))
    return re.compile("^" + "".join(pieces) + "$" )


class Router:
    def __init__(self) -> None:
        self.routes: list[Route] = []

    def add(self, method: str, path: str, handler: Callable, name: str | None = None) -> None:
        method = method.upper()
        for route in self.routes:
            if route.method == method and route.path == path:
                raise ValueError(f"Duplicate route: {method} {path}")
        self.routes.append(Route(method, path, compile_path(path), handler, name))

    def match(self, method: str, path: str) -> tuple[Route | None, dict[str, str], set[str]]:
        allowed: set[str] = set()
        head_fallback: tuple[Route, dict[str, str]] | None = None
        for route in self.routes:
            found = route.pattern.fullmatch(path)
            if found:
                allowed.add(route.method)
                if route.method == method.upper():
                    return route, found.groupdict(), allowed
                if method.upper() == "HEAD" and route.method == "GET":
                    head_fallback = (route, found.groupdict())
        if head_fallback:
            return head_fallback[0], head_fallback[1], allowed
        return None, {}, allowed
