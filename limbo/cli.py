"""Command-line interface for Limbo."""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .frontend import FrontendCompiler
from .frontend_engine import AdapterRegistry, BrowserNativeAdapter, EsbuildAdapter, FrameworkSFCAdapter
from .config import ConfigError, load_config
from .project import ProjectMetadataError, edit_package_metadata, preview_directory
from .build_engine import BuildError, build_project
from .tunnel import (
    TunnelError, cloudflared_version, detect_cloudflared, exposure_warnings, missing_tool_message, plan_delete, prepare_quick_tunnel,
)


def _create(name: str, template: str = "python-api") -> int:
    target = Path(name).expanduser()
    if template not in {"python-api", "static", "browser-native", "vue"}:
        print(f"Error: unknown template {template!r}.", file=sys.stderr)
        return 2
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        print(f"Error: {target} already exists and is not an empty directory.", file=sys.stderr)
        return 2
    target.mkdir(parents=True, exist_ok=True)
    for folder in ("static", "templates", "uploads", "tests", "frontend/src", "frontend/public"):
        (target / folder).mkdir(parents=True, exist_ok=True)
    (target / "limbo.toml").write_text('[app]\nname = "My Limbo app"\ndebug = true\n\n[server]\nhost = "127.0.0.1"\nport = 8000\n', encoding="utf-8")
    (target / "requirements.txt").write_text("# Limbo core has no third-party dependencies.\n", encoding="utf-8")
    if template == "static":
        (target / "dist").mkdir(exist_ok=True)
        (target / "dist" / "index.html").write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Limbo static site</title><body><main><h1>Hello from Limbo</h1><p>Your static site is ready.</p></main></body></html>\n', encoding="utf-8")
        readme = "Run limbo preview to serve the included dist/ directory. Edit dist/index.html to customize the site.\n"
    else:
        (target / "frontend" / "src" / "main.js").write_text('const root = document.querySelector("#app");\nroot.textContent = "Frontend modules are served locally by Limbo.";\n', encoding="utf-8")
        if template == "vue":
            (target / "frontend" / "src" / "main.js").write_text('import { createApp } from "vue";\nimport App from "./App.vue";\ncreateApp(App).mount("#app");\n', encoding="utf-8")
            (target / "frontend" / "src" / "App.vue").write_text('<script setup>\nimport { ref } from "vue";\nconst count = ref(0);\n</script>\n<template><main><h1>Limbo + Vue</h1><button @click="count++">Count: {{ count }}</button></main></template>\n', encoding="utf-8")
            (target / "package.json").write_text('{\n  "name": "limbo-vue-starter",\n  "private": true,\n  "dependencies": {\n    "vue": "*"\n  },\n  "devDependencies": {\n    "@vue/compiler-sfc": "*"\n  }\n}\n', encoding="utf-8")
            readme = "Install the declared frontend packages with your preferred package manager, then run python app.py. Limbo never installs packages automatically.\n"
        else:
            readme = "Run python app.py from this directory. The browser-native frontend has no Node.js requirement.\n"
        app_source = (
            "from limbo import Limbo\n\napp = Limbo(name=\"My Limbo app\")\napp.enable_frontend(\"frontend\")\n\n"
            "@app.get(\"/\")\ndef home(req):\n"
            "    tags = app.frontend_script(\"src/main.js\")\n"
            "    return app.html(\"<!doctype html><html><head><meta charset='utf-8'><title>Limbo</title></head>\"\n"
            "                    \"<body><h1>Hello from Limbo</h1><div id='app'>Loading…</div>\" + tags + \"</body></html>\")\n\n"
            "@app.get(\"/api/health\")\ndef health(req):\n    return {\"ok\": True}\n\n"
            "app.static(\"/static\", \"static\")\n\nif __name__ == \"__main__\":\n    app.run()\n"
        )
        (target / "app.py").write_text(app_source, encoding="utf-8")
    (target / "README.md").write_text(f"# {target.name}\n\nTemplate: {template}\n\n{readme}", encoding="utf-8")
    print(f"Created {target} using the {template} template.")
    return 0


def _load_app() -> object:
    path = Path.cwd() / "app.py"
    if not path.is_file():
        raise FileNotFoundError("No app.py found in the current directory. Use `limbo create my-app` first.")
    spec = importlib.util.spec_from_file_location("limbo_user_app", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    app = getattr(module, "app", None)
    if app is None or not callable(getattr(app, "run", None)):
        raise TypeError("app.py must define `app = Limbo(...)`")
    return app


def _tool_status(executable: str, version_args: tuple[str, ...]) -> str:
    path = shutil.which(executable)
    if not path:
        return "not found (optional)"
    try:
        result = subprocess.run([path, *version_args], capture_output=True, text=True, timeout=3, check=False)
        lines = (result.stdout or result.stderr).strip().splitlines()
        return f"{path} ({lines[0] if lines else 'version unavailable'})"
    except (OSError, subprocess.SubprocessError):
        return f"{path} (version unavailable)"


def _frontend_tool_report() -> list[str]:
    try:
        capabilities = FrontendCompiler(".").doctor()
    except (OSError, ValueError) as exc:
        return [f"Frontend tools: unavailable ({exc})"]
    report = []
    for name, location in capabilities.items():
        if not location:
            report.append(f"{name}: not found (optional)")
            continue
        if name in {"node", "esbuild"}:
            report.append(f"{name}: {_tool_status(location, ('--version',))}")
            continue
        manifest = Path(location) / "package.json"
        try:
            version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
            version = None
        report.append(f"{name}: {location}" + (f" (v{version})" if version else " (version unavailable)"))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="limbo", description="Python-first, zero-dependency web framework")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="create a starter project"); create.add_argument("name"); create.add_argument("--template", choices=("python-api", "static", "browser-native", "vue"), default="python-api")
    dev = sub.add_parser("dev", help="run the local app (dev)")
    dev.add_argument("--host", default=None); dev.add_argument("--port", type=int, default=None)
    dev.add_argument("--public", action="store_true", help="supervise a Quick Tunnel with an existing cloudflared")
    start = sub.add_parser("start", help="run the local app (start)")
    start.add_argument("--host", default=None); start.add_argument("--port", type=int, default=None)
    sub.add_parser("doctor", help="inspect available optional tools")
    sub.add_parser("info", help="show Limbo version and core capability")
    sub.add_parser("dependencies", help="list optional integrations")
    build = sub.add_parser("build", help="build browser-native frontend assets into dist")
    build.add_argument("--out-dir", default=None); build.add_argument("--base", default=None)
    build.add_argument("--minify", action="store_true"); build.add_argument("--sourcemap", action="store_true")
    preview = sub.add_parser("preview", help="serve an existing build output directory")
    preview.add_argument("--host", default=None); preview.add_argument("--port", type=int, default=None)
    config_cmd = sub.add_parser("config", help="configuration commands")
    config_sub = config_cmd.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("validate", help="validate limbo.toml and environment overrides")
    add = sub.add_parser("add", help="declare a frontend package without installing it")
    add.add_argument("package"); add.add_argument("--dev", action="store_true")
    remove = sub.add_parser("remove", help="remove a frontend package declaration")
    remove.add_argument("package")
    frontend = sub.add_parser("frontend", help="inspect local frontend compiler integrations")
    frontend_sub = frontend.add_subparsers(dest="frontend_command", required=True)
    frontend_doctor = frontend_sub.add_parser("doctor", help="inspect optional frontend tools")
    frontend_doctor.add_argument("directory", nargs="?", default=".")
    frontend_capabilities = frontend_sub.add_parser("capabilities", help="print the machine-readable compiler capability manifest")
    frontend_capabilities.add_argument("directory", nargs="?", default=".")
    frontend_capabilities.add_argument("--json", action="store_true", dest="as_json")
    tunnel = sub.add_parser("tunnel", help="optional cloudflared supervision")
    tunnel_sub = tunnel.add_subparsers(dest="tunnel_command", required=True)
    tunnel_sub.add_parser("preflight", help="explain public exposure and tool detection")
    delete = tunnel_sub.add_parser("delete", help="delete a named tunnel only when confirmed")
    delete.add_argument("name"); delete.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "create": return _create(args.name, args.template)
    if args.command in ("add", "remove"):
        try:
            message = edit_package_metadata(Path.cwd(), args.command, args.package, dev=getattr(args, "dev", False))
        except ProjectMetadataError as exc:
            print(f"Error: {exc}", file=sys.stderr); return 2
        print(message)
        return 0
    if args.command in ("dev", "start"):
        supervisor = None
        try:
            config = load_config(overrides={"server": {"host": args.host, "port": args.port}})
            host = config.server.get("host", "127.0.0.1")
            port = config.server.get("port", 8000)
            if getattr(args, "public", False):
                if args.command != "dev":
                    print("Error: --public is only valid with limbo dev.", file=sys.stderr)
                    return 2
                try:
                    supervisor = prepare_quick_tunnel(
                        detect_cloudflared(config.tunnel.get("cloudflared")),
                        host=host, port=port, debug=bool(config.app.get("debug", False)),
                        csrf=config.security.get("csrf"),
                    )
                except TunnelError as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    return 2
                for line in supervisor.warnings:
                    print(f"Warning: {line}")
                supervisor.start()
                found = supervisor.wait_for_url(timeout=15)
                print(f"Tunnel URL: {found or 'waiting — see cloudflared output'}")
            app = _load_app()
            print(f"Limbo {__version__} serving on http://{host}:{port} (Ctrl+C to stop)")
            app.run(host=host, port=port)
            return 0
        except KeyboardInterrupt:
            return 0
        except (OSError, RuntimeError, TypeError, ConfigError, TunnelError) as exc:
            print(f"Error: {exc}", file=sys.stderr); return 2
        finally:
            if supervisor is not None:
                supervisor.stop()
    if args.command == "preview":
        try:
            config = load_config(overrides={"server": {"host": args.host, "port": args.port}})
            output = preview_directory(Path.cwd(), config.build.get("out_dir", "dist"))
            handler = partial(SimpleHTTPRequestHandler, directory=str(output))
            server = ThreadingHTTPServer((config.server.get("host", "127.0.0.1"), config.server.get("port", 8000)), handler)
            print(f"Limbo preview serving {output} at http://{server.server_address[0]}:{server.server_address[1]} (Ctrl+C to stop)")
            try: server.serve_forever()
            except KeyboardInterrupt: pass
            finally: server.server_close()
            return 0
        except (OSError, ConfigError, ProjectMetadataError) as exc:
            print(f"Error: {exc}", file=sys.stderr); return 2
    if args.command == "config" and args.config_command == "validate":
        try: config = load_config()
        except ConfigError as exc:
            print(f"Configuration invalid: {exc}", file=sys.stderr); return 2
        print(f"Configuration valid: {config.path or 'safe defaults'}")
        for key, source in sorted(config.sources.items()): print(f"  {key}: {source}")
        return 0
    if args.command == "info":
        print(f"Limbo {__version__}\nCore: Python standard library only\nHTTP server: built-in threaded TCP implementation")
        return 0
    if args.command == "dependencies":
        print("Required: none (Python standard library only)\nBuilt-in: SQLite migrations, uploads, templates, auth primitives, WebSocket, local module resolver, production build for browser-native assets\nOptional frontend compilers: esbuild, @vue/compiler-sfc, svelte (detected, never installed)\nOptional tunnel: official cloudflared executable (detected, never downloaded)")
        return 0
    if args.command == "doctor":
        try:
            config = load_config()
        except ConfigError as exc:
            print(f"Configuration: invalid ({exc})", file=sys.stderr)
            return 2
        status = "loaded " + str(config.path) if config.path else "defaults (no limbo.toml)"
        print(f"Python: {sys.version.split()[0]}\nConfiguration: {status}\ncloudflared: {_tool_status('cloudflared', ('--version',))}\nNode.js: {_tool_status('node', ('--version',))}")
        for line in _frontend_tool_report(): print(line)
        return 0
    if args.command == "build":
        overrides = {"build": {}}
        if args.out_dir: overrides["build"]["out_dir"] = args.out_dir
        if args.base: overrides["build"]["base"] = args.base
        if args.minify: overrides["build"]["minify"] = True
        if args.sourcemap: overrides["build"]["sourcemap"] = True
        try:
            config = load_config(overrides=overrides)
            report = build_project(Path.cwd(), config)
        except (BuildError, ConfigError, OSError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        print(f"Built {report.asset_count} assets → {report.out_dir}")
        print(f"Digest: {report.digest}")
        for warning in report.warnings:
            print(f"Warning: {warning}")
        return 0
    if args.command == "tunnel" and args.tunnel_command == "preflight":
        try:
            config = load_config()
        except ConfigError as exc:
            print(f"Configuration invalid: {exc}", file=sys.stderr)
            return 2
        executable = detect_cloudflared(config.tunnel.get("cloudflared"))
        host = config.server.get("host", "127.0.0.1")
        for line in exposure_warnings(host=host, debug=bool(config.app.get("debug", False)), csrf=config.security.get("csrf")):
            print(f"Warning: {line}")
        if not executable:
            print(f"Error: {missing_tool_message()}", file=sys.stderr)
            return 2
        try:
            print(f"cloudflared: {cloudflared_version(executable)}")
        except TunnelError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.command == "tunnel" and args.tunnel_command == "delete":
        try:
            argv = plan_delete(confirm=args.confirm, name=args.name)
        except TunnelError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        executable = detect_cloudflared()
        if not executable:
            print(f"Error: {missing_tool_message()}", file=sys.stderr)
            return 2
        argv[0] = executable
        print("Confirmed destructive command:")
        print(" ".join(argv))
        print("Limbo printed the confirmed cloudflared command and did not execute it.")
        return 0
    if args.command == "frontend" and args.frontend_command == "capabilities":
        try:
            registry = AdapterRegistry((BrowserNativeAdapter(), EsbuildAdapter(args.directory), FrameworkSFCAdapter(args.directory, "vue"), FrameworkSFCAdapter(args.directory, "svelte")) )
            manifest = registry.manifest()
        except (OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr); return 2
        if args.as_json:
            print(json.dumps(manifest, indent=2, sort_keys=True))
        else:
            for adapter in manifest["adapters"]:
                state = "available" if adapter["available"] else "unavailable (optional tool missing)"
                print(f"{adapter['adapter_id']}: {state} — {adapter['reason']}")
        return 0
    if args.command == "frontend" and args.frontend_command == "doctor":
        try:
            capabilities = FrontendCompiler(args.directory).doctor()
        except (OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr); return 2
        for name, path in capabilities.items():
            print(f"{name}: {path or 'not installed (optional)'}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
