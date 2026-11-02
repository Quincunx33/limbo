"""Deterministic route table with named routes and reusable route groups."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    pattern: re.Pattern[str]
    handler: Callable
    name: str | None = None
    priority: int = 0
    middleware: tuple[Callable, ...] = ()


def compile_path(path: str) -> re.Pattern[str]:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("Route paths must start with '/'")
    pieces: list[str] = []
    names: set[str] = set()
    cursor = 0
    for match in re.finditer(r"\{([^{}]+)\}", path):
        parameter = match.group(1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", parameter):
            raise ValueError(f"Invalid route parameter: {{{parameter}}}")
        if parameter in names:
            raise ValueError(f"Duplicate route parameter: {parameter}")
        names.add(parameter)
        pieces.append(re.escape(path[cursor:match.start()]))
        pieces.append(f"(?P<{parameter}>[^/]+)")
        cursor = match.end()
    tail = path[cursor:]
    if "{" in tail or "}" in tail:
        raise ValueError("Unmatched or malformed route parameter")
    pieces.append(re.escape(tail))
    return re.compile("^" + "".join(pieces) + "$")


def _specificity(path: str) -> tuple[int, int, int]:
    parameters = len(re.findall(r"\{[A-Za-z_][A-Za-z0-9_]*\}", path))
    static_segments = sum(1 for segment in path.split("/") if segment and "{" not in segment)
    return (static_segments, len(path) - parameters * 2, -parameters)


def _route_signature(path: str) -> str:
    """Normalize parameter names so /users/{id} conflicts with /users/{slug}."""
    return re.sub(r"\{[A-Za-z_][A-Za-z0-9_]*\}", "{}", path)


class Router:
    def __init__(self) -> None:
        self.routes: list[Route] = []

    def add(self, method: str, path: str, handler: Callable, name: str | None = None,
            priority: int = 0, middleware: tuple[Callable, ...] = ()) -> None:
        method = method.upper()
        if not re.fullmatch(r"[A-Z]+", method):
            raise ValueError("Invalid HTTP method")
        pattern = compile_path(path)
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise ValueError("Route name must be a non-empty string")
        if not isinstance(priority, int):
            raise TypeError("Route priority must be an integer")
        for route in self.routes:
            if route.method == method and _route_signature(route.path) == _route_signature(path):
                raise ValueError(f"Conflicting route: {method} {path} conflicts with {route.path}")
            if name and route.name == name and route.path != path:
                raise ValueError(f"Route name already used for another path: {name}")
        self.routes.append(Route(method, path, pattern, handler, name, priority, tuple(middleware)))

    def match(self, method: str, path: str) -> tuple[Route | None, dict[str, str], set[str]]:
        allowed: set[str] = set()
        matches: list[tuple[Route, dict[str, str]]] = []
        for route in self.routes:
            found = route.pattern.fullmatch(path)
            if found:
                allowed.add(route.method)
                matches.append((route, found.groupdict()))
        if not matches:
            return None, {}, allowed
        matches.sort(key=lambda item: (item[0].priority, *_specificity(item[0].path)), reverse=True)
        requested = method.upper()
        for route, params in matches:
            if route.method == requested:
                return route, params, allowed
        if requested == "HEAD":
            for route, params in matches:
                if route.method == "GET":
                    return route, params, allowed
        return None, {}, allowed

    def url_for(self, name: str, **parameters: object) -> str:
        candidates = [route for route in self.routes if route.name == name]
        if not candidates:
            raise KeyError(f"Unknown route name: {name}")
        path = candidates[0].path
        required = set(candidates[0].pattern.groupindex)
        missing = required - parameters.keys()
        extra = parameters.keys() - required
        if missing or extra:
            details = []
            if missing:
                details.append("missing: " + ", ".join(sorted(missing)))
            if extra:
                details.append("unexpected: " + ", ".join(sorted(extra)))
            raise TypeError("Invalid parameters for route " + name + " (" + "; ".join(details) + ")")
        return re.sub(
            r"\{([A-Za-z_][A-Za-z0-9_]*)\}",
            lambda match: quote(str(parameters[match.group(1)]), safe=""),
            path,
        )

    def describe(self) -> list[dict[str, object]]:
        return [
            {"method": route.method, "path": route.path, "name": route.name,
             "priority": route.priority, "parameters": tuple(route.pattern.groupindex)}
            for route in self.routes
        ]


class RouteGroup:
    """A prefix/name namespace that registers routes on a parent Router."""

    def __init__(self, router: Router, *, prefix: str = "", name: str | None = None) -> None:
        if prefix and not prefix.startswith("/"):
            raise ValueError("Route group prefix must start with '/'")
        self.router = router
        self.prefix = prefix.rstrip("/")
        self.name = name.strip(".") if name else None
        if name is not None and not self.name:
            raise ValueError("Route group name cannot be empty")

    def _path(self, path: str) -> str:
        if not path.startswith("/"):
            raise ValueError("Route paths must start with '/'")
        return self.prefix + path or "/"

    def _name(self, name: str | None) -> str | None:
        if name is None:
            return None
        return f"{self.name}.{name}" if self.name else name

    def route(self, path: str, methods: tuple[str, ...] = ("GET",), *,
              name: str | None = None, priority: int = 0, middleware: tuple[Callable, ...] = ()):
        full_path, full_name = self._path(path), self._name(name)

        def decorate(handler):
            for method in methods:
                self.router.add(method, full_path, handler, name=full_name, priority=priority, middleware=middleware)
            return handler
        return decorate

    def get(self, path: str, **kwargs):
        return self.route(path, ("GET",), **kwargs)

    def post(self, path: str, **kwargs):
        return self.route(path, ("POST",), **kwargs)

    def put(self, path: str, **kwargs):
        return self.route(path, ("PUT",), **kwargs)

    def patch(self, path: str, **kwargs):
        return self.route(path, ("PATCH",), **kwargs)

    def delete(self, path: str, **kwargs):
        return self.route(path, ("DELETE",), **kwargs)

    def group(self, prefix: str, *, name: str | None = None) -> "RouteGroup":
        if not prefix.startswith("/"):
            raise ValueError("Route group prefix must start with '/'")
        nested_name = ".".join(part for part in (self.name, name) if part)
        return RouteGroup(self.router, prefix=self.prefix + prefix, name=nested_name or None)
