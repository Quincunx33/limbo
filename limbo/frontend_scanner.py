"""Small dependency-free JavaScript ESM import scanner.

This is a lexical scanner, not a JavaScript parser. It recognizes literal
static imports, re-exports, side-effect imports and literal dynamic imports,
while ignoring comments and ordinary string/template contents.
"""
from __future__ import annotations

import re
import json
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache


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


@lru_cache(maxsize=256)
def _v8_module_dependencies(source: str) -> tuple[str, ...] | None:
    """Ask V8 to parse standard JavaScript modules when Node is available.

    SourceTextModule is part of Node itself, so this adds no package dependency.
    TypeScript/JSX or older Nodes fall back to the lexical scanner.
    """
    node = shutil.which("node")
    if not node:
        return None
    script = (
        "const vm=require('vm');let s='';process.stdin.setEncoding('utf8');"
        "process.stdin.on('data',c=>s+=c);process.stdin.on('end',()=>{"
        "try{const m=new vm.SourceTextModule(s);process.stdout.write(JSON.stringify(m.dependencySpecifiers))}"
        "catch(e){process.exitCode=2;process.stderr.write(String(e.message))}})"
    )
    try:
        result = subprocess.run(
            [node, "--no-warnings", "--experimental-vm-modules", "-e", script],
            input=source, text=True, capture_output=True, timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    try:
        dependencies = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return tuple(item for item in dependencies if isinstance(item, str))


@lru_cache(maxsize=128)
def _acorn_scan(source: str) -> ScanResult | None:
    """Parse ordinary JavaScript with an installed local Acorn package.

    Acorn is intentionally optional: browser-native projects still work with
    the standard-library scanner, while projects that install the documented
    parser receive AST-accurate module ranges and syntax diagnostics.  The
    tiny Node bridge emits only the stable data Limbo needs and walks the AST
    without requiring an additional AST-walk package.
    """
    node = shutil.which("node")
    if not node:
        return None
    script = r'''
const fs=require('fs'); let source='';
try { require.resolve('acorn'); } catch (_) { process.stdout.write(JSON.stringify({available:false})); process.exit(0); }
const acorn=require('acorn');
process.stdin.setEncoding('utf8'); process.stdin.on('data', c => source += c);
process.stdin.on('end', () => {
  try {
    const ast=acorn.parse(source,{ecmaVersion:'latest',sourceType:'module',locations:true,allowHashBang:true});
    const imports=[]; const diagnostics=[];
    const add=(node,kind) => {
      if (!node || !node.source || node.source.type !== 'Literal' || typeof node.source.value !== 'string') return;
      imports.push({specifier:node.source.value,kind,start:node.source.start,end:node.source.end,line:node.source.loc.start.line});
    };
    const walk=(node) => {
      if (!node || typeof node !== 'object') return;
      if (node.type==='ImportDeclaration') add(node,node.specifiers.length?'static':'side-effect');
      else if (node.type==='ExportNamedDeclaration' || node.type==='ExportAllDeclaration') add(node,'re-export');
      else if (node.type==='ImportExpression') {
        if (node.source && node.source.type==='Literal' && typeof node.source.value==='string')
          imports.push({specifier:node.source.value,kind:'dynamic',start:node.source.start,end:node.source.end,line:node.source.loc.start.line});
        else diagnostics.push(`non-literal dynamic import at line ${node.loc.start.line} is not statically resolvable`);
      }
      for (const [key,value] of Object.entries(node)) {
        if (key==='loc' || key==='start' || key==='end') continue;
        if (Array.isArray(value)) value.forEach(walk); else if (value && typeof value==='object') walk(value);
      }
    };
    walk(ast); process.stdout.write(JSON.stringify({available:true,imports,diagnostics}));
  } catch (error) {
    process.stdout.write(JSON.stringify({available:true,error:String(error.message),line:error.loc&&error.loc.line||1}));
  }
});
'''
    try:
        result = subprocess.run([node, "--no-warnings", "-e", script], input=source,
                                text=True, capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not payload.get("available"):
        return None
    if payload.get("error"):
        # Acorn intentionally covers standard JavaScript only.  TypeScript,
        # JSX and files using syntax outside the selected Acorn grammar fall
        # through to Limbo's conservative scanner (and, in the dev graph,
        # normally arrive here already transformed by esbuild).
        return None
    found: list[ImportReference] = []
    seen: set[tuple[str, str]] = set()
    for item in payload.get("imports", []):
        ref = ImportReference(item["specifier"], item["kind"], item["start"], item["end"])
        key = (ref.specifier, ref.kind)
        if key not in seen:
            found.append(ref); seen.add(key)
    return ScanResult(tuple(found), tuple(payload.get("diagnostics", ())))


def _template_expression_end(source: str, start: int) -> int:
    """Find a template interpolation's closing brace, skipping quoted text."""
    depth = 1
    i = start
    while i < len(source):
        if source.startswith("//", i):
            end = source.find("\n", i + 2)
            i = len(source) if end < 0 else end
            continue
        if source.startswith("/*", i):
            end = source.find("*/", i + 2)
            i = len(source) if end < 0 else end + 2
            continue
        char = source[i]
        if char == "/" and i + 1 < len(source) and source[i + 1] not in "/*=":
            previous = source[:i].rstrip()
            previous_char = previous[-1] if previous else ""
            previous_word = re.search(r"([A-Za-z_$][\w$]*)$", previous)
            if not previous or previous_char in "=(:,[!&|?;{" or (previous_word and previous_word.group(1) in {"return", "throw", "case", "await"}):
                i += 1
                in_class = False
                while i < len(source):
                    current = source[i]
                    if current == "\\":
                        i += 2
                    elif current == "[":
                        in_class = True
                        i += 1
                    elif current == "]":
                        in_class = False
                        i += 1
                    elif current == "/" and not in_class:
                        i += 1
                        while i < len(source) and source[i].isalpha():
                            i += 1
                        break
                    elif current == "\n":
                        break
                    else:
                        i += 1
                continue
        if char in ("'", '"', "`"):
            quote = char
            i += 1
            while i < len(source):
                if source[i] == "\\":
                    i += 2
                elif source[i] == quote:
                    i += 1
                    break
                else:
                    i += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(source)


def _tokens(source: str, *, offset: int = 0, line_offset: int = 0) -> list[_Token]:
    result: list[_Token] = []
    i = 0
    line = 1
    length = len(source)
    while i < length:
        char = source[i]
        if i == 0 and source.startswith("#!"):
            end = source.find("\n", i + 2)
            i = length if end < 0 else end
            continue
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
        if char == "/" and i + 1 < length and source[i + 1] not in "/*=":
            previous = result[-1].value if result else ""
            if previous in {"", "(", "[", "{", ",", ":", "=", "!", "?", ";", "return", "throw", "case", "=>", "&&", "||", "??"}:
                start = i
                i += 1
                in_class = False
                while i < length:
                    current = source[i]
                    if current == "\\":
                        i += 2
                        continue
                    if current == "[":
                        in_class = True
                    elif current == "]":
                        in_class = False
                    elif current == "/" and not in_class:
                        i += 1
                        while i < length and source[i].isalpha():
                            i += 1
                        break
                    elif current == "\n":
                        break
                    i += 1
                result.append(_Token("regex", "", offset + start, offset + i, line_offset + line))
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
            result.append(_Token("string", "".join(value), offset + start, offset + i, line_offset + start_line))
            continue
        if char == chr(96):
            start_line = line
            i += 1
            while i < length:
                if source.startswith("${", i):
                    expression_start = i + 2
                    expression_end = _template_expression_end(source, expression_start)
                    if expression_end >= length:
                        raise ValueError(f"unterminated template interpolation at line {line_offset + start_line}")
                    expression = source[expression_start:expression_end]
                    result.extend(_tokens(
                        expression,
                        offset=offset + expression_start,
                        line_offset=line_offset + source[:expression_start].count("\n"),
                    ))
                    line += expression.count("\n")
                    i = expression_end + 1
                    continue
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
            result.append(_Token("identifier", source[start:i], offset + start, offset + i, line_offset + line))
            continue
        result.append(_Token("punctuation", char, offset + i, offset + i + 1, line_offset + line))
        i += 1
    return result


def scan_imports(source: str) -> ScanResult:
    """Extract supported literal ESM references without matching comments/strings."""
    parsed = _acorn_scan(source)
    if parsed is not None:
        return parsed
    tokens = _tokens(source)
    found: list[ImportReference] = []
    diagnostics: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, token in enumerate(tokens):
        if token.kind != "identifier" or token.value not in {"import", "export"}:
            continue
        if index and tokens[index - 1].value in {".", "?.", "#"}:
            continue
        next_index = index + 1
        if next_index >= len(tokens):
            continue
        next_token = tokens[next_index]
        if token.value == "import":
            if next_token.value == ".":
                continue
            if next_token.value == "(":
                # `import` can also be an ordinary method name in a class or
                # object.  A zero-argument method (`import() {}`) is the most
                # common ambiguous form; do not report it as a dynamic import.
                if (next_index + 2 < len(tokens) and tokens[next_index + 1].value == ")"
                        and next_index + 2 < len(tokens)
                        and tokens[next_index + 2].value == "{"):
                    continue
                if (next_index + 2 < len(tokens) and tokens[next_index + 1].kind == "string"
                        and tokens[next_index + 2].value in {")", ","}):
                    literal = tokens[next_index + 1]
                    cursor = next_index + 2
                    depth = 0
                    valid_options = True
                    while cursor < len(tokens):
                        value = tokens[cursor].value
                        if value in {"(", "[", "{"}:
                            depth += 1
                        elif value in {")", "]", "}"}:
                            if value == ")" and depth == 0:
                                break
                            depth = max(0, depth - 1)
                        cursor += 1
                    if cursor >= len(tokens):
                        valid_options = False
                    if valid_options:
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
    parsed_dependencies = _v8_module_dependencies(source)
    if parsed_dependencies is not None:
        remaining = Counter(parsed_dependencies)
        verified = []
        for reference in found:
            if reference.kind == "dynamic":
                verified.append(reference)
            elif remaining[reference.specifier] > 0:
                verified.append(reference)
                remaining[reference.specifier] -= 1
        found = verified
    return ScanResult(tuple(found), tuple(diagnostics))


def rewrite_import_meta_hot(source: str) -> str:
    """Rewrite real import.meta.hot expressions to Limbo's optional HMR context."""
    tokens = _tokens(source)
    replacements: list[tuple[int, int]] = []
    for index in range(len(tokens) - 4):
        sequence = tokens[index:index + 5]
        if ([token.value for token in sequence] != ["import", ".", "meta", ".", "hot"]
                or sequence[0].kind != "identifier"
                or sequence[2].kind != "identifier"
                or sequence[4].kind != "identifier"):
            continue
        if index and tokens[index - 1].value == ".":
            continue
        replacements.append((sequence[0].start, sequence[4].end))
    replacement = (
        "(globalThis.__LIMBO_HMR_HOT__?globalThis.__LIMBO_HMR_HOT__(import.meta.url):"
        "{data:{},accept(){},dispose(){}})"
    )
    for start, end in reversed(replacements):
        source = source[:start] + replacement + source[end:]
    return source
