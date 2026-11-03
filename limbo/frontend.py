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
from .frontend_scanner import rewrite_import_meta_hot, scan_imports
from .frontend_cache import TransformCache
from .css_scanner import scan_css_dependencies
from .frontend_engine import (
    AdapterError,
    AdapterRegistry,
    BrowserNativeAdapter,
    EsbuildAdapter,
    FrameworkSFCAdapter,
)


EXTENSIONS = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".json", ".css", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".wasm", ".mp3", ".mp4")
GRAPH_SOURCE_EXTENSIONS = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".css"}
BROWSER_BUILTINS = {"assert", "buffer", "child_process", "crypto", "events", "fs", "http", "https",
                    "module", "net", "os", "path", "process", "stream", "timers", "tls", "url", "util", "zlib"}
_JS_RESERVED_WORDS = frozenset("await break case catch class const continue debugger default delete do else enum export extends false finally for function if implements import in instanceof interface let new null package private protected public return static super switch this throw true try typeof var void while with yield".split())
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
    def __init__(self, project_root: str | os.PathLike[str], *, aliases: dict[str, list[str] | str] | None = None,
                 conditions: tuple[str, ...] = ("browser", "import", "module", "default")) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Frontend project root must be a directory")
        if not conditions or any(not isinstance(condition, str) or not condition for condition in conditions):
            raise ValueError("Package resolution conditions must contain non-empty strings")
        self.conditions = tuple(dict.fromkeys(conditions))
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
            if not isinstance(imports, dict) or any(
                not isinstance(key, str) or not key.startswith("#") or key.count("*") > 1
                for key in imports
            ):
                raise FrontendError(f"Invalid package imports map: {manifest_path}")
            target_value = imports.get(specifier) if isinstance(imports, dict) else None
            if target_value is None and isinstance(imports, dict):
                patterns = sorted(
                    ((key, value) for key, value in imports.items() if "*" in key),
                    key=lambda item: (len(item[0].split("*", 1)[0]), len(item[0].split("*", 1)[1])),
                    reverse=True,
                )
                for key, value in patterns:
                    before, after = key.split("*", 1)
                    if specifier.startswith(before) and specifier.endswith(after):
                        middle = specifier[len(before):len(specifier) - len(after) if after else None]
                        target_value = (value, middle)
                        break
            wildcard_middle = None
            if isinstance(target_value, tuple) and len(target_value) == 2:
                target_value, wildcard_middle = target_value
            targets = self._conditional_candidates(target_value)
            if wildcard_middle is not None:
                targets = [target.replace("*", wildcard_middle) for target in targets]
            if not targets:
                raise FrontendError(f"Package does not define internal import '{specifier}'")
            package_root = parent.resolve()
            last_candidate = None
            for target in targets:
                if target.startswith("./"):
                    candidate = (package_root / target[2:]).resolve()
                    try: candidate.relative_to(package_root)
                    except ValueError as exc: raise FrontendError(f"Package import escapes package root: {specifier}") from exc
                    last_candidate = candidate
                    found = self._resolve_path(candidate)
                    if not found: continue
                    self._assert_in_project(found)
                    return ResolvedModule(found, specifier, package_root)
                try:
                    return self.resolve(target, importer)
                except FrontendError:
                    continue
            raise FrontendError(f"Package import target is missing: {specifier} ({last_candidate})")
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
        export_targets = self._export_target_candidates(
            manifest.get("exports"), "." + ("/" + subpath if subpath else "")
        )
        if manifest.get("exports") is not None and not export_targets:
            raise FrontendError(f"Package '{package_name}' does not export '{subpath or '.'}'")
        if not export_targets and subpath:
            export_targets = ["./" + subpath]
        if not export_targets:
            browser = manifest.get("browser")
            if isinstance(browser, str):
                export_targets = [browser]
            else:
                export_targets = [manifest.get("module") or manifest.get("main") or "index.js"]
        last_candidate: Path | None = None
        for target in export_targets:
            if manifest.get("exports") is None and isinstance(target, str) and not target.startswith("./"):
                target = "./" + target
            if not isinstance(target, str) or not target.startswith("./"):
                raise FrontendError(f"Invalid package export target for '{specifier}': {target!r}")
            candidate = (package_root / target[2:]).resolve()
            try:
                candidate.relative_to(package_root)
            except ValueError as exc:
                raise FrontendError(f"Package export escapes package root: {specifier}") from exc
            last_candidate = candidate
            found = self._resolve_path(candidate)
            if found:
                self._assert_in_project(found)
                return ResolvedModule(found, specifier, package_root)
        raise FrontendError(f"Package export for '{specifier}' points to a missing file: {last_candidate}")

    def _export_target_candidates(self, exports: Any, subpath: str) -> list[str]:
        if exports is None:
            return []
        if isinstance(exports, str):
            return [exports] if subpath == "." else []
        if isinstance(exports, list):
            candidates: list[str] = []
            for value in exports:
                candidates.extend(self._export_target_candidates(value, subpath))
            return candidates
        if not isinstance(exports, dict):
            return []
        keys = list(exports)
        if any(not isinstance(key, str) for key in keys):
            raise FrontendError("Package exports keys must be strings")
        subpath_keys = [key for key in keys if key.startswith(".")]
        condition_keys = [key for key in keys if not key.startswith(".")]
        if subpath_keys and condition_keys:
            raise FrontendError("Package exports cannot mix subpath and condition keys")
        if any(not key.startswith("./") and key != "." for key in subpath_keys):
            raise FrontendError("Package exports subpaths must start with './'")
        if any(key.count("*") > 1 for key in subpath_keys):
            raise FrontendError("Package exports patterns may contain only one '*'")
        subpath_map = bool(subpath_keys)
        if subpath_map:
            if subpath in exports:
                return self._conditional_candidates(exports[subpath])
            patterns = sorted(
                ((key, value) for key, value in exports.items() if "*" in key),
                key=lambda item: (
                    len(item[0].split("*", 1)[0]),
                    len(item[0].split("*", 1)[1]),
                ),
                reverse=True,
            )
            for key, value in patterns:
                if "*" not in key: continue
                before, after = key.split("*", 1)
                if subpath.startswith(before) and subpath.endswith(after):
                    middle = subpath[len(before):len(subpath) - len(after) if after else None]
                    return [target.replace("*", middle) for target in self._conditional_candidates(value)]
            return []
        return self._conditional_candidates(exports) if subpath == "." else []

    def _conditional_candidates(self, value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if value is None:
            raise FrontendError("Package target is blocked by a null export/imports target")
        if isinstance(value, list):
            candidates: list[str] = []
            for item in value:
                candidates.extend(self._conditional_candidates(item))
            return candidates
        if isinstance(value, dict):
            active = set(self.conditions)
            active.add("default")
            for condition, target in value.items():
                if condition in active:
                    return self._conditional_candidates(target)
        return []

    def _conditional(self, value: Any) -> str | None:
        if isinstance(value, str): return value
        if value is None:
            raise FrontendError("Package target is blocked by a null export/imports target")
        if isinstance(value, list):
            for item in value:
                selected = self._conditional(item)
                if selected: return selected
        if isinstance(value, dict):
            active = set(self.conditions)
            active.add("default")
            # Node evaluates condition objects in the order written in package.json;
            # condition names form the active set rather than a priority list.
            for condition, target in value.items():
                if condition in active:
                    selected = self._conditional(target)
                    if selected: return selected
        return None


class DependencyGraph:
    """Cycle-safe import graph with reverse edges and dependency invalidation."""
    def __init__(self, resolver: ModuleResolver, *, max_modules: int = 10_000,
                 allow_remote: bool = False) -> None:
        self.resolver = resolver
        self.max_modules = max_modules
        self.allow_remote = allow_remote
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
                composition_dependencies = re.findall(
                    r"(?:composes|compose-with)\s*:[^;{}]*?\s+from\s+[\"']([^\"']+)[\"']",
                    source,
                )
                dependencies.extend((specifier, "composition") for specifier in composition_dependencies)
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
            for specifier, kind in dependencies:
                if specifier.startswith(("http://", "https://", "data:")):
                    if not self.allow_remote:
                        raise FrontendError(
                            f"Remote frontend import '{specifier}' is disabled; install the package locally in node_modules"
                        )
                    continue
                if current.suffix.lower() == ".css" and kind == "asset" and not specifier.startswith("/"):
                    dependency = self.resolver._resolve_path(current.parent / specifier)
                    if not dependency:
                        raise FrontendError(f"CSS asset '{specifier}' in {current.name} was not found")
                    self.resolver._assert_in_project(dependency)
                else:
                    dependency = self.resolver.resolve(specifier, current).path
                if current.suffix.lower() != ".css" and not specifier.startswith(("./", "../", "/", "#")):
                    bare.add(specifier)
                local_edges.add(dependency)
                if dependency.suffix.lower() in GRAPH_SOURCE_EXTENSIONS:
                    pending.append(dependency)
            for stale_dependency in self.edges.get(current, set()) - local_edges:
                self.reverse.get(stale_dependency, set()).discard(current)
            self.edges[current] = local_edges
            self.bare_imports[current] = bare
            for dependency in local_edges:
                self.reverse.setdefault(dependency, set()).add(current)
        stale_modules = set(self.edges) - visited
        for stale_module in stale_modules:
            for dependency in self.edges.pop(stale_module, set()):
                self.reverse.get(dependency, set()).discard(stale_module)
            self.reverse.pop(stale_module, None)
            self.bare_imports.pop(stale_module, None)
            self.diagnostics.pop(stale_module, None)
        return {path: set(deps) for path, deps in self.edges.items() if path in visited}

    def refresh(self, entry: str | os.PathLike[str], changed: str | os.PathLike[str], *, transform=None) -> tuple[set[Path], dict[Path, set[Path]]]:
        """Invalidate a changed module and rebuild the reachable graph.

        This keeps reverse edges accurate when an edit removes an import or
        changes a module's extension, while returning both the affected set and
        the refreshed graph for dev-server consumers.
        """
        affected = self.invalidate(changed)
        return affected, self.build(entry, transform=transform)

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
            adapter = EsbuildAdapter(self.root, executable=self.esbuild, jsx_import_source=jsx_import_source)
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

    def _esbuild_stdin(self, source: str, loader: str, source_path: Path, jsx_import_source: str,
                       *, source_map: bool = False) -> str:
        args = [self.esbuild, "--loader=" + loader, "--format=esm", "--target=es2020", "--jsx=automatic",
                "--jsx-import-source=" + jsx_import_source, "--sourcefile=" + source_path.name, "--log-level=error"]
        if source_map:
            args.append("--sourcemap=inline")
        try:
            result = subprocess.run(args, cwd=self.root, input=source, text=True, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FrontendError(f"esbuild could not compile {source_path.name}: {exc}") from exc
        if result.returncode: raise FrontendError("esbuild compilation failed:\n" + result.stderr.strip())
        return result.stdout

    def _compile_sfc(self, source_path: Path) -> str:
        code, _source_map = self._compile_sfc_result(source_path)
        return code

    def _compile_sfc_result(self, source_path: Path) -> tuple[str, dict[str, Any] | str | None]:
        if not self.node:
            raise FrontendError(f"Compiling {source_path.suffix} requires Node.js and its optional framework compiler")
        package = "@vue/compiler-sfc" if source_path.suffix == ".vue" else "svelte"
        if not self._node_package_path(package):
            install_name = "@vue/compiler-sfc" if source_path.suffix == ".vue" else "svelte"
            raise FrontendError(f"{source_path.suffix} compilation requires optional '{package}'. Install it in the frontend project with `npm install --save-dev {install_name}`.")
        bridge = Path(__file__).with_name("sfc_bridge.cjs")
        try:
            result = subprocess.run([self.node, str(bridge), str(source_path), "--json"], cwd=self.root,
                                    text=True, capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FrontendError(f"Framework compiler failed: {exc}") from exc
        if result.returncode:
            raise FrontendError(f"{package} compilation failed:\n{result.stderr.strip()}")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise FrontendError(f"{package} compiler returned invalid JSON output") from exc
        code = payload.get("code")
        source_map = payload.get("sourceMap")
        if not isinstance(code, str):
            raise FrontendError(f"{package} compiler returned no JavaScript output")
        if source_map is not None and not isinstance(source_map, (dict, str)):
            raise FrontendError(f"{package} compiler returned an invalid source map")
        return code, source_map

    def _node_package_path(self, package: str) -> str | None:
        for start in (self.root, *self.root.parents):
            manifest = start / "node_modules" / package / "package.json"
            if manifest.is_file(): return str(manifest.parent)
        return None


class FrontendDevServer:
    """Serve a local source tree and compiler output under one URL prefix."""
    def __init__(self, project_root: str | os.PathLike[str], *, url_prefix: str = "/__limbo",
                 allow_remote: bool = False) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=True)
        normalized_prefix = url_prefix.strip("/")
        if normalized_prefix and not re.fullmatch(r"[A-Za-z0-9/_-]+", normalized_prefix):
            raise ValueError("Frontend URL prefix may contain only letters, digits, slash, underscore, and hyphen")
        self.prefix = "/" + normalized_prefix
        if self.prefix == "/": raise ValueError("Frontend URL prefix cannot be root")
        self.resolver = ModuleResolver(self.root)
        self.allow_remote = allow_remote
        self.graph = DependencyGraph(self.resolver, allow_remote=allow_remote)
        self.compiler = FrontendCompiler(self.root)
        self.registry = AdapterRegistry((
            BrowserNativeAdapter(),
            EsbuildAdapter(self.root, executable=self.compiler.esbuild),
            FrameworkSFCAdapter(self.root, "vue"),
            FrameworkSFCAdapter(self.root, "svelte"),
        ))
        self.cache = TransformCache(self.root)
        self._allowed_modules: set[Path] = set()
        self._last_version_snapshot: dict[str, str] | None = None

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
                f'<script src="{html.escape(self.prefix + "/_client.js", quote=True)}"></script>\n'
                f'<script type="module" src="{html.escape(source, quote=True)}"></script>')

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
                return Response.json(self._version_payload(), headers={"Cache-Control": "no-store"})
            if relative == "_client.js":
                client = r"""(()=>{
const modules=new Map();
const moduleId=url=>new URL(url,location.href).pathname;
const hot=url=>{
  const id=moduleId(url);let context=modules.get(id);
  if(!context){context={id,data:{},self:false,selfCallback:null,dependencies:new Map(),disposeCallback:null};modules.set(id,context)}
  context.accept=(dependencies,callback)=>{
    if(typeof dependencies==='function'||dependencies===undefined){context.self=true;context.selfCallback=typeof dependencies==='function'?dependencies:null;return}
    for(const dependency of (Array.isArray(dependencies)?dependencies:[dependencies]))context.dependencies.set(moduleId(new URL(dependency,url)),callback||null);
  };
  context.dispose=callback=>{context.disposeCallback=callback};return context;
};
window.__LIMBO_HMR_HOT__=hot;
const importFresh=(url,version)=>{const next=new URL(url,location.href);next.searchParams.set('__limbo_hmr',version);return import(next.href)};
const applyUpdate=async(url,version)=>{
  const id=moduleId(url);let handled=false;
  for(const context of modules.values()){
    if(context.id===id&&context.self){handled=true;const callback=context.selfCallback;const data=context.data;if(context.disposeCallback)await context.disposeCallback(data);const updated=await importFresh(id,version);if(callback)await callback(updated)}
    if(context.dependencies.has(id)){handled=true;const updated=await importFresh(id,version);const callback=context.dependencies.get(id);if(callback)await callback(updated)}
  }
  return handled;
};
const key='__limbo_hmr_state__';
const restore=()=>{try{const raw=sessionStorage.getItem(key);if(!raw)return;const state=JSON.parse(raw);sessionStorage.removeItem(key);window.__LIMBO_HMR_STATE__=state;window.dispatchEvent(new CustomEvent('limbo:hmr-ready',{detail:state}))}catch(_){}};
const preserve=()=>{try{if(window.__LIMBO_HMR_STATE__!==undefined)sessionStorage.setItem(key,JSON.stringify(window.__LIMBO_HMR_STATE__))}catch(_){}};
restore();let version=null;
const poll=async()=>{try{const response=await fetch("__VERSION_URL__",{cache:'no-store'});if(!response.ok)return;const data=await response.json();if(version===null){version=data.version;return}if(version===data.version)return;let fallback=false;for(const changed of data.changed||[]){if(!await applyUpdate(changed,data.version))fallback=true}if(fallback){preserve();location.reload()}else version=data.version}catch(_){preserve();location.reload()}};
poll();setInterval(poll,1200);
})();""".replace("__VERSION_URL__", self.prefix + "/_version")
                return Response(client, 200, {"Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "no-store"})
            if relative.startswith("_cssmodule/"):
                css_relative = unquote(relative[len("_cssmodule/"):])
                css_path = (self.root / css_relative).resolve()
                if css_path not in self._allowed_modules or css_path.suffix.lower() != ".css":
                    return Response.text("CSS module not found", 404)
                classes, _compiled_css = self._compile_css_module(css_path)
                encoded_classes = json.dumps(classes, separators=(",", ":"))
                css_url = self.prefix + "/_cssmodule-css/" + quote(css_relative, safe="/@._-")
                exports = "".join(
                    f"export const {name}=styles[{json.dumps(name)}];\n"
                    for name in classes if name.isidentifier() and name not in _JS_RESERVED_WORDS
                )
                js = (f"const styles=Object.freeze({encoded_classes});\n"
                      "const link=document.createElement('link');link.rel='stylesheet';link.href="
                      + json.dumps(css_url) + ";document.head.appendChild(link);\n"
                      + exports + "export default styles;\n")
                return Response(js, 200, {"Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "no-store"})
            if relative.startswith("_cssmodule-css/"):
                css_relative = unquote(relative[len("_cssmodule-css/"):])
                asset_path = (self.root / css_relative).resolve()
                if asset_path not in self._allowed_modules:
                    return Response.text("CSS module asset not found", 404)
                if asset_path.suffix.lower() == ".css":
                    _classes, compiled_css = self._compile_css_module(asset_path)
                    compiled_css = self._rewrite_css_imports(compiled_css, asset_path)
                    return Response(compiled_css, 200, {"Content-Type": "text/css; charset=utf-8", "Cache-Control": "no-store"})
                return Response(asset_path.read_bytes(), 200, {"Content-Type": _mime(asset_path), "Cache-Control": "no-store"})
            if relative.startswith("_css/"):
                css_relative = unquote(relative[len("_css/"):])
                # A missing or malformed URL is a normal not-found response,
                # not a frontend transform/configuration error.
                css_path = (self.root / css_relative).resolve()
                if css_path not in self._allowed_modules or css_path.suffix.lower() != ".css":
                    return Response.text("CSS module not found", 404)
                css_url = self._module_url(css_path)
                js = ("const link = document.createElement('link');\n"
                      "link.rel = 'stylesheet'; link.href = " + json.dumps(css_url) + ";\n"
                      "document.head.appendChild(link);\n")
                return Response(js, 200, {"Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "no-store"})
            if relative.startswith("_pkgfile/"):
                decoded_package_path = unquote(relative[len("_pkgfile/"):])
                module_path = (self.root / decoded_package_path).resolve(strict=True)
                if not module_path.is_relative_to(self.root / "node_modules") or module_path not in self._allowed_modules:
                    return Response.text("Frontend module not found", 404)
                if module_path.suffix.lower() in (".js", ".mjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte"):
                    content = self._rewrite_internal_imports(self._transform_cached(module_path), module_path)
                    return Response(content, 200, {"Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "no-store"})
                if module_path.suffix.lower() in (".css", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico",
                                                    ".woff", ".woff2", ".ttf", ".wasm", ".mp3", ".mp4"):
                    if module_path.suffix.lower() == ".css":
                        content = self._rewrite_css_imports(module_path.read_text(encoding="utf-8"), module_path)
                        return Response(content, 200, {"Content-Type": "text/css; charset=utf-8", "Cache-Control": "no-store"})
                    return Response(module_path.read_bytes(), 200, {"Content-Type": _mime(module_path), "Cache-Control": "no-store"})
                return Response.text("Frontend package asset type is not supported", 404)
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
                return Response.text("Frontend asset type is not supported", 404)
            if candidate.suffix.lower() == ".css":
                content = self._rewrite_css_imports(candidate.read_text(encoding="utf-8"), candidate)
                return Response(content, 200, {"Content-Type": "text/css; charset=utf-8", "Cache-Control": "no-store"})
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

    def _version_payload(self) -> dict[str, Any]:
        snapshot: dict[str, str] = {}
        for path in self._allowed_modules:
            resolved = path.resolve()
            relative = resolved.relative_to(self.root).as_posix()
            if relative.startswith("node_modules/"):
                url = self.prefix + "/_pkgfile/" + quote(relative, safe="/@._-")
            else:
                url = self.prefix + "/" + quote(relative, safe="/@._-")
            try:
                fingerprint = hashlib.sha256(resolved.read_bytes()).hexdigest()
            except OSError:
                fingerprint = "missing"
            snapshot[url] = fingerprint
        changed: list[str] = []
        previous = self._last_version_snapshot
        if previous is not None:
            changed = sorted(
                url for url in set(previous) | set(snapshot)
                if previous.get(url) != snapshot.get(url)
            )
        self._last_version_snapshot = snapshot
        digest = hashlib.sha256()
        for url, fingerprint in sorted(snapshot.items()):
            digest.update(url.encode("utf-8") + b"\0" + fingerprint.encode("ascii") + b"\n")
        return {"version": digest.hexdigest(), "changed": changed}

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
                target = self.resolver.resolve(specifier, importer).path
                self._allowed_modules.add(target)
                relative = target.relative_to(self.root).as_posix()
                if reference.kind == "side-effect":
                    replacement = self.prefix + "/_css/" + quote(relative, safe="/@._-")
                elif reference.kind == "static":
                    replacement = self.prefix + "/_cssmodule/" + quote(relative, safe="/@._-")
                else:
                    raise FrontendError(f"Unsupported CSS import form in {importer.name}")
            else:
                continue
            replacements.append((reference.start, reference.end, json.dumps(replacement)))
        for start, end, replacement in reversed(replacements):
            code = code[:start] + replacement + code[end:]
        return rewrite_import_meta_hot(code)

    def _compile_css_module(self, path: Path, _stack: tuple[Path, ...] = ()) -> tuple[dict[str, str], str]:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise FrontendError(f"Cannot read CSS module {path.name}: {exc}") from exc
        path = path.resolve()
        if path in _stack:
            chain = " -> ".join(item.name for item in (*_stack, path))
            raise FrontendError(f"CSS Modules composition cycle: {chain}")
        if re.search(r"(?m)^\s*@value\b", source):
            raise FrontendError(
                f"CSS Modules @value is not supported in {path.name}; use CSS custom properties or JavaScript exports"
            )
        relative = path.relative_to(self.root).as_posix()
        classes: dict[str, str] = {}
        ids: dict[str, str] = {}
        animations: dict[str, str] = {}
        class_pattern = re.compile(r"(?<![\w-])\.([A-Za-z_][\w-]*)")
        id_pattern = re.compile(r"(?<![\w-])#([A-Za-z_][\w-]*)")

        composition_pattern = re.compile(
            r"(?m)(?:^|[;{])\s*(?:composes|compose-with)\s*:\s*"
            r"(?P<names>[A-Za-z_][\w-]*(?:\s+[A-Za-z_][\w-]*)*)"
            r"(?:\s+from\s+(?P<source>['\"][^'\"]+['\"]))?\s*;"
        )

        # Capture the selector immediately preceding each composition block,
        # then remove only the declaration (not surrounding CSS whitespace).
        declaration_records: list[tuple[str, tuple[str, ...], str | None]] = []
        def remove_composition(match: re.Match[str]) -> str:
            before = source[:match.start()]
            owner = ""
            owner_match = re.search(r"\.([A-Za-z_][\w-]*)\s*$", before)
            if owner_match:
                owner = owner_match.group(1)
            source_specifier = match.group("source")
            if source_specifier:
                source_specifier = source_specifier[1:-1]
            declaration_records.append((owner, tuple(match.group("names").split()), source_specifier))
            delimiter = source[match.start()]
            return delimiter if delimiter in "{;" else ""

        source = composition_pattern.sub(remove_composition, source)

        def rewrite_selector(selector: str) -> str:
            if selector.lstrip().startswith("@"):
                return selector

            # Hide comments and CSS Modules' :global(...) escapes before local
            # class substitution, then restore them verbatim.  This prevents a
            # comment such as ``/* .not-a-class */`` from becoming an export.
            hidden: list[str] = []

            def hide(value: str) -> str:
                hidden.append(value)
                return f"\x00GLOBAL{len(hidden) - 1}\x00"

            selector = re.sub(r"/\*[\s\S]*?\*/", lambda match: hide(match.group(0)), selector)

            def hide_global(match: re.Match[str]) -> str:
                return hide(match.group(1))

            selector = re.sub(r":global\(([^()]*)\)", hide_global, selector)
            selector = re.sub(r":global\s+([^,\s>{]+)", hide_global, selector)
            # :local(...) is a scope marker, not part of the emitted CSS.
            selector = re.sub(r":local\(([^()]*)\)", r"\1", selector)
            selector = re.sub(r":local\s+", "", selector)

            def rename(class_match: re.Match[str]) -> str:
                name = class_match.group(1)
                renamed = classes.get(name)
                if renamed is None:
                    digest = hashlib.sha256(f"{relative}:{name}".encode("utf-8")).hexdigest()[:7]
                    renamed = f"{name}_{digest}"
                    classes[name] = renamed
                return "." + renamed

            rewritten = class_pattern.sub(rename, selector)

            def rename_id(id_match: re.Match[str]) -> str:
                name = id_match.group(1)
                renamed = ids.get(name)
                if renamed is None:
                    digest = hashlib.sha256(f"{relative}:id:{name}".encode("utf-8")).hexdigest()[:7]
                    renamed = f"{name}_{digest}"
                    ids[name] = renamed
                return "#" + renamed

            rewritten = id_pattern.sub(rename_id, rewritten)
            for index, value in enumerate(hidden):
                rewritten = rewritten.replace(f"\x00GLOBAL{index}\x00", value)
            return rewritten

        # Rewrite only selector preludes.  A regex over the whole stylesheet
        # can accidentally consume comments or miss selectors nested in
        # @media/@supports blocks.  This small brace-aware pass is not a CSS
        # parser, but it respects comments/strings and visits nested rules.
        replacements: list[tuple[int, int, str]] = []
        segment_start = 0
        i = 0
        quote: str | None = None
        while i < len(source):
            if source.startswith("/*", i):
                end = source.find("*/", i + 2)
                i = len(source) if end < 0 else end + 2
                continue
            char = source[i]
            if quote:
                if char == "\\":
                    i += 2
                    continue
                if char == quote:
                    quote = None
                i += 1
                continue
            if char in ("'", '"'):
                quote = char
                i += 1
                continue
            if char == "{":
                selector = source[segment_start:i]
                stripped = selector.lstrip()
                if stripped and not stripped.startswith("@"):
                    rewritten = rewrite_selector(selector)
                    if rewritten != selector:
                        replacements.append((segment_start, i, rewritten))
                segment_start = i + 1
            elif char == "}" or char == ";":
                segment_start = i + 1
            i += 1

        for start, end, replacement in reversed(replacements):
            source = source[:start] + replacement + source[end:]

        def animation_name(name: str) -> str:
            renamed = animations.get(name)
            if renamed is None:
                digest = hashlib.sha256(f"{relative}:animation:{name}".encode("utf-8")).hexdigest()[:7]
                renamed = f"{name}_{digest}"
                animations[name] = renamed
            return renamed

        # Keyframe names are a separate namespace from classes.  Rewrite the
        # declaration name and the identifiers in animation/animation-name
        # values, while leaving timing keywords, durations and CSS variables
        # untouched.  Global keyframe escapes remain global.
        keyframe_pattern = re.compile(r"(@(?:-webkit-)?keyframes\s+)([A-Za-z_][\w-]*)")
        source = keyframe_pattern.sub(lambda match: match.group(1) + animation_name(match.group(2)), source)

        def rewrite_animation_value(match: re.Match[str]) -> str:
            prefix, value = match.group(1), match.group(2)
            keywords = {"none", "initial", "inherit", "unset", "revert", "revert-layer", "linear", "ease",
                        "ease-in", "ease-out", "ease-in-out", "step-start", "step-end", "infinite", "normal",
                        "reverse", "alternate", "alternate-reverse", "forwards", "backwards", "both", "running", "paused"}
            parts = re.split(r"(\s+|,)", value)
            for index, part in enumerate(parts):
                if (part and not part.isspace() and part != "," and part in animations
                        and part.lower() not in keywords and not part.startswith("--")):
                    parts[index] = animations[part]
            return prefix + "".join(parts)

        source = re.sub(r"(\banimation(?:-name)?\s*:\s*)([^;{}]+)", rewrite_animation_value, source,
                        flags=re.IGNORECASE)

        # Apply composition after local names are known.  Composed values are
        # exported through the same default `styles` object; composition
        # declarations never reach the browser as invalid CSS.
        for owner, names, source_specifier in declaration_records:
            if not owner or owner not in classes:
                raise FrontendError(f"CSS Modules composition has no local owner in {path.name}")
            composed: list[str] = []
            if source_specifier == "global":
                composed.extend(names)
            elif source_specifier:
                try:
                    dependency = self.resolver.resolve(source_specifier, path).path
                except FrontendError as exc:
                    raise FrontendError(f"Cannot resolve composed CSS module '{source_specifier}' from {path.name}: {exc}") from exc
                if dependency.suffix.lower() != ".css":
                    raise FrontendError(f"CSS Modules composition source must be CSS: {source_specifier}")
                self._allowed_modules.add(dependency)
                imported_classes, _ = self._compile_css_module(dependency, (*_stack, path))
                for name in names:
                    if name not in imported_classes:
                        raise FrontendError(f"CSS Modules class '{name}' is not exported by {source_specifier}")
                    composed.append(imported_classes[name])
            else:
                for name in names:
                    if name not in classes:
                        raise FrontendError(f"CSS Modules class '{name}' is not defined in {path.name}")
                    composed.append(classes[name])
            classes[owner] = " ".join(dict.fromkeys((classes[owner], *composed)))

        return classes, source

    def _rewrite_css_imports(self, source: str, importer: Path) -> str:
        replacements: list[tuple[int, int, str]] = []
        for reference in scan_css_dependencies(source):
            if reference.kind != "import" or reference.specifier.startswith(("./", "../", "/", "#", "data:", "http://", "https://")):
                continue
            target = self.resolver.resolve(reference.specifier, importer).path
            self._allowed_modules.add(target)
            replacements.append((reference.start, reference.end, self._module_url(target)))
        for start, end, replacement in reversed(replacements):
            source = source[:start] + replacement + source[end:]
        return source

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
        adapter = self.registry.select(resolved.suffix)
        if adapter is not None:
            try:
                transformed = adapter.transform(resolved).code
            except AdapterError as exc:
                raise FrontendError(str(exc)) from exc
        else:
            # JSON and future non-adapter assets retain the compiler's explicit
            # fallback diagnostics rather than being silently passed through.
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
