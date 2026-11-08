"""Real local runtime fixtures for the frontend runtime roadmap.

Skipped unless Chromium and npm are available. These tests install packages
into a temporary project; Limbo itself never runs the package manager.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from limbo.build_engine import build_project
from limbo.frontend import FrontendDevServer
from limbo.http import Request


def _chromium() -> str | None:
    configured = os.environ.get("LIMBO_CHROMIUM")
    if configured and Path(configured).is_file():
        return configured
    return shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome") or shutil.which("chrome-headless-shell")


def _dump(chromium: str, url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [chromium, "--headless", "--no-sandbox", "--disable-gpu", "--dump-dom",
         "--virtual-time-budget=4000", url],
        capture_output=True, text=True, timeout=30, check=False,
    )


class BrowserRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chromium = _chromium()
        if not cls.chromium or not shutil.which("npm"):
            raise unittest.SkipTest("Chromium or npm is not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        frontend = cls.root / "frontend"
        (frontend / "src").mkdir(parents=True)
        (frontend / "package.json").write_text('{"private":true}\n', encoding="utf-8")
        install = subprocess.run(
            ["npm", "install", "--silent", "--no-fund", "--no-audit",
             "preact@10.26.9", "lit@3.3.0", "react@19.1.0", "react-dom@19.1.0", "esbuild@0.25.5"],
            cwd=frontend, capture_output=True, text=True, timeout=180, check=False,
        )
        if install.returncode != 0:
            raise unittest.SkipTest("Could not install local runtime fixtures:\n" + install.stderr[-500:])

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "tmp"):
            cls.tmp.cleanup()

    def _serve(self, page: str, frontend: FrontendDevServer, expected: str):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/":
                    body, status, headers = page.encode("utf-8"), 200, {"Content-Type": "text/html; charset=utf-8"}
                else:
                    parsed = urlsplit(self.path)
                    response = frontend.serve(Request("GET", parsed.path, {}))
                    body = response.body.encode("utf-8") if isinstance(response.body, str) else response.body
                    status, headers = response.status, response.headers
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = _dump(self.chromium, f"http://127.0.0.1:{server.server_address[1]}/")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn(expected, result.stdout)
        self.assertNotIn("unpkg.com", result.stdout)
        self.assertNotIn("esm.sh", result.stdout)

    def test_local_esm_runtimes_execute_without_a_cdn(self):
        frontend_root = self.root / "frontend"
        cases = {
            "preact": (
                'import { h, render } from "preact";\n'
                'render(h("span", null, "Preact OK"), document.querySelector("#result"));\n',
                "Preact OK",
            ),
            "lit": (
                'import { html, render } from "lit";\n'
                'render(html`<span>Lit OK</span>`, document.querySelector("#result"));\n',
                "Lit OK",
            ),
            "react": (
                'import { createElement } from "react";\n'
                'import { createRoot } from "react-dom/client";\n'
                'createRoot(document.querySelector("#result")).render(createElement("span", null, "React OK"));\n',
                "React OK",
            ),
        }
        for name, (source, expected) in cases.items():
            with self.subTest(runtime=name):
                entry = frontend_root / "src" / "main.js"
                entry.write_text(source, encoding="utf-8")
                frontend = FrontendDevServer(frontend_root)
                tags = frontend.script_tag("src/main.js")
                self.assertIn("/_limbo_modules/", tags)
                self.assertNotIn("http://", tags)
                self.assertNotIn("https://", tags)
                page = f"<!doctype html><meta charset='utf-8'><div id='result'></div>{tags}"
                self._serve(page, frontend, expected)

    def test_production_builds_have_no_cdn_and_run(self):
        frontend_root = self.root / "frontend"
        cases = {
            "preact": (
                'import { h, render } from "preact";\n'
                'render(h("span", null, "Preact build OK"), document.querySelector("#result"));\n',
                "Preact build OK",
            ),
            "lit": (
                'import { html, render } from "lit";\n'
                'render(html`<span>Lit build OK</span>`, document.querySelector("#result"));\n',
                "Lit build OK",
            ),
            "react": (
                'import { createElement } from "react";\n'
                'import { createRoot } from "react-dom/client";\n'
                'createRoot(document.querySelector("#result")).render(createElement("span", null, "React build OK"));\n',
                "React build OK",
            ),
        }
        for name, (source, expected) in cases.items():
            with self.subTest(runtime=name):
                (frontend_root / "src" / "main.js").write_text(source, encoding="utf-8")
                (frontend_root / "index.html").write_text(
                    "<!doctype html><meta charset='utf-8'><div id='result'></div>"
                    "<script type='module' src='./src/main.js'></script>\n",
                    encoding="utf-8",
                )
                report = build_project(self.root, frontend="frontend", entry="src/main.js")
                blob = "\n".join(
                    path.read_text(encoding="utf-8", errors="ignore")
                    for path in report.out_dir.rglob("*") if path.is_file()
                )
                self.assertNotIn("unpkg.com", blob)
                self.assertNotIn("esm.sh", blob)
                self.assertNotIn(f'from "{name if name != "react" else "react"}"', blob)

                class Handler(BaseHTTPRequestHandler):
                    def do_GET(self):
                        relative = urlsplit(self.path).path.lstrip("/") or "index.html"
                        target = (report.out_dir / relative).resolve()
                        try:
                            target.relative_to(report.out_dir.resolve())
                            data = target.read_bytes()
                        except (OSError, ValueError):
                            data, status = b"missing", 404
                        else:
                            status = 200
                        kind = "text/html" if str(target).endswith(".html") else "text/javascript"
                        self.send_response(status)
                        self.send_header("Content-Type", kind)
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)

                    def log_message(self, *_args):
                        pass

                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    result = _dump(self.chromium, f"http://127.0.0.1:{server.server_address[1]}/")
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                self.assertIn(expected, result.stdout)


if __name__ == "__main__":
    unittest.main()
