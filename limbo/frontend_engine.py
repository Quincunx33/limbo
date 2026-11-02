"""Stable frontend compiler adapter contract and capability manifest.

The core stays standard-library-only. Adapters are explicit local tools; this
module never installs packages or downloads executables.
"""
from __future__ import annotations

import json
import base64
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


class AdapterError(RuntimeError):
    """A compiler adapter could not process a source module."""


@dataclass(frozen=True)
class TransformResult:
    code: str
    loader: str
    dependencies: tuple[str, ...] = ()
    source_map: str | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdapterCapability:
    adapter_id: str
    extensions: tuple[str, ...]
    available: bool
    requires: tuple[str, ...] = ()
    source_maps: bool = False
    hmr: bool = False
    status: str = "experimental"
    reason: str = ""


@runtime_checkable
class CompilerAdapter(Protocol):
    """Contract implemented by every local compiler integration."""

    capability: AdapterCapability

    def detect(self) -> bool:
        """Return whether all required local tools are available."""

    def transform(self, source_path: Path, *, source: str | None = None) -> TransformResult:
        """Transform one source file or raise AdapterError."""


class BrowserNativeAdapter:
    """Pass through browser-native JavaScript modules and CSS."""

    capability = AdapterCapability(
        adapter_id="browser-native",
        extensions=(".js", ".mjs", ".css"),
        available=True,
        status="supported",
        reason="Served without a compiler; imports are resolved by Limbo's dev server.",
    )

    def detect(self) -> bool:
        return True

    def transform(self, source_path: Path, *, source: str | None = None) -> TransformResult:
        path = Path(source_path)
        if path.suffix.lower() not in self.capability.extensions:
            raise AdapterError(f"browser-native adapter does not handle {path.suffix or 'extensionless'} files")
        try:
            code = source if source is not None else path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise AdapterError(f"cannot read {path}: {exc}") from exc
        if path.suffix.lower() in (".js", ".mjs"):
            scan_source = re.sub(r"/\*[\s\S]*?\*/", "", code)
            scan_source = re.sub(r"(?m)^\s*//.*$", "", scan_source)
            if re.search(r"\bmodule\.exports\b|^\s*exports\.[A-Za-z_$][\w$]*\s*=|\brequire\s*\(", scan_source, re.MULTILINE):
                raise AdapterError(
                    f"CommonJS module {path.name} needs a bundler; direct browser-module serving is not supported yet"
                )
        loader = "css" if path.suffix.lower() == ".css" else "js"
        return TransformResult(code=code, loader=loader)


class EsbuildAdapter:
    """Optional local esbuild transform for TypeScript and JSX-family files."""

    capability = AdapterCapability(
        adapter_id="esbuild",
        extensions=(".ts", ".tsx", ".jsx"),
        available=False,
        requires=("esbuild",),
        source_maps=True,
        hmr=False,
        status="optional",
        reason="Requires a locally installed esbuild executable; no automatic installation.",
    )

    def __init__(self, project_root: str | Path, executable: str | None = None, *, jsx_import_source: str = "react") -> None:
        self.root = Path(project_root).expanduser().resolve()
        local_name = "esbuild.cmd" if __import__("os").name == "nt" else "esbuild"
        local = self.root / "node_modules" / ".bin" / local_name
        self.executable = executable or (str(local) if local.is_file() else shutil.which("esbuild"))
        self.jsx_import_source = jsx_import_source

    def detect(self) -> bool:
        return bool(self.executable)

    def transform(self, source_path: Path, *, source: str | None = None) -> TransformResult:
        path = Path(source_path).expanduser().resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise AdapterError("esbuild source is outside the frontend project") from exc
        suffix = path.suffix.lower()
        loaders = {".ts": "ts", ".tsx": "tsx", ".jsx": "jsx"}
        loader = loaders.get(suffix)
        if loader is None:
            raise AdapterError(f"esbuild adapter does not handle {suffix or 'extensionless'} files")
        if not self.executable:
            raise AdapterError(
                "esbuild is not installed locally; install it in the project with "
                "`npm install --save-dev esbuild` to enable this adapter"
            )
        if source is None:
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise AdapterError(f"cannot read {path}: {exc}") from exc
        args = [self.executable, "--loader=" + loader, "--format=esm", "--target=es2020",
                "--jsx=automatic", "--jsx-import-source=" + self.jsx_import_source, "--sourcemap=inline", "--sourcefile=" + path.name, "--log-level=error"]
        try:
            result = subprocess.run(
                args, cwd=self.root, input=source, text=True, capture_output=True,
                timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdapterError(f"esbuild could not compile {path.name}: {exc}") from exc
        if result.returncode:
            raise AdapterError(result.stderr.strip() or f"esbuild exited with status {result.returncode}")
        source_map = None
        match = re.search(r"sourceMappingURL=data:application/json;base64,([A-Za-z0-9+/=]+)", result.stdout)
        if match:
            try:
                source_map = base64.b64decode(match.group(1), validate=True).decode("utf-8")
            except (ValueError, UnicodeError):
                raise AdapterError("esbuild returned an invalid inline source map")
        return TransformResult(code=result.stdout, loader="js", source_map=source_map)


class FrameworkSFCAdapter:
    """Optional Vue/Svelte SFC adapter backed by the project's local compiler."""

    def __init__(self, project_root: str | Path, framework: str) -> None:
        if framework not in {"vue", "svelte"}:
            raise ValueError("framework must be 'vue' or 'svelte'")
        self.root = Path(project_root).expanduser().resolve()
        self.framework = framework
        package = "@vue/compiler-sfc" if framework == "vue" else "svelte"
        self.capability = AdapterCapability(
            adapter_id=framework,
            extensions=(".vue",) if framework == "vue" else (".svelte",),
            available=False,
            requires=("node", package),
            status="optional",
            reason=f"Requires Node.js and the local {package} package; Limbo does not install it.",
        )

    def detect(self) -> bool:
        import shutil
        from .frontend import FrontendCompiler
        compiler = FrontendCompiler(self.root)
        package = "@vue/compiler-sfc" if self.framework == "vue" else "svelte"
        return bool(compiler.node and compiler._node_package_path(package))

    def transform(self, source_path: Path, *, source: str | None = None) -> TransformResult:
        path = Path(source_path).expanduser().resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise AdapterError("SFC source is outside the frontend project") from exc
        if path.suffix.lower() not in self.capability.extensions:
            raise AdapterError(f"{self.framework} adapter does not handle {path.suffix or 'extensionless'} files")
        if source is not None:
            try:
                path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise AdapterError(f"cannot read SFC source {path}: {exc}") from exc
            if path.read_text(encoding="utf-8") != source:
                raise AdapterError("SFC adapter currently requires source to match the on-disk file")
        if not self.detect():
            raise AdapterError(self.capability.reason)
        from .frontend import FrontendCompiler, FrontendError
        try:
            code = FrontendCompiler(self.root).transform(path)
        except FrontendError as exc:
            raise AdapterError(str(exc)) from exc
        return TransformResult(code=code, loader="js")


class AdapterRegistry:
    """Deterministic adapter selection and JSON-serializable capability report."""

    def __init__(self, adapters: tuple[CompilerAdapter, ...] = ()) -> None:
        self._adapters: dict[str, CompilerAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: CompilerAdapter) -> None:
        capability = adapter.capability
        if capability.adapter_id in self._adapters:
            raise ValueError(f"duplicate frontend adapter: {capability.adapter_id}")
        self._adapters[capability.adapter_id] = adapter

    def select(self, extension: str) -> CompilerAdapter | None:
        normalized = extension.lower()
        if normalized and not normalized.startswith("."):
            normalized = "." + normalized
        for adapter in self._adapters.values():
            if normalized in adapter.capability.extensions and adapter.detect():
                return adapter
        return None

    def manifest(self) -> dict[str, object]:
        entries = []
        for adapter_id in sorted(self._adapters):
            adapter = self._adapters[adapter_id]
            capability = asdict(adapter.capability)
            # Keep the public manifest JSON-compatible even though the internal
            # capability model uses tuples for immutable, deterministic data.
            capability["extensions"] = list(capability["extensions"])
            capability["requires"] = list(capability["requires"])
            capability["available"] = bool(adapter.detect())
            entries.append(capability)
        return {"schema_version": 1, "adapters": entries}

    def manifest_json(self) -> str:
        return json.dumps(self.manifest(), indent=2, sort_keys=True)
