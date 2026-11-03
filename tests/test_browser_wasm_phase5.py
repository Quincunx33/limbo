"""Phase 5 browser-level WebAssembly smoke test using Chromium headless."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class BrowserWasmSmokeTests(unittest.TestCase):
    def test_browser_instantiates_served_wasm_module(self):
        chromium = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
        if not chromium:
            self.skipTest("Chromium is not installed")
        # (module (func (export "answer") (result i32) i32.const 42))
        wasm = bytes.fromhex(
            "0061736d01000000"
            "0105016000017f"
            "03020100"
            "070a0106616e737765720000"
            "0a06010400412a0b"
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "module.wasm").write_bytes(wasm)
            (root / "index.html").write_text(
                """<!doctype html><meta charset='utf-8'><output id='result'>pending</output>
<script>
(async () => {
  const response = await fetch('/module.wasm');
  const bytes = await response.arrayBuffer();
  const {instance} = await WebAssembly.instantiate(bytes);
  document.querySelector('#result').textContent = String(instance.exports.answer());
})().catch(error => { document.querySelector('#result').textContent = 'error:' + error; });
</script>""",
                encoding="utf-8",
            )

            class QuietHandler(SimpleHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), lambda *args, **kwargs: QuietHandler(*args, directory=str(root), **kwargs))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
                result = subprocess.run(
                    [chromium, "--headless", "--no-sandbox", "--disable-gpu", "--dump-dom",
                     "--run-all-compositor-stages-before-draw", "--virtual-time-budget=10000", url],
                    capture_output=True, text=True, timeout=30, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                self.assertIn(">42</output>", result.stdout)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
