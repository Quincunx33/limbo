"""Optional real-browser smoke fixtures for Vue and Svelte SFC adapters."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import unittest
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from limbo.frontend import FrontendCompiler, FrontendDevServer
from limbo.frontend_engine import FrameworkSFCAdapter
from limbo.http import Request


class BrowserSFCFixtureTests(unittest.TestCase):
    def _project_with_local_tools(self, root: Path) -> None:
        """Copy repository-installed optional tools into the isolated fixture."""
        tools = Path(__file__).resolve().parents[1] / "node_modules"
        if tools.is_dir():
            shutil.copytree(tools, root / "node_modules", symlinks=True)

    def test_typescript_sfc_emits_composed_inline_source_map_with_real_tools(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temp:
            root = Path(temp)
            self._project_with_local_tools(root)
            (root / "Component.vue").write_text(
                "<script lang='ts'>export const answer: number = 42;</script>"
                "<template><p>{{ answer }}</p></template>", encoding="utf-8")
            compiler = FrontendCompiler(root)
            adapter = FrameworkSFCAdapter(root, "vue")
            if not adapter.detect() or not compiler.esbuild:
                self.skipTest("Local Vue compiler and esbuild are required")
            result = adapter.transform(root / "Component.vue")
            self.assertIsNotNone(result.source_map)
            payload = json.loads(result.source_map)
            self.assertEqual(payload.get("version"), 3)
            self.assertTrue(payload.get("sources"))
            self.assertIn("sourceMappingURL=data:application/json;base64,", result.code)

    def _smoke(self, framework: str) -> None:
        chromium = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
        if not chromium:
            self.skipTest("Chromium is not installed")
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temp:
            root = Path(temp)
            self._project_with_local_tools(root)
            (root / "src").mkdir()
            compiler = FrontendCompiler(root)
            adapter = FrameworkSFCAdapter(root, framework)
            if not adapter.detect():
                self.skipTest(f"Local {framework} SFC compiler is not installed")
            if framework == "vue":
                if not compiler._node_package_path("vue"):
                    self.skipTest("Local Vue runtime is not installed")
                (root / "src" / "App.vue").write_text(
                    "<template><p id='done'>Vue browser fixture</p></template>", encoding="utf-8")
                (root / "src" / "main.js").write_text(
                    "import {createApp} from 'vue/dist/vue.runtime.esm-browser.js';"
                    "import App from './App.vue';createApp(App).mount('#app');",
                    encoding="utf-8",
                )
                expected = "Vue browser fixture"
            else:
                (root / "src" / "App.svelte").write_text(
                    "<p id='done'>Svelte browser fixture</p>", encoding="utf-8")
                (root / "src" / "main.js").write_text(
                    "import App from './App.svelte';import * as Svelte from 'svelte';"
                    "const target=document.querySelector('#app');"
                    "if(typeof Svelte.mount==='function')Svelte.mount(App,{target});else new App({target});",
                    encoding="utf-8",
                )
                expected = "Svelte browser fixture"
            frontend = FrontendDevServer(root)
            page = "<!doctype html><meta charset='utf-8'><div id='app'></div>" + frontend.script_tag("src/main.js")

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path == "/":
                        body = page.encode("utf-8")
                        status, headers = 200, {"Content-Type": "text/html; charset=utf-8"}
                    else:
                        response = frontend.serve(Request("GET", urlsplit(self.path).path, {}))
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
                     "--virtual-time-budget=5000", url],
                    capture_output=True, text=True, timeout=25, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr[-3000:])
                self.assertIn(expected, result.stdout, result.stderr[-3000:])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_vue_sfc_runs_in_chromium(self):
        self._smoke("vue")

    def test_svelte_sfc_runs_in_chromium(self):
        self._smoke("svelte")


if __name__ == "__main__":
    unittest.main()
