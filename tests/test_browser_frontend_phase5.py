"""Chromium smoke coverage for Limbo's optional TS and JSX adapters."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from limbo.frontend import FrontendDevServer
from limbo.http import Request


class BrowserFrontendSmokeTests(unittest.TestCase):
    def test_css_modules_rewrite_nested_selectors_and_preserve_global_classes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            css = root / "styles.css"
            css.write_text(
                "/* .comment must stay unchanged */\n"
                ":global(.vendor) .local, .other { color: red; }\n"
                "@media (min-width: 1px) { .nested:hover { color: blue; } }\n",
                encoding="utf-8",
            )
            frontend = FrontendDevServer(root)
            classes, compiled = frontend._compile_css_module(css)
            self.assertEqual(set(classes), {"local", "other", "nested"})
            self.assertIn(":global(.vendor)", compiled)
            self.assertNotIn(".local ", compiled)
            self.assertNotIn(".other ", compiled)
            self.assertNotIn(".nested:hover", compiled)
            self.assertIn(".comment must stay unchanged", compiled)

    def test_typescript_and_jsx_modules_execute_in_chromium(self):
        chromium = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
        if not chromium:
            self.skipTest("Chromium is not installed")

        for extension, source, expected in (
            ("ts", 'document.querySelector("#result").textContent = "TypeScript OK";', "TypeScript OK"),
            ("jsx", 'const node = <div>JSX OK</div>; document.querySelector("#result").append(node);', "JSX OK"),
        ):
            with self.subTest(extension=extension), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source_dir = root / "src"
                source_dir.mkdir()
                entry = source_dir / f"main.{extension}"
                entry.write_text(source, encoding="utf-8")
                if extension == "jsx":
                    runtime = root / "node_modules" / "react"
                    runtime.mkdir(parents=True)
                    (runtime / "jsx-runtime.js").write_text(
                        "export const Fragment=Symbol.for('fragment');"
                        "export function jsx(type,props){const node=document.createElement(type);"
                        "node.textContent=props.children||'';return node}export const jsxs=jsx;",
                        encoding="utf-8",
                    )
                frontend = FrontendDevServer(root)
                if not frontend.compiler.esbuild:
                    self.skipTest("Local esbuild is not installed")
                tags = frontend.script_tag(f"src/main.{extension}")
                page = f"<!doctype html><meta charset='utf-8'><div id='result'></div>{tags}"

                class Handler(BaseHTTPRequestHandler):
                    def do_GET(self):
                        if self.path == "/":
                            body = page.encode("utf-8")
                            status, headers = 200, {"Content-Type": "text/html; charset=utf-8"}
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
                    url = f"http://127.0.0.1:{server.server_address[1]}/"
                    result = subprocess.run(
                        [chromium, "--headless", "--no-sandbox", "--disable-gpu", "--dump-dom",
                         "--virtual-time-budget=3000", url],
                        capture_output=True, text=True, timeout=20, check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                    self.assertIn(expected, result.stdout)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

    def test_self_accepted_module_updates_without_document_reload(self):
        chromium = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
        if not chromium:
            self.skipTest("Chromium is not installed")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            module = root / "main.js"
            module.write_text(
                "export const version=1;import.meta.hot.accept(updated=>{document.body.dataset.hmr=String(updated.version)});",
                encoding="utf-8",
            )
            frontend = FrontendDevServer(root)
            page = "<!doctype html><meta charset='utf-8'><body>HMR fixture</body>" + frontend.script_tag("main.js")
            version_requests = 0

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path == "/":
                        body = page.encode("utf-8")
                        status, headers = 200, {"Content-Type": "text/html; charset=utf-8"}
                    else:
                        parsed = urlsplit(self.path)
                        if parsed.path == "/__limbo/_version":
                            nonlocal version_requests
                            version_requests += 1
                            if version_requests == 2:
                                module.write_text(
                                    "export const version=2;import.meta.hot.accept(updated=>{document.body.dataset.hmr=String(updated.version)});",
                                    encoding="utf-8",
                                )
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
                url = f"http://127.0.0.1:{server.server_address[1]}/"
                result = subprocess.run(
                    [chromium, "--headless", "--no-sandbox", "--disable-gpu", "--dump-dom",
                     "--virtual-time-budget=6000", url],
                    capture_output=True, text=True, timeout=20, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                self.assertGreaterEqual(version_requests, 2, result.stderr[-2000:])
                self.assertIn('data-hmr="2"', result.stdout, result.stderr[-4000:])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_accepted_dependency_updates_without_document_reload(self):
        chromium = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
        if not chromium:
            self.skipTest("Chromium is not installed")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dependency = root / "dep.js"
            dependency.write_text("export const value=1;", encoding="utf-8")
            (root / "main.js").write_text(
                "import {value} from './dep.js';document.body.dataset.initial=String(value);"
                "import.meta.hot.accept('./dep.js',updated=>{document.body.dataset.hmr=String(updated.value)});",
                encoding="utf-8",
            )
            frontend = FrontendDevServer(root)
            page = "<!doctype html><meta charset='utf-8'><body>HMR dependency fixture</body>" + frontend.script_tag("main.js")
            version_requests = 0

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    parsed = urlsplit(self.path)
                    if parsed.path == "/":
                        body = page.encode("utf-8")
                        status, headers = 200, {"Content-Type": "text/html; charset=utf-8"}
                    else:
                        if parsed.path == "/__limbo/_version":
                            nonlocal version_requests
                            version_requests += 1
                            if version_requests == 2:
                                dependency.write_text("export const value=2;", encoding="utf-8")
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
                url = f"http://127.0.0.1:{server.server_address[1]}/"
                result = subprocess.run(
                    [chromium, "--headless", "--no-sandbox", "--disable-gpu", "--dump-dom",
                     "--virtual-time-budget=6000", url],
                    capture_output=True, text=True, timeout=20, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                self.assertGreaterEqual(version_requests, 2)
                self.assertIn('data-hmr="2"', result.stdout, result.stderr[-4000:])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
