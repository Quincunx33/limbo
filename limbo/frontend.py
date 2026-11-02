"""Local frontend module resolution and optional compiler orchestration.

The core is dependency-free. TypeScript/JSX and single-file framework compilers
are delegated to locally installed, optional mature compiler packages.
"""
from __future__ import annotations

import html
import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from .http import Request, Response
from .frontend_scanner import scan_imports
from .frontend_cache import TransformCache
from .css_scanner import scan_css_dependencies
from .frontend_engine import AdapterError, EsbuildAdapter


EXTENSIONS = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".json", ".css", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".wasm", ".mp3", ".mp4")
GRAPH_SOURCE_EXTENSIONS = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".css"}
BROWSER_BUILTINS = {"assert", "buffer", "child_process", "crypto", "events", "fs", "http", "https",
                    "module", "net", "os", "path", "process", "stream", "timers", "tls", "url", "util", "zlib"}
IMPORT_RE = re.compile(
    r"(?:\b(?:import|export)\s+(?:[^'\"]*?\s+from\s*)?|\bimport\s*\(\s*)['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)


class FrontendError(RuntimeError):
    """Actionable frontend resolution or optional-tool failure."""


@dataclass(frozen=True)
class ResolvedModule:
    path: Path
    specifier: str
    package_root: Path | None = None


class ModuleResolver:
    """Resolve local, tsconfig-aliased, and package imports within a project."""
    def __init__(self, project_root: str | os.PathLike[str], *, aliases: dict[str, list[str] | str] | None = None) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Frontend project root must be a directory")
        self.base_url = self.root
        self.aliases = self._load_aliases(aliases)

    def _load_aliases(self, explicit) -> list[tuple[str, list[str]]]:
        raw = explicit
        config = self.root / "tsconfig.json"
        if raw is None and config.is_file():
            try:
                payload = json.loads(config.read_text(encoding="utf-8"))
                options = payload.get("compilerOptions", {})
                base = options.get("baseUrl")
                if base:
                    self.base_url = (self.root / base).resolve()
                raw = options.get("paths", {})
                base_url = options.get("baseUrl")
                if base_url:
                    self.base_url = (self.root / base_url).resolve()
            except (OSError, json.JSONDecodeError, AttributeError) as exc:
                raise FrontendError(f"Cannot read tsconfig.json path aliases: {exc}") from exc
        aliases = []
        for key, value in (raw or {}).items():
            targets = [value] if isinstance(value, str) else list(value)
            if key.count("*") > 1 or any(not isinstance(target, str) or target.count("*") > 1 for target in targets):
                raise FrontendError(f"Unsupported tsconfig path alias: {key}")
            aliases.append((key, targets))
        return sorted(aliases, key=lambda item: len(item[0].replace("*", "")), reverse=True)

    def resolve(self, specifier: str, importer: str | os.PathLike[str] | None = None) -> ResolvedModule:
        if not specifier or "\x00" in specifier:
            raise FrontendError("Empty or invalid module specifier")
        importer_path = Path(importer).resolve() if importer else self.root / "__entry__.js"
        if specifier.startswith(("http://", "https://", "data:")):
            raise FrontendError(f"Remote or package-import specifier requires an explicit adapter: {specifier}")
        if specifier.startswith("#"):
            return self._resolve_package_import(specifier, importer_path)
        if specifier.startswith("node:") or specifier.split("/", 1)[0] in BROWSER_BUILTINS:
            raise FrontendError(f"Node.js built-in '{specifier}' is not available in browser modules")
        aliased = self._apply_alias(specifier)
        if aliased is not None:
            for candidate in aliased:
                found = self._resolve_path(self.base_url / candidate)
                if found:
                    self._assert_in_project(found)
                    return ResolvedModule(found, specifier)
            raise FrontendError(f"Path alias '{specifier}' did not resolve; checked: {', '.join(aliased)}")
        if specifier.startswith(("./", "../", "/")):
            base = self.root / specifier.lstrip("/") if specifier.startswith("/") else importer_path.parent / specifier
            found = self._resolve_path(base)
            if not found:
                raise FrontendError(f"Cannot resolve local import '{specifier}' from {importer_path}")
            self._assert_in_project(found)
            return ResolvedModule(found, specifier)
        return self._resolve_package(specifier, importer_path)

    def _resolve_package_import(self, specifier: str, importer: Path) -> ResolvedModule:
        for parent in (importer.parent, *importer.parents):
            if parent == self.root.parent:
                break
            manifest_path = parent / "package.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise FrontendError(f"Invalid package imports map: {manifest_path}") from exc
            imports = manifest.get("imports", {})
            target = imports.get(specifier) if isinstance(imports, dict) else None
            if target is None and isinstance(imports, dict):
                for key, value in imports.items():
                    if "*" not in key: continue
                    before, after = key.split("*", 1)
                    if specifier.startswith(before) and specifier.endswith(after):
                        middle = specifier[len(before):len(specifier) - len(after) if after else None]
                        selected = self._conditional(value)
                        target = selected.replace("*", middle) if selected else None
                        break
            target = self._conditional(target)
            if not target:
                raise FrontendError(f"Package does not define internal import '{specifier}'")
            if target.startswith("./"):
                package_root = parent.resolve()
                candidate = (package_root / target[2:]).resolve()
                try: candidate.relative_to(package_root)
                except ValueError as exc: raise FrontendError(f"Package import escapes package root: {specifier}") from exc
                found = self._resolve_path(candidate)
                if not found: raise FrontendError(f"Package import target is missing: {specifier}")
                self._assert_in_project(found)
                return ResolvedModule(found, specifier, package_root)
            return self.resolve(target, importer)
        raise FrontendError(f"No package.json imports map found for '{specifier}'")

    def _apply_alias(self, specifier: str) -> list[str] | None:
        for key, values in self.aliases:
            if "*" in key:
                before, after = key.split("*", 1)
                if specifier.startswith(before) and specifier.endswith(after):
                    middle = specifier[len(before):len(specifier) - len(after) if after else None]
                    return [value.replace("*", middle) for value in values]
            elif specifier == key:
                return values
        return None

    def _resolve_path(self, candidate: Path) -> Path | None:
        attempts = [candidate]
        if candidate.suffix:
            # TypeScript projects commonly author .js imports while storing .ts source.
            if candidate.suffix in (".js", ".mjs"):
                attempts.extend([candidate.with_suffix(".ts"), candidate.with_suffix(".tsx"), candidate.with_suffix(".jsx")])
        else:
            attempts.extend(candidate.with_suffix(ext) for ext in EXTENSIONS)
            attempts.extend(candidate / ("index" + ext) for ext in EXTENSIONS)
        for attempt in attempts:
            try:
                resolved = attempt.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if resolved.is_file():
                return resolved
        return None

    def _assert_in_project(self, path: Path) -> None:
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise FrontendError(f"Resolved module escapes project root: {path}") from exc

    def _resolve_package(self, specifier: str, importer: Path) -> ResolvedModule:
        parts = specifier.split("/")
        if specifier.startswith("@"):
            if len(parts) < 2:
                raise FrontendError(f"Invalid scoped package specifier: {specifier}")
            package_name, subpath = "/".join(parts[:2]), "/".join(parts[2:])
        else:
            package_name, subpath = parts[0], "/".join(parts[1:])
        package_root = None
        for parent in (importer, *importer.parents):
            try:
                parent.relative_to(self.root)
            except ValueError:
                continue
            candidate_root = parent / "node_modules" / package_name
            try:
                package_root = candidate_root.resolve(strict=True)
                break
            except (OSError, RuntimeError):
                continue
        if package_root is None:
            raise FrontendError(f"Package '{package_name}' is not installed in the frontend project's node_modules")
        self._assert_in_project(package_root)
        manifest_path = package_root / "package.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        except (OSError, json.JSONDecodeError) as exc:
            raise FrontendError(f"Invalid package manifest for '{package_name}': {exc}") from exc
        target = self._export_target(manifest.get("exports"), "." + ("/" + subpath if subpath else ""))
        if manifest.get("exports") is not None and target is None:
            raise FrontendError(f"Package '{package_name}' does not export '{subpath or '.'}'")
        if target is None and subpath:
            target = "./" + subpath
        if target is None:
            browser = manifest.get("browser")
            if isinstance(browser, str):
                target = browser
            target = target or manifest.get("module") or manifest.get("main") or "index.js"
        if not isinstance(target, str):
            raise FrontendError(f"Package '{package_name}' has no browser/import entry for '{specifier}'")
        candidate = (package_root / target.removeprefix("./")).resolve()
        try:
            candidate.relative_to(package_root)
        except ValueError as exc:
            raise FrontendError(f"Package export escapes package root: {specifier}") from exc
        found = self._resolve_path(candidate)
        if not found:
            raise FrontendError(f"Package export for '{specifier}' points to a missing file: {candidate}")
        self._assert_in_project(found)
        return ResolvedModule(found, specifier, package_root)

    def _export_target(self, exports: Any, subpath: str) -> str | None:
        if exports is None:
            return None
        if isinstance(exports, str):
            return exports if subpath == "." else None
        if isinstance(exports, list):
            for value in exports:
                selected = self._export_target(value, subpath)
                if selected: return selected
            return None
        if not isinstance(exports, dict):
            return None
        subpath_map = any(str(key).startswith(".") for key in exports)
        if subpath_map:
            if subpath in exports:
                return self._conditional(exports[subpath])
            for key, value in exports.items():
                if "*" not in key: continue
                before, after = key.split("*", 1)
                if subpath.startswith(before) and subpath.endswith(after):
                    middle = subpath[len(before):len(subpath) - len(after) if after else None]
                    resolved = self._conditional(value)
                    return resolved.replace("*", middle) if resolved else None
            return None
        return self._conditional(exports) if subpath == "." else None

    def _conditional(self, value: Any) -> str | None:
        if isinstance(value, str): return value
        if isinstance(value, list):
            for item in value:
                selected = self._conditional(item)
                if selected: return selected
        if isinstance(value, dict):
            for condition in ("browser", "import", "module", "default"):
                if condition in value:
                    selected = self._conditional(value[condition])
                    if selected: return selected
        return None


class DependencyGraph:
    """Cycle-safe import graph with reverse edges and dependency invalidation."""
    def __init__(self, resolver: ModuleResolver, *, max_modules: int = 10_000) -> None:
        self.resolver = resolver
        self.max_modules = max_modules
        self.edges: dict[Path, set[Path]] = {}
        self.reverse: dict[Path, set[Path]] = {}
        self.bare_imports: dict[Path, set[str]] = {}
        self.diagnostics: dict[Path, tuple[str, ...]] = {}

    def build(self, entry: str | os.PathLike[str], *, transform=None) -> dict[Path, set[Path]]:
        resolved_entry = self.resolver._resolve_path(self.resolver.root / entry)
        if not resolved_entry:
            raise FrontendError(f"Frontend entry not found: {entry}")
        self.resolver._assert_in_project(resolved_entry)
        pending = [resolved_entry]
        visited: set[Path] = set()
        while pending:
            current = pending.pop()
            if current in visited: continue
            if len(visited) >= self.max_modules:
                raise FrontendError(f"Dependency graph exceeds {self.max_modules} modules")
            visited.add(current)
            try:
                source = transform(current) if transform else current.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise FrontendError(f"Cannot read frontend module {current}: {exc}") from exc
            local_edges: set[Path] = set()
            bare: set[str] = set()
            if current.suffix.lower() == ".css":
                references = scan_css_dependencies(source)
                dependencies = [(item.specifier, item.kind) for item in references]
                self.diagnostics[current] = ()
            elif current.suffix.lower() in GRAPH_SOURCE_EXTENSIONS:
                try:
                    scan = scan_imports(source)
                except ValueError as exc:
                    raise FrontendError(f"Cannot scan imports in {current}: {exc}") from exc
                self.diagnostics[current] = scan.diagnostics
                dependencies = [(item.specifier, item.kind) for item in scan.imports]
            else:
                self.diagnostics[current] = ()
                dependencies = []
            for specifier, _kind in dependencies:
                if specifier.startswith(("http://", "https://", "data:")):
                    continue
                if not specifier.startswith(("./", "../", "/", "#")):
                    bare.add(specifier)
                dependency = self.resolver.resolve(specifier, current).path
                local_edges.add(dependency)
                if dependency.suffix.lower() in GRAPH_SOURCE_EXTENSIONS:
                    pending.append(dependency)
            for stale_dependency in self.edges.get(current, set()) - local_edges:
                self.reverse.get(stale_dependency, set()).discard(current)
            self.edges[current] = local_edges
            self.bare_imports[current] = bare
            for dependency in local_edges:
                self.reverse.setdefault(dependency, set()).add(current)
        return {path: set(deps) for path, deps in self.edges.items() if path in visited}

    def invalidate(self, changed: str | os.PathLike[str]) -> set[Path]:
        path = Path(changed).resolve()
        affected: set[Path] = set()
        pending = [path]
        while pending:
            current = pending.pop()
            if current in affected: continue
            affected.add(current)
            pending.extend(self.reverse.get(current, ()))
        for module in affected:
            for dependency in self.edges.pop(module, set()):
                self.reverse.get(dependency, set()).discard(module)
            self.reverse.pop(module, None)
            self.bare_imports.pop(module, None)
            self.diagnostics.pop(module, None)
        return affected


class FrontendCompiler:
    """Orchestrate optional local compiler binaries; never installs tools."""
    def __init__(self, project_root: str | os.PathLike[str]) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=True)
        local = self.root / "node_modules" / ".bin" / ("esbuild.cmd" if os.name == "nt" else "esbuild")
        self.esbuild = str(local) if local.exists() else shutil.which("esbuild")
        self.node = shutil.which("node")

    def doctor(self) -> dict[str, Any]:
        found = {"esbuild": self.esbuild, "node": self.node}
        for package in ("typescript", "@vue/compiler-sfc", "svelte", "react", "react-dom", "vue"):
            found[package] = self._node_package_path(package)
        return found

    def transform(self, path: str | os.PathLike[str], *, jsx_import_source: str = "react") -> str:
        source_path = Path(path).resolve(strict=True)
        try: source_path.relative_to(self.root)
        except ValueError as exc: raise FrontendError("Source module is outside the frontend project") from exc
        suffix = source_path.suffix.lower()
        if suffix in (".js", ".mjs", ".cjs", ".mjs"):
            source = source_path.read_text(encoding="utf-8")
            scan_source = re.sub(r"/\*[\s\S]*?\*/", "", source)
            scan_source = re.sub(r"(?m)^\s*//.*$", "", scan_source)
            if re.search(r"\bmodule\.exports\b|^\s*exports\.[A-Za-z_$][\w$]*\s*=|\brequire\s*\(", scan_source, re.MULTILINE):
                raise FrontendError(f"CommonJS module {source_path.name} needs a bundler; direct browser-module serving is not supported yet")
            return source
        if suffix in (".vue", ".svelte"):
            original = source_path.read_text(encoding="utf-8")
            source = self._compile_sfc(source_path)
            if self.esbuild and re.search(r"<script\b[^>]*\blang\s*=\s*(['\"])ts\1", original):
                return self._esbuild_stdin(source, "tsx", source_path, jsx_import_source)
            return source
        loader = {".ts": "ts", ".tsx": "tsx", ".jsx": "jsx", ".json": "json"}.get(suffix)
        if loader in ("ts", "tsx", "jsx"):
            adapter = EsbuildAdapter(self.root, executable=self.esbuild)
            try:
                return adapter.transform(source_path).code
            except AdapterError as exc:
                raise FrontendError(str(exc)) from exc
        if loader == "json" and self.esbuild:
            args = [self.esbuild, str(source_path), "--loader:.json=json", "--format=esm", "--target=es2020", "--log-level=error"]
            try:
                result = subprocess.run(args, cwd=self.root, text=True, capture_output=True, timeout=30, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise FrontendError(f"esbuild could not compile {source_path.name}: {exc}") from exc
            if result.returncode:
                raise FrontendError("esbuild compilation failed:\\n" + result.stderr.strip())
            return result.stdout
        raise FrontendError(f"No frontend transformer is configured for {suffix or 'extensionless'} files")

    def _esbuild_stdin(self, source: str, loader: str, source_path: Path, jsx_import_source: str) -> str:
        args = [self.esbuild, "--loader=" + loader, "--format=esm", "--target=es2020", "--jsx=automatic",
                "--jsx-import-source=" + jsx_import_source, "--sourcefile=" + source_path.name, "--log-level=error"]
        try:
            result = subprocess.run(args, cwd=self.root, input=source, text=True, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FrontendError(f"esbuild could not compile {source_path.name}: {exc}") from exc
        if result.returncode: raise FrontendError("esbuild compilation failed:\n" + result.stderr.strip())
        return result.stdout

    def _compile_sfc(self, source_path: Path) -> str:
        if not self.node:
            raise FrontendError(f"Compiling {source_path.suffix} requires Node.js and its optional framework compiler")
        package = "@vue/compiler-sfc" if source_path.suffix == ".vue" else "svelte"
        if not self._node_package_path(package):
            install_name = "@vue/compiler-sfc" if source_path.suffix == ".vue" else "svelte"
            raise FrontendError(f"{source_path.suffix} compilation requires optional '{package}'. Install it in the frontend project with `npm install --save-dev {install_name}`.")
        bridge = Path(__file__).with_name("sfc_bridge.cjs")
        try:
            result = subprocess.run([self.node, str(bridge), str(source_path)], cwd=self.root,
                                    text=True, capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FrontendError(f"Framework compiler failed: {exc}") from exc
        if result.returncode:
            raise FrontendError(f"{package} compilation failed:\n{result.stderr.strip()}")
        return result.stdout

    def _node_package_path(self, package: str) -> str | None:
        for start in (self.root, *self.root.parents):
            manifest = start / "node_modules" / package / "package.json"
            if manifest.is_file(): return str(manifest.parent)
        return None


class FrontendDevServer:
    """Serve a local source tree and compiler output under one URL prefix."""
    def __init__(self, project_root: str | os.PathLike[str], *, url_prefix: str = "/__limbo") -> None:
        self.root = Path(project_root).expanduser().resolve(strict=True)
        normalized_prefix = url_prefix.strip("/")
        if normalized_prefix and not re.fullmatch(r"[A-Za-z0-9/_-]+", normalized_prefix):
            raise ValueError("Frontend URL prefix may contain only letters, digits, slash, underscore, and hyphen")
        self.prefix = "/" + normalized_prefix
        if self.prefix == "/": raise ValueError("Frontend URL prefix cannot be root")
        self.resolver = ModuleResolver(self.root)
        self.graph = DependencyGraph(self.resolver)
        self.compiler = FrontendCompiler(self.root)
        self.cache = TransformCache(self.root)
        self._allowed_modules: set[Path] = set()

    def import_map(self, entry: str) -> dict[str, Any]:
        graph = self.graph.build(entry, transform=self._source_for_graph)
        imports = {}
        self._allowed_modules.update(graph)
        for dependencies in graph.values():
            self._allowed_modules.update(dependencies)
        for source in graph:
            for specifier in self.graph.bare_imports.get(source, ()):
                target = self.resolver.resolve(specifier, source).path
                imports[specifier] = self._module_url(target)
        return {"imports": imports}

    def script_tag(self, entry: str) -> str:
        import_map = json.dumps(self.import_map(entry), separators=(",", ":")).replace("</", "<\\/")
        source = self.prefix + "/" + quote(entry.lstrip("/"), safe="/@._-")
        return (f'<script type="importmap">{import_map}</script>\n'
                f'<script type="module" src="{html.escape(source, quote=True)}"></script>\n'
                f'<script src="{html.escape(self.prefix + "/_client.js", quote=True)}" defer></script>')

    def serve(self, request: Request) -> Response:
        if not (request.path == self.prefix or request.path.startswith(self.prefix + "/")):
            return Response.text("Not Found", 404)
        relative = request.path[len(self.prefix):].lstrip("/")
        try:
            if relative == "_importmap.json":
                entry = request.query_params.get("entry", [""])[0]
                if not entry: return Response.json({"error": "Missing entry query parameter"}, 400)
                return Response.json(self.import_map(entry), headers={"Cache-Control": "no-store"})
            if relative == "_version":
                return Response.json({"version": self._version_token()}, headers={"Cache-Control": "no-store"})
            if relative == "_client.js":
                client = ("(()=>{let version=null;const poll=async()=>{try{const r=await fetch(" + json.dumps(self.prefix + "/_version") + ",{cache:'no-store'});if(!r.ok)return;const d=await r.json();if(version===null)version=d.version;else if(version!==d.version)location.reload()}catch(_){}};poll();setInterval(poll,1200)})();")
                return Response(client, 200, {"Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "no-store"})
            if relative.startswith("_css/"):
                css_relative = unquote(relative[len("_css/"):])
                css_path = (self.root / css_relative).resolve(strict=True)
                if css_path not in self._allowed_modules or css_path.suffix.lower() != ".css":
                    return Response.text("CSS module not found", 404)
                css_url = self._module_url(css_path)
                js = ("const response = await fetch(" + json.dumps(css_url) + ");\n"
                      "if (!response.ok) throw new Error(`Limbo CSS load failed: ${response.status}`);\n"
                      "const css = await response.text();\n"
                      "const style = document.createElement('style'); style.textContent = css; document.head.appendChild(style);\n")
                return Response(js, 200, {"Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "no-store"})
            if relative.startswith("_pkgfile/"):
                decoded_package_path = unquote(relative[len("_pkgfile/"):])
                module_path = (self.root / decoded_package_path).resolve(strict=True)
                if not module_path.is_relative_to(self.root / "node_modules") or module_path not in self._allowed_modules:
                    return Response.text("Frontend module not found", 404)
                content = self._rewrite_internal_imports(self._transform_cached(module_path), module_path)
                return Response(content, 200, {"Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "no-store"})
            decoded = unquote(relative)
            if any(part.startswith(".") or part == "node_modules" for part in Path(decoded).parts):
                return Response.text("Frontend path is not publicly served", 404)
            candidate = self.resolver._resolve_path(self.root / decoded)
            if not candidate:
                return Response.text("Frontend module not found", 404)
            try:
                resolved_relative = candidate.relative_to(self.root)
            except ValueError:
                return Response.text("Frontend path is not publicly served", 404)
            if any(part.startswith(".") or part == "node_modules" for part in resolved_relative.parts):
                return Response.text("Frontend path is not publicly served", 404)
            if candidate.suffix.lower() in (".js", ".mjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte"):
                content = self._rewrite_internal_imports(self._transform_cached(candidate), candidate)
                return Response(content, 200, {"Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "no-store"})
            if candidate.suffix.lower() not in (".css", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico",
                                                   ".woff", ".woff2", ".ttf", ".wasm", ".mp3", ".mp4"):
                return Response.text("Frontend asset type is not publicly served", 404)
            return Response(candidate.read_bytes(), 200, {"Content-Type": _mime(candidate), "Cache-Control": "no-store"})
        except (FrontendError, ValueError, OSError) as exc:
            return Response.text(str(exc), 422)

    def _version_token(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(self._allowed_modules, key=lambda item: item.as_posix()):
            try:
                stat = path.stat()
            except OSError:
                digest.update((path.as_posix() + "|missing").encode("utf-8"))
                continue
            digest.update(path.as_posix().encode("utf-8") + b"\\0")
            try:
                with path.open("rb") as source:
                    for chunk in iter(lambda: source.read(64 * 1024), b""):
                        digest.update(chunk)
            except OSError:
                digest.update(b"|unreadable")
            digest.update(b"\\n")
        return digest.hexdigest()

    def _rewrite_internal_imports(self, code: str, importer: Path) -> str:
        try:
            scan = scan_imports(code)
        except ValueError as exc:
            raise FrontendError(f"Cannot rewrite imports in {importer}: {exc}") from exc
        replacements = []
        for reference in scan.imports:
            specifier = reference.specifier
            if specifier.startswith("#"):
                target = self.resolver.resolve(specifier, importer).path
                replacement = self._module_url(target)
            elif specifier.lower().endswith(".css"):
                if reference.kind != "side-effect":
                    raise FrontendError(
                        f"CSS imports with bindings are not supported yet in {importer.name}; use a side-effect import"
                    )
                target = self.resolver.resolve(specifier, importer).path
                self._allowed_modules.add(target)
                relative = target.relative_to(self.root).as_posix()
                replacement = self.prefix + "/_css/" + quote(relative, safe="/@._-")
            else:
                continue
            replacements.append((reference.start, reference.end, json.dumps(replacement)))
        for start, end, replacement in reversed(replacements):
            code = code[:start] + replacement + code[end:]
        return code

    def _transform_cached(self, path: Path) -> str:
        resolved = path.resolve(strict=True)
        self.resolver._assert_in_project(resolved)
        source = resolved.read_bytes()
        relative = resolved.relative_to(self.root).as_posix()
        tool_identity = "limbo-frontend-v1"
        for label, executable in (("esbuild", self.compiler.esbuild), ("node", self.compiler.node)):
            if executable:
                try:
                    stat = Path(executable).stat()
                    tool_identity += f"|{label}:{executable}:{stat.st_mtime_ns}:{stat.st_size}"
                except OSError:
                    tool_identity += f"|{label}:{executable}:unstatable"
        for package in ("@vue/compiler-sfc", "svelte"):
            manifest = self.root / "node_modules" / package / "package.json"
            if manifest.is_file():
                try:
                    tool_identity += f"|{package}:" + hashlib.sha256(manifest.read_bytes()).hexdigest()
                except OSError:
                    tool_identity += f"|{package}:unreadable"
        options = {"path": relative, "suffix": resolved.suffix.lower(), "tool": tool_identity}
        key = self.cache.key(source, compiler="limbo-frontend-transform-v1", options=options)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        transformed = self.compiler.transform(resolved)
        self.cache.put(key, transformed)
        return transformed

    def _source_for_graph(self, path: Path) -> str:
        try:
            return self._transform_cached(path)
        except FrontendError as exc:
            if "CommonJS module" in str(exc):
                return path.read_text(encoding="utf-8")
            raise

    def _module_url(self, path: Path) -> str:
        resolved = path.resolve(strict=True)
        self.resolver._assert_in_project(resolved)
        self._allowed_modules.add(resolved)
        relative = resolved.relative_to(self.root).as_posix()
        if relative.startswith("node_modules/"):
            return self.prefix + "/_pkgfile/" + quote(relative, safe="/@._-")
        return self.prefix + "/" + quote(relative, safe="/@._-")


def _mime(path: Path) -> str:
    import mimetypes
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
