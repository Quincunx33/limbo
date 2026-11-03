"""Local production build for browser-native frontend projects.

The builder hashes files, rewrites relative URLs, emits a manifest and can
optionally minify through a locally installed esbuild. It never downloads
compilers, never invents a bundler, and never claims code splitting.
"""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .css_scanner import scan_css_dependencies
from .frontend import BROWSER_BUILTINS, FrontendError, ModuleResolver
from .frontend_scanner import scan_imports

_SOURCE_SUFFIXES = {".js", ".mjs", ".css"}
_ASSET_SUFFIXES = {
    ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico",
    ".woff", ".woff2", ".ttf", ".wasm", ".mp3", ".mp4", ".webmanifest", ".json", ".txt",
}
_COMPILED_ONLY = {".ts", ".tsx", ".jsx", ".vue", ".svelte"}


class BuildError(RuntimeError):
    """The production build cannot produce an honest output."""


@dataclass(frozen=True)
class BuildReport:
    out_dir: Path
    manifest: dict
    warnings: tuple[str, ...]
    digest: str
    asset_count: int
    minify: str


def build_project(project: str | Path, config=None, *, frontend: str | None = None, entry: str | None = None,
                  out_dir: str | None = None, base: str | None = None, minify: bool | None = None,
                  sourcemap: bool | None = None, clean: bool | None = None,
                  code_splitting: bool = False) -> BuildReport:
    """Build a frontend directory into a static output folder.

    Supported without Node: relative ESM, CSS `@import` / `url()`, side-effect
    CSS extraction, and binary assets including WASM. TypeScript, JSX and SFC
    files require the already-detected local compiler; missing tools raise
    BuildError instead of emitting source as if it were runnable.
    """
    if code_splitting:
        raise BuildError(
            "Code splitting is not available. Limbo only emits one file per module "
            "and does not invoke a bundler splitter."
        )
    root = Path(project).expanduser().resolve()
    if not root.is_dir():
        raise BuildError(f"Project directory does not exist: {root}")
    frontend_rel = frontend or _cfg(config, "frontend", "directory", "frontend")
    entry_rel = entry or _cfg(config, "frontend", "entry", "src/main.js")
    out_rel = out_dir or _cfg(config, "build", "out_dir", "dist")
    base_path = base or _cfg(config, "build", "base", "/")
    do_minify = _cfg(config, "build", "minify", False) if minify is None else minify
    do_map = _cfg(config, "build", "sourcemap", False) if sourcemap is None else sourcemap
    do_clean = _cfg(config, "build", "clean", True) if clean is None else clean
    _validate_base(base_path)
    frontend_root = _frontend_root(root, frontend_rel)
    resolver = ModuleResolver(frontend_root)
    entry_path = resolver._resolve_path(frontend_root / entry_rel)
    html_path = frontend_root / "index.html"
    if entry_path is None and not html_path.is_file():
        raise BuildError(
            f"No frontend entry at {frontend_rel}/{entry_rel} and no index.html. "
            "Create a browser-native module or pass --entry."
        )
    if entry_path is not None:
        resolver._assert_in_project(entry_path)
    warnings: list[str] = []
    external: set[str] = set()
    files: dict[Path, bytes] = {}
    if entry_path is not None:
        _walk(frontend_root, resolver, entry_path, files, external, warnings)
    html_refs: list[Path] = []
    html_text = ""
    if html_path.is_file():
        html_text = html_path.read_text(encoding="utf-8")
        for url in _html_urls(html_text):
            target = _resolve_html_url(resolver, frontend_root, url)
            if target is None:
                continue
            html_refs.append(target)
            if target.suffix.lower() in _SOURCE_SUFFIXES | _COMPILED_ONLY and target not in files:
                _walk(frontend_root, resolver, target, files, external, warnings)
            elif target not in files:
                _add_leaf(frontend_root, target, files)
    for path in list(files):
        suffix = path.suffix.lower()
        if suffix in _COMPILED_ONLY:
            raise BuildError(_compiler_message(suffix))
    if not files and not html_path.is_file():
        raise BuildError("Nothing to build.")
    names = {path: _output_name(frontend_root, path, raw) for path, raw in files.items()}
    emitted: dict[str, bytes] = {}
    css_links: list[str] = []
    for path, raw in files.items():
        suffix = path.suffix.lower()
        if suffix == ".css":
            text = raw.decode("utf-8")
            text = _rewrite_css(text, path, frontend_root, resolver, names)
            emitted[names[path]] = text.encode("utf-8")
        elif suffix in {".js", ".mjs"}:
            text = raw.decode("utf-8")
            text, links = _rewrite_js(text, path, frontend_root, resolver, names, base_path)
            css_links.extend(links)
            emitted[names[path]] = text.encode("utf-8")
        else:
            emitted[names[path]] = raw
    esbuild = _local_esbuild(frontend_root)
    minify_mode = "off"
    if do_minify:
        if esbuild:
            minify_mode = "esbuild"
            emitted = {name: _minify(esbuild, name, data, frontend_root) for name, data in emitted.items()}
        else:
            minify_mode = "skipped"
            warnings.append("Minify requested but esbuild is not installed locally; emitted unminified files. Limbo does not install it.")
    target = _cfg(config, "build", "target", None)
    if target and minify_mode != "esbuild":
        warnings.append(f"Build target {target!r} was not applied because esbuild is unavailable.")
    if do_map:
        for name, data in list(emitted.items()):
            if name.endswith((".js", ".mjs", ".css")):
                text = data.decode("utf-8")
                smap = _identity_map(text, name.rsplit("/", 1)[-1])
                map_name = name + ".map"
                emitted[map_name] = json.dumps(smap, separators=(",", ":")).encode("utf-8")
                suffix = "\n" if text.endswith("\n") else ""
                emitted[name] = (text + suffix + f"\n/*# sourceMappingURL={map_name.rsplit('/', 1)[-1]} */\n").encode("utf-8")
    html_out = _render_html(
        html_text, html_path if html_path.is_file() else None, frontend_root, resolver, names,
        base_path, css_links, entry_path, entry_rel,
    )
    emitted["index.html"] = html_out.encode("utf-8")
    file_map: dict[str, str] = {}
    if html_path.is_file():
        file_map["index.html"] = "index.html"
    else:
        file_map["index.html"] = "index.html"
    for path, name in names.items():
        file_map[path.relative_to(frontend_root).as_posix()] = name
    integrity = {name: "sha256-" + base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")
                 for name, data in sorted(emitted.items())}
    manifest = {
        "schema_version": 1,
        "base": base_path,
        "entry": entry_rel if entry_path is not None else None,
        "files": dict(sorted(file_map.items())),
        "external": sorted(external),
        "integrity": integrity,
        "code_splitting": False,
        "minify": minify_mode,
    }
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    out = _safe_out(root, frontend_root, out_rel)
    if do_clean and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    for name, data in emitted.items():
        dest = out / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_body = {
        "digest": digest,
        "asset_count": len(emitted),
        "warnings": warnings,
        "minify": minify_mode,
        "code_splitting": False,
    }
    (out / "limbo-build-report.json").write_text(json.dumps(report_body, indent=2) + "\n", encoding="utf-8")
    return BuildReport(out, manifest, tuple(warnings), digest, len(emitted), minify_mode)


def _cfg(config, section: str, key: str, default):
    if config is None:
        return default
    return config.section(section).get(key, default)


def _validate_base(base: str) -> None:
    if not isinstance(base, str) or not base.startswith("/") or not base.endswith("/") or ".." in base.split("/"):
        raise BuildError("Base path must be an absolute URL path that starts and ends with '/' and does not contain '..'.")


def _frontend_root(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve() if relative not in {".", ""} else root
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise BuildError("Frontend directory escapes the project root.") from exc
    if candidate.is_dir() and ((candidate / "src").is_dir() or (candidate / "index.html").is_file() or candidate == root):
        return candidate
    if (root / "index.html").is_file() or (root / "src").is_dir():
        return root
    raise BuildError(
        f"Frontend directory '{relative}' was not found. Expected {relative}/src or an index.html."
    )


def _safe_out(root: Path, frontend: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip() or relative.strip() in {".", "/"}:
        raise BuildError("Refusing to use the project root as the build output directory.")
    candidate = Path(relative)
    if candidate.is_absolute():
        raise BuildError("build out_dir must stay inside the project; absolute paths are rejected.")
    out = (root / candidate).resolve()
    try:
        out.relative_to(root)
    except ValueError as exc:
        raise BuildError("build out_dir escapes the project root.") from exc
    if out == root or out == frontend or out in frontend.parents:
        raise BuildError("Refusing to delete the project or frontend source directory.")
    return out


def _output_name(root: Path, path: Path, raw: bytes) -> str:
    rel = path.relative_to(root).as_posix()
    digest = hashlib.sha256(rel.encode("utf-8") + b"\0" + raw).hexdigest()[:12]
    return f"assets/{digest}{path.suffix.lower()}"


def _walk(root: Path, resolver: ModuleResolver, entry: Path, files: dict[Path, bytes], external: set[str], warnings: list[str]) -> None:
    pending = [entry]
    seen: set[Path] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        resolver._assert_in_project(current)
        rel_parts = current.relative_to(root).parts
        if "node_modules" in rel_parts or any(part.startswith(".") for part in rel_parts):
            raise BuildError(f"Refusing to publish hidden or node_modules path: {current.relative_to(root)}")
        try:
            raw = current.read_bytes()
        except OSError as exc:
            raise BuildError(f"Cannot read {current}: {exc}") from exc
        files[current] = raw
        suffix = current.suffix.lower()
        if suffix not in _SOURCE_SUFFIXES:
            if suffix in _COMPILED_ONLY:
                files[current] = raw
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeError as exc:
            raise BuildError(f"{current.name} is not valid UTF-8 source.") from exc
        if suffix == ".css":
            for ref in scan_css_dependencies(text):
                if ref.kind == "asset" and ref.specifier.startswith("/"):
                    target = resolver._resolve_path(root / ref.specifier.lstrip("/"))
                else:
                    target = _resolve_local(resolver, current, ref.specifier)
                if target is None:
                    raise BuildError(f"CSS reference '{ref.specifier}' in {current.name} was not found.")
                pending.append(target)
            continue
        try:
            scan = scan_imports(text)
        except ValueError as exc:
            raise BuildError(f"Cannot scan imports in {current.name}: {exc}") from exc
        for diag in scan.diagnostics:
            warnings.append(f"{current.relative_to(root).as_posix()}: {diag}")
        for ref in scan.imports:
            spec = ref.specifier
            if spec.startswith(("http://", "https://", "data:")):
                continue
            head = spec.split("/", 1)[0]
            if spec.startswith("node:") or head in BROWSER_BUILTINS:
                raise BuildError(
                    f"Node.js built-in '{spec}' in {current.name} cannot ship in a browser build."
                )
            if not spec.startswith(("./", "../", "/")):
                external.add(spec)
                warnings.append(
                    f"{current.relative_to(root).as_posix()} imports '{spec}', left external. "
                    "Limbo does not bundle npm packages or claim they are browser-ready."
                )
                continue
            try:
                target = resolver.resolve(spec, current).path
            except FrontendError as exc:
                raise BuildError(str(exc)) from exc
            pending.append(target)


def _add_leaf(root: Path, path: Path, files: dict[Path, bytes]) -> None:
    if "node_modules" in path.relative_to(root).parts:
        return
    files[path] = path.read_bytes()


def _resolve_local(resolver: ModuleResolver, importer: Path, specifier: str) -> Path | None:
    if specifier.startswith(("data:", "http://", "https://", "#")):
        return None
    try:
        if specifier.startswith(("./", "../", "/")):
            return resolver.resolve(specifier, importer).path
    except FrontendError:
        return None
    return None


def _rewrite_css(text: str, path: Path, root: Path, resolver: ModuleResolver, names: dict[Path, str]) -> str:
    spans = []
    for ref in scan_css_dependencies(text):
        if ref.specifier.startswith(("/", "./", "../")):
            target = resolver.resolve(ref.specifier, path).path if ref.specifier.startswith(("./", "../", "/")) else None
            if target is None or target not in names:
                raise BuildError(f"Cannot rewrite CSS reference '{ref.specifier}' in {path.name}.")
            spans.append((ref.start, ref.end, "./" + names[target].rsplit("/", 1)[-1]))
    for start, end, repl in sorted(spans, reverse=True):
        text = text[:start] + repl + text[end:]
    return text


def _rewrite_js(text: str, path: Path, root: Path, resolver: ModuleResolver, names: dict[Path, str], base: str):
    try:
        scan = scan_imports(text)
    except ValueError as exc:
        raise BuildError(f"Cannot rewrite {path.name}: {exc}") from exc
    spans = []
    links: list[str] = []
    for ref in scan.imports:
        spec = ref.specifier
        if not spec.startswith(("./", "../", "/")):
            continue
        try:
            target = resolver.resolve(spec, path).path
        except FrontendError as exc:
            raise BuildError(str(exc)) from exc
        if target.suffix.lower() == ".css":
            if ref.kind != "side-effect":
                raise BuildError(
                    f"{path.name} imports '{spec}' with bindings. The production builder extracts "
                    "side-effect stylesheets into <link> tags only; CSS module bindings stay on the dev server."
                )
            href = base + names[target]
            links.append(href)
            statement = _side_effect_span(text, ref.start, ref.end)
            spans.append((statement[0], statement[1], ""))
            continue
        if target not in names:
            raise BuildError(f"Missing build output for '{spec}' imported by {path.name}.")
        spans.append((ref.start, ref.end, json.dumps("./" + names[target].rsplit("/", 1)[-1])))
    for start, end, repl in sorted(spans, reverse=True):
        text = text[:start] + repl + text[end:]
    return text, links


def _side_effect_span(source: str, spec_start: int, spec_end: int) -> tuple[int, int]:
    keyword = source.rfind("import", 0, spec_start)
    if keyword < 0:
        raise BuildError("Could not remove a CSS side-effect import.")
    end = spec_end
    if end < len(source) and source[end] == ";":
        end += 1
    if end < len(source) and source[end] == "\n":
        end += 1
    return keyword, end


def _render_html(html: str, html_path: Path | None, root: Path, resolver: ModuleResolver, names: dict[Path, str],
                 base: str, css_links: list[str], entry_path: Path | None, entry_rel: str) -> str:
    links = []
    seen = set()
    for href in css_links:
        if href not in seen:
            seen.add(href)
            links.append(f'<link rel="stylesheet" href="{href}">')
    if html_path is None:
        if entry_path is None or entry_path not in names:
            raise BuildError("Cannot synthesize index.html without a JavaScript entry.")
        script = base + names[entry_path]
        link_html = "\n".join(links)
        return (
            "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>Limbo</title>\n{link_html}\n</head>\n<body>\n<div id=\"app\"></div>\n"
            f"<script type=\"module\" src=\"{script}\"></script>\n</body>\n</html>\n"
        )
    spans = []
    for match in _HTML_ATTR.finditer(html):
        url = match.group(2)
        target = _resolve_html_url(resolver, root, url)
        if target is None or target not in names:
            continue
        spans.append((match.start(2), match.end(2), base + names[target]))
    for start, end, repl in sorted(spans, reverse=True):
        html = html[:start] + repl + html[end:]
    if links:
        block = "\n".join(links) + "\n"
        if "</head>" in html:
            html = html.replace("</head>", block + "</head>", 1)
        else:
            html = block + html
    return html if html.endswith("\n") else html + "\n"


def _resolve_html_url(resolver: ModuleResolver, root: Path, url: str) -> Path | None:
    if not url or url.startswith(("http://", "https://", "data:", "mailto:", "#", "//")):
        return None
    path = url.split("?", 1)[0].split("#", 1)[0]
    if not path or path.endswith("/"):
        return None
    try:
        if path.startswith("/"):
            return resolver._resolve_path(root / path.lstrip("/"))
        return resolver._resolve_path(root / path)
    except (OSError, ValueError):
        return None


def _html_urls(html: str) -> list[str]:
    return [match.group(2) for match in _HTML_ATTR.finditer(html)]


import re

_HTML_ATTR = re.compile(r"""(?:src|href)\s*=\s*(['"])(.*?)\1""", re.IGNORECASE)


def _compiler_message(suffix: str) -> str:
    if suffix in {".ts", ".tsx", ".jsx"}:
        return (
            f"Production build cannot transform {suffix} files without a local esbuild executable. "
            "Install it in the frontend project (`npm install --save-dev esbuild`). Limbo will not install it."
        )
    framework = "Vue" if suffix == ".vue" else "Svelte"
    package = "@vue/compiler-sfc" if suffix == ".vue" else "svelte"
    return (
        f"{framework} single-file components are not emitted by the dependency-free production builder. "
        f"Install Node.js and the local {package} package, then use the dev server. "
        "A missing compiler is not treated as a successful build."
    )


def _local_esbuild(root: Path) -> str | None:
    local = root / "node_modules" / ".bin" / "esbuild"
    if local.is_file():
        return str(local)
    parent = root.parent / "node_modules" / ".bin" / "esbuild"
    if parent.is_file():
        return str(parent)
    return shutil.which("esbuild")


def _minify(executable: str, name: str, data: bytes, cwd: Path) -> bytes:
    if not name.endswith((".js", ".mjs", ".css")):
        return data
    loader = "css" if name.endswith(".css") else "js"
    args = [executable, f"--loader={loader}", "--minify", "--log-level=error"]
    if loader == "js":
        args.append("--format=esm")
    try:
        result = subprocess.run(args, input=data, capture_output=True, timeout=30, check=False, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError(f"esbuild failed while minifying {name}: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise BuildError(detail or f"esbuild exited {result.returncode} while minifying {name}")
    return result.stdout


def _identity_map(source: str, filename: str) -> dict:
    count = max(1, len(source.splitlines()))
    mappings = "AAAA" if count == 1 else "AAAA;" + "AACA;" * (count - 2) + "AACA"
    return {
        "version": 3,
        "file": filename,
        "sources": [filename],
        "sourcesContent": [source],
        "names": [],
        "mappings": mappings,
    }
