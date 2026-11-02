import gzip
import tempfile
import unittest
from pathlib import Path

from limbo import Limbo, Request, Response, cors, gzip_compress


class StaticAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "file.txt").write_bytes(b"0123456789")
        (self.root / "nested").mkdir()
        (self.root / "nested" / "index.html").write_text("nested-index", encoding="utf-8")
        self.app = Limbo()
        self.app.static("/static", self.root, index="index.html")

    def tearDown(self):
        self.temp.cleanup()

    def get(self, path="/static/file.txt", headers=None):
        return self.app.dispatch(Request("GET", path, headers or {}))

    def test_encoded_traversal_and_encoded_separator_are_not_served(self):
        for path in ("/static/%2e%2e/secret", "/static/%2E%2E%2fsecret",
                     "/static/nested%2f..%2f..%2fsecret"):
            self.assertEqual(self.get(path).status, 404)

    def test_multiple_and_malformed_ranges_return_416(self):
        for value in ("bytes=0-1,4-5", "bytes=abc-def", "bytes=4-2",
                      "bytes=-", "items=0-1", "bytes=999999999999999999999999999999-"):
            response = self.get(headers={"range": value})
            self.assertEqual(response.status, 416, value)
            self.assertEqual(response.headers["Content-Range"], "bytes */10")

    def test_open_ended_and_clamped_ranges(self):
        first = self.get(headers={"range": "bytes=4-"})
        self.assertEqual(first.status, 206)
        self.assertEqual(b"".join(first.body), b"456789")
        last = self.get(headers={"range": "bytes=8-999"})
        self.assertEqual(last.status, 206)
        self.assertEqual(b"".join(last.body), b"89")

    def test_invalid_if_range_falls_back_to_full_representation(self):
        response = self.get(headers={"range": "bytes=0-1", "if-range": "not-a-date"})
        self.assertEqual(response.status, 200)
        self.assertEqual(b"".join(response.body), b"0123456789")

    def test_directory_index_is_opened_through_safe_directory_walk(self):
        response = self.get("/static/nested/")
        self.assertEqual(response.status, 200)
        self.assertEqual(b"".join(response.body), b"nested-index")


class MiddlewarePolicyTests(unittest.TestCase):
    def test_gzip_explicit_zero_overrides_wildcard(self):
        middleware = gzip_compress(minimum_size=1)
        request = Request("GET", "/", {"accept-encoding": "*;q=1, gzip;q=0"})
        response = middleware(request, lambda req: Response.text("compress me" * 20))
        self.assertNotIn("Content-Encoding", response.headers)

    def test_gzip_respects_no_transform_and_partial_responses(self):
        middleware = gzip_compress(minimum_size=1)
        body = "compress me" * 20
        no_transform = middleware(
            Request("GET", "/", {"accept-encoding": "gzip"}),
            lambda req: Response(body, 200, {"Content-Type": "text/plain", "Cache-Control": "no-transform"}))
        self.assertNotIn("Content-Encoding", no_transform.headers)
        partial = middleware(
            Request("GET", "/", {"accept-encoding": "gzip"}),
            lambda req: Response(body, 206, {"Content-Type": "text/plain", "Content-Range": "bytes 0-2/3"}))
        self.assertNotIn("Content-Encoding", partial.headers)

    def test_cors_preflight_rejects_disallowed_method(self):
        app = Limbo()
        app.middleware(cors(allow_origins=("https://app.example",), allow_methods=("GET",)))
        response = app.dispatch(Request("OPTIONS", "/anything", {
            "origin": "https://app.example",
            "access-control-request-method": "DELETE",
        }))
        self.assertEqual(response.status, 403)

    def test_cors_wildcard_origin_is_not_reflected(self):
        middleware = cors(allow_origins=("*",))
        response = middleware(Request("GET", "/", {"origin": "https://site.example"}),
                              lambda req: Response.text("ok"))
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")


if __name__ == "__main__":
    unittest.main()
