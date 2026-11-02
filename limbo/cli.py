"""Command-line interface for Limbo."""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
from pathlib import Path

from . import __version__


def _create(name: str) -> int:
    target = Path(name).expanduser()
    if target.exists() and any(target.iterdir()):
        print(f"Error: {target} already exists and is not empty.", file=sys.stderr)
        return 2
    target.mkdir(parents=True, exist_ok=True)
    for folder in ("static", "templates", "uploads", "tests"):
        (target / folder).mkdir(exist_ok=True)
    (target / "app.py").write_text('''from limbo import Limbo\n\napp = Limbo(name="My Limbo app")\n\n@app.get("/")\ndef home(req):\n    return app.html("<h1>Hello from Limbo</h1><p>Your app is running.</p>")\n\n@app.get("/api/health")\ndef health(req):\n    return {"ok": True}\n\napp.static("/static", "static")\n\nif __name__ == "__main__":\n    app.run()\n''', encoding="utf-8")
    (target / "limbo.toml").write_text('[app]\nname = "My Limbo app"\nhost = "127.0.0.1"\nport = 8000\ndebug = true\n', encoding="utf-8")
    (target / "requirements.txt").write_text("# Limbo core has no third-party dependencies.\n", encoding="utf-8")
    (target / "README.md").write_text(f"# {target.name}\n\nRun `python app.py` from this directory.\n", encoding="utf-8")
    print(f"Created {target}. Run `cd {target} && python app.py`.")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="limbo", description="Python-first, zero-dependency web framework")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="create a starter project"); create.add_argument("name")
    for command in ("dev", "start"):
        cmd = sub.add_parser(command, help=f"run the local app ({command})")
        cmd.add_argument("--host", default="127.0.0.1"); cmd.add_argument("--port", type=int, default=8000)
    sub.add_parser("doctor", help="inspect available optional tools")
    sub.add_parser("info", help="show Limbo version and core capability")
    sub.add_parser("dependencies", help="list optional integrations")
    sub.add_parser("build", help="explain current build support")
    args = parser.parse_args(argv)
    if args.command == "create": return _create(args.name)
    if args.command in ("dev", "start"):
        try:
            app = _load_app()
            print(f"Limbo {__version__} serving on http://{args.host}:{args.port} (Ctrl+C to stop)")
            app.run(host=args.host, port=args.port)
            return 0
        except (OSError, RuntimeError, TypeError) as exc:
            print(f"Error: {exc}", file=sys.stderr); return 2
    if args.command == "info":
        print(f"Limbo {__version__}\nCore: Python standard library only\nHTTP server: built-in threaded TCP implementation")
        return 0
    if args.command == "dependencies":
        print("Required: none (Python standard library only)\nBuilt-in: SQLite, multipart uploads, escaped templates, scrypt password hashing, signed sessions, WebSocket\nOptional compiler integrations: not yet implemented\nOptional tunnel: cloudflared integration not yet implemented")
        return 0
    if args.command == "doctor":
        print(f"Python: {sys.version.split()[0]}\ncloudflared: {shutil.which('cloudflared') or 'not found (optional)'}\nNode.js: {shutil.which('node') or 'not found (optional)'}")
        return 0
    if args.command == "build":
        print("Production frontend builds are not implemented in Limbo 0.1.0; no build was run.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
