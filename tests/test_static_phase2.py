import socket
import tempfile
import unittest
from pathlib import Path
from limbo import Limbo, Request, Response
from limbo.server import LimboServer

class StaticDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "hello.txt").write_bytes(b"0123456789")
        (self.root / "empty.txt").write_bytes(b"")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "index.html").write_text("index", encoding="utf-8")
        self.app = Limbo()
        self.app.static("/assets", self.root, index="index.html")

    def tearDown(self): self.temp.cleanup()
    def request(self, path="/assets/hello.txt", headers=None, method="GET"):
        return self.app.dispatch(Request(method, path, headers or {}))

    def test_streamed_file_and_validators(self):
        response = self.request()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Type"], "text/plain")
        self.assertTrue(response.headers["ETag"].startswith('W/"'))
        self.assertEqual(b"".join(response.body), b"0123456789")
        not_modified = self.request(headers={"if-none-match": response.headers["ETag"]})
        self.assertEqual(not_modified.status, 304)
        updated = self.request(headers={"if-none-match": '"different"',
                                        "if-modified-since": response.headers["Last-Modified"]})
        self.assertEqual(updated.status, 200)
        updated.close()

    def test_ranges_and_if_range(self):
        partial = self.request(headers={"range": "bytes=-3"})
        self.assertEqual(partial.status, 206)
        self.assertEqual(b"".join(partial.body), b"789")
        self.assertEqual(partial.headers["Content-Range"], "bytes 7-9/10")
        self.assertEqual(self.request(headers={"range": "bytes=-0"}).status, 416)
        self.assertEqual(self.request(headers={"range": "bytes=10-"}).status, 416)
        self.assertEqual(self.request("/assets/empty.txt", {"range": "bytes=0-"}).status, 416)
        first = self.request()
        first.close()
        full = self.request(headers={"range": "bytes=0-1", "if-range": first.headers["ETag"]})
        self.assertEqual(full.status, 200)
        self.assertEqual(b"".join(full.body), b"0123456789")

    def test_head_and_index(self):
        head = self.request(method="HEAD")
        self.assertEqual(head.status, 200)
        self.assertEqual(head.headers["Content-Length"], "10")
        self.assertEqual(head.body, b"")
        wire_server = LimboServer(lambda request: head)
        left, right = socket.socketpair()
        try:
            wire_server._write_response(left, head, "HEAD", False)
            wire = right.recv(4096).lower()
            self.assertIn(b"content-length: 10", wire)
            self.assertNotIn(b"0123456789", wire)
        finally:
            left.close()
            right.close()
        directory = self.request("/assets/docs/")
        self.assertEqual(directory.status, 200)
        self.assertEqual(b"".join(directory.body), b"index")
        self.assertEqual(self.request("/assets/").status, 404)

    def test_symlink_policy_and_mount_validation(self):
        outside = self.root.parent / (self.root.name + "-outside.txt")
        outside.write_text("outside", encoding="utf-8")
        try: (self.root / "outside.txt").symlink_to(outside)
        except (OSError, NotImplementedError): self.skipTest("Symlinks unavailable")
        self.assertEqual(self.request("/assets/outside.txt").status, 404)
        with self.assertRaises(ValueError): self.app.static("/assets", self.root)
        with self.assertRaises(ValueError): Limbo().static("/x", self.root, index="../secret")
        with self.assertRaises(ValueError): Limbo().static("/x", self.root, symlinks="unsafe")

    def test_response_cookie_helpers_keep_multiple_headers(self):
        response = Response.text("ok").set_header("X-Trace", "abc")
        response.set_cookie("session", "one", secure=True, samesite="Strict")
        response.set_cookie("theme", "dark")
        self.assertEqual(response.headers["X-Trace"], "abc")
        self.assertEqual(len(response.cookies), 2)
        self.assertIn("HttpOnly", response.cookies[0])
        self.assertIn("Secure", response.cookies[0])
        self.assertIn("SameSite=Strict", response.cookies[0])
        left, right = socket.socketpair()
        try:
            LimboServer(lambda request: response)._write_response(left, response, "GET", False)
            wire = right.recv(4096)
            self.assertEqual(wire.count(b"set-cookie:"), 2)
        finally:
            left.close()
            right.close()
        with self.assertRaises(ValueError): response.set_header("Bad\r\nName", "x")

if __name__ == "__main__": unittest.main()
