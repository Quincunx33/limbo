"""Phase 7 local SSR process contract. Not a Next/Nuxt claim."""
import socket
import sys
import unittest

from limbo.capabilities import assert_no_blanket_claim, capability_matrix
from limbo.runtime_ssr import LocalProcessRuntime, SsrRuntimeError


def _port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class RuntimeTests(unittest.TestCase):
    def test_child_readiness_forward_and_shutdown(self):
        port = _port()
        code = (
            "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
            "class H(BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            "        body = b'ok' if self.path == '/health' else b'page'\n"
            "        self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)\n"
            "    def log_message(self, *args): pass\n"
            f"ThreadingHTTPServer(('127.0.0.1', {port}), H).serve_forever()\n"
        )
        runtime = LocalProcessRuntime([sys.executable, "-c", code], f"http://127.0.0.1:{port}/health", startup_timeout=5)
        runtime.start()
        try:
            status, body = runtime.forward("GET", "/page")
            self.assertEqual(status, 200)
            self.assertEqual(body, b"page")
        finally:
            runtime.stop()
        self.assertIsNotNone(runtime.proc.poll())

    def test_crash_is_reported(self):
        runtime = LocalProcessRuntime([sys.executable, "-c", "import sys; print('token=hideme', flush=True); sys.exit(3)"], "http://127.0.0.1:9/health", startup_timeout=2)
        with self.assertRaises(SsrRuntimeError) as caught:
            runtime.start()
        self.assertIn("exited", str(caught.exception))
        self.assertNotIn("hideme", runtime.log_text())
        runtime.stop()

    def test_matrix_does_not_claim_untested_frameworks(self):
        rows = capability_matrix()
        assert_no_blanket_claim(rows)
        by_id = {row["id"]: row["status"] for row in rows}
        self.assertEqual(by_id["html-css-js"], "supported")
        self.assertEqual(by_id["sqlite"], "supported")
        self.assertEqual(by_id["react"], "optional")
        self.assertEqual(by_id["preact"], "optional")
        self.assertEqual(by_id["lit"], "optional")
        self.assertEqual(by_id["next"], "unsupported")
        self.assertEqual(by_id["postgres"], "unsupported")
        self.assertEqual(by_id["vue"], "experimental")


if __name__ == "__main__":
    unittest.main()
