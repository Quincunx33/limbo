"""Small dependency-free JavaScript ESM import scanner.

This is a lexical scanner, not a JavaScript parser. It recognizes literal
static imports, re-exports, side-effect imports and literal dynamic imports,
while ignoring comments and ordinary string/template contents.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImportReference:
    specifier: str
    kind: str
    start: int
    end: int


@dataclass(frozen=True)
class ScanResult:
    imports: tuple[ImportReference, ...]
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    start: int
    end: int
    line: int


def _tokens(source: str) -> list[_Token]:
    result: list[_Token] = []
    i = 0
    line = 1
    length = len(source)
    while i < length:
        char = source[i]
        if char in " \t\r\f":
            i += 1
            continue
        if char == "\n":
            line += 1
            i += 1
            continue
        if source.startswith("//", i):
            end = source.find("\n", i + 2)
            i = length if end < 0 else end
            continue
        if source.startswith("/*", i):
            end = source.find("*/", i + 2)
            if end < 0:
                raise ValueError(f"unterminated block comment at line {line}")
            line += source[i:end + 2].count("\n")
            i = end + 2
            continue
        if char in ("'", '"'):
            quote = char
            start = i
            start_line = line
            i += 1
            value = []
            while i < length:
                current = source[i]
                if current == "\\":
                    if i + 1 >= length:
                        raise ValueError(f"unterminated string at line {start_line}")
                    escaped = source[i + 1]
                    translations = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}
                    value.append(translations.get(escaped, escaped))
                    i += 2
                    continue
                if current == quote:
                    i += 1
                    break
                if current == "\n":
                    raise ValueError(f"newline in string literal at line {start_line}")
                value.append(current)
                i += 1
            else:
                raise ValueError(f"unterminated string at line {start_line}")
            result.append(_Token("string", "".join(value), start, i, start_line))
            continue
        if char == chr(96):
            start_line = line
            i += 1
            while i < length:
                if source[i] == "\\":
                    if i + 1 < length and source[i + 1] == "\n":
                        line += 1
                    i += 2
                    continue
                if source[i] == chr(96):
                    i += 1
                    break
                if source[i] == "\n":
                    line += 1
                i += 1
            else:
                raise ValueError(f"unterminated template literal at line {start_line}")
            continue
        if char.isalpha() or char in "_$":
            start = i
            i += 1
            while i < length and (source[i].isalnum() or source[i] in "_$"):
                i += 1
            result.append(_Token("identifier", source[start:i], start, i, line))
            continue
        result.append(_Token("punctuation", char, i, i + 1, line))
        i += 1
    return result


def scan_imports(source: str) -> ScanResult:
    """Extract supported literal ESM references without matching comments/strings."""
    tokens = _tokens(source)
    found: list[ImportReference] = []
    diagnostics: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, token in enumerate(tokens):
        if token.kind != "identifier" or token.value not in {"import", "export"}:
            continue
        next_index = index + 1
        if next_index >= len(tokens):
            continue
        next_token = tokens[next_index]
        if token.value == "import":
            if next_token.value == ".":
                continue
            if next_token.value == "(":
                if next_index + 2 < len(tokens) and tokens[next_index + 1].kind == "string" and tokens[next_index + 2].value == ")":
                    literal = tokens[next_index + 1]
                    ref = ImportReference(literal.value, "dynamic", literal.start, literal.end)
                    key = (ref.specifier, ref.kind)
                    if key not in seen:
                        found.append(ref)
                        seen.add(key)
                else:
                    diagnostics.append(f"non-literal dynamic import at line {token.line} is not statically resolvable")
                continue
            if next_token.kind == "string":
                ref = ImportReference(next_token.value, "side-effect", next_token.start, next_token.end)
                key = (ref.specifier, ref.kind)
                if key not in seen:
                    found.append(ref)
                    seen.add(key)
                continue
        for cursor in range(next_index, min(len(tokens), next_index + 256)):
            current = tokens[cursor]
            if current.value == ";":
                break
            if current.kind == "identifier" and current.value == "from" and cursor + 1 < len(tokens):
                literal = tokens[cursor + 1]
                if literal.kind == "string":
                    kind = "re-export" if token.value == "export" else "static"
                    ref = ImportReference(literal.value, kind, literal.start, literal.end)
                    key = (ref.specifier, ref.kind)
                    if key not in seen:
                        found.append(ref)
                        seen.add(key)
                break
            if cursor > next_index and current.line > tokens[cursor - 1].line and current.value not in {"from", "as", "}"}:
                previous = tokens[cursor - 1]
                if previous.value not in {",", "{", "}", "*"}:
                    break
    return ScanResult(tuple(found), tuple(diagnostics))
