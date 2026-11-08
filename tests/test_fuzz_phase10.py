"""Phase 10 property checks for the HTTP parser and package resolver.

These are deterministic adversarial samples, not a certification.
"""
from __future__ import annotations

import io
import random
import tempfile
import unittest
from pathlib import Path

from limbo.frontend import FrontendError, ModuleResolver
from limbo.http import HTTPError
from limbo.server import LimboServer
from limbo.http import Response


class ParserFuzzTests(unittest.TestCase):
    def setUp(self):
        self.server = LimboServer(lambda request: Response.text("ok"), max_header_bytes=4096, max_body_bytes=1024, max_header_count=20)

    def parse(self, raw: bytes):
        return self.server._read_request(io.BytesIO(raw), ("127.0.0.1", 9))

    def test_random_bytes_never_become_a_successful_routable_request(self):
        rng = random.Random(10)
        for _ in range(80):
            raw = rng.randbytes(rng.randint(0, 180))
            try:
                request = self.parse(raw)
            except HTTPError as exc:
                self.assertIn(exc.status, {400, 408, 413, 431})
                continue
            if request is None:
                continue
            self.assertIn(request.method, {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
            self.assertTrue(request.path.startswith("/") or request.path == "*")
            self.assertNotIn("\r", request.path)
            self.assertNotIn("\n", request.path)

    def test_hostile_request_lines_are_rejected(self):
        samples = [
            b"GET / HTTP/1.1\n\n",
            b"GET /\x00 HTTP/1.1\r\nHost: a\r\n\r\n",
            b"GET / HTTP/9.9\r\nHost: a\r\n\r\n",
            b"G(T / HTTP/1.1\r\nHost: a\r\n\r\n",
            b"GET http://evil/ HTTP/1.1\r\nHost: evil\r\n\r\n",
            b"GET /a b HTTP/1.1\r\n\r\n",
            b"GET //x HTTP/1.1\r\nHost: a\r\n\r\n",
            "GET /\u0001 HTTP/1.1\r\nHost: a\r\n\r\n".encode("latin-1"),
        ]
        for raw in samples:
            with self.subTest(raw=raw[:40]):
                with self.assertRaises(HTTPError):
                    self.parse(raw)


class ResolverFuzzTests(unittest.TestCase):
    def test_random_specifiers_cannot_escape_the_project(self):
        rng = random.Random(11)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "src" / "main.js").write_text("export const ok = 1;\n", encoding="utf-8")
            (root / "node_modules" / "pkg").mkdir(parents=True)
            (root / "node_modules" / "pkg" / "package.json").write_text('{"name":"pkg","module":"index.js"}', encoding="utf-8")
            (root / "node_modules" / "pkg" / "index.js").write_text("export const pkg = 1;\n", encoding="utf-8")
            resolver = ModuleResolver(root)
            alphabet = "./\\@#:?&\n\x00*"
            for _ in range(60):
                specifier = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 24)))
                try:
                    found = resolver.resolve(specifier, root / "src" / "main.js")
                except FrontendError:
                    continue
                found.path.relative_to(root)


if __name__ == "__main__":
    unittest.main()
