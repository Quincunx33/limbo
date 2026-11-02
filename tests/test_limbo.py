import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from limbo import Limbo, Request, Response
from limbo.server import LimboServer


class RequestTests(unittest.TestCase):
    def test_query_form_json_cookies(self):
        req = Request("POST", "/x?a=1&a=2&blank=", {"cookie": "session=abc; theme=dark"}, b'{"ok":true}')
        self.assertEqual(req.path, "/x")
        self.assertEqual(req.query_params["a"], ["1", "2"])
        self.assertEqual(req.json, {"ok": True})
        self.assertEqual(req.cookies["session"], "abc")
        form = Request("POST", "/", {}, b"a=one&a=two")
        self.assertEqual(form.form["a"], ["one", "two"])


class AppTests(unittest.TestCase):
    def setUp(self):
        self.app = Limbo()

    def test_route_parameters_and_json(self):
        @self.app.get("/users/{user_id}")
        def user(req): return {"id": req.path_params["user_id"]}
        result = self.app.dispatch(Request("GET", "/users/42", {}))
        self.assertEqual(result.status, 200)
        self.assertEqual(json.loads(result.body), {"id": "42"})

    def test_404_405_and_options(self):
        @self.app.get("/only")
        def only(req): return "ok"
        self.assertEqual(self.app.dispatch(Request("GET", "/absent", {})).status, 404)
        post = self.app.dispatch(Request("POST", "/only", {}))
        self.assertEqual(post.status, 405)
        self.assertIn("GET", post.headers["Allow"])
        self.assertEqual(self.app.dispatch(Request("OPTIONS", "/only", {})).status, 204)

    def test_middleware_order_and_return(self):
        calls = []
        @self.app.middleware
        def wrapper(req, next_handler):
            calls.append("before"); res = next_handler(req); calls.append("after"); return res
        @self.app.get("/")
        def home(req): calls.append("route"); return "ok"
        self.app.dispatch(Request("GET", "/", {}))
        self.assertEqual(calls, ["before", "route", "after"])

    def test_static_range_and_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "hello.txt").write_bytes(b"0123456789")
            self.app.static("/assets", root)
            partial = self.app.dispatch(Request("GET", "/assets/hello.txt", {"range": "bytes=2-5"}))
            self.assertEqual(partial.status, 206)
            self.assertEqual(partial.body, b"2345")
            escaped = self.app.dispatch(Request("GET", "/assets/%2e%2e/secret", {}))
            self.assertEqual(escaped.status, 404)

    def test_duplicate_route_rejected(self):
        @self.app.get("/same")
        def a(req): return "a"
        with self.assertRaises(ValueError):
            self.app.get("/same")(lambda req: "b")


class SocketServerTests(unittest.TestCase):
    def setUp(self):
        self.app = Limbo()
        @self.app.get("/hello/{name}")
        def hello(req): return {"hello": req.path_params["name"]}
        self.server = LimboServer(self.app.dispatch, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        deadline = time.time() + 2
        while self.server.port == 0 and time.time() < deadline: time.sleep(0.01)

    def tearDown(self):
        self.server.shutdown(); self.thread.join(timeout=2)

    def test_http_request_and_keep_alive(self):
        with socket.create_connection(("127.0.0.1", self.server.port), timeout=2) as client:
            client.sendall(b"GET /hello/Limbo HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            data = b""
            while True:
                part = client.recv(4096)
                if not part: break
                data += part
        self.assertIn(b"200 OK", data)
        self.assertIn(b'{"hello":"Limbo"}', data)

    def test_head_and_no_content_response_framing(self):
        left, right = socket.socketpair()
        try:
            self.server._write_response(left, Response(b"hidden", 200), "HEAD", False)
            head = right.recv(4096)
            self.assertIn(b"content-length: 6", head.lower())
            self.assertNotIn(b"hidden", head)
            left.close(); right.close()
            left, right = socket.socketpair()
            self.server._write_response(left, Response(b"", 204), "GET", False)
            empty = right.recv(4096)
            self.assertIn(b"204 No Content", empty)
            self.assertNotIn(b"content-length", empty.lower())
            left.close(); right.close()
            left, right = socket.socketpair()
            self.server._write_response(left, Response(b"", 304, {"Content-Length": "999"}), "GET", False)
            not_modified = right.recv(4096).lower()
            self.assertNotIn(b"content-length", not_modified)
            left.close(); right.close()
            left, right = socket.socketpair()
            self.server._write_response(left, Response(iter([b"stream"])), "HEAD", False)
            streamed_head = right.recv(4096).lower()
            self.assertNotIn(b"transfer-encoding", streamed_head)
        finally:
            left.close(); right.close()

    def test_chunked_request_body(self):
        @self.app.post("/echo")
        def echo(req): return {"body": req.body.decode()}
        # Keep a separate local app server for the request-body test.
        self.server.shutdown(); self.thread.join(timeout=2)
        self.server = LimboServer(self.app.dispatch, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        deadline = time.time() + 2
        while self.server.port == 0 and time.time() < deadline: time.sleep(0.01)
        with socket.create_connection(("127.0.0.1", self.server.port), timeout=2) as client:
            client.sendall(b"POST /echo HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n3\r\nabc\r\n0\r\n\r\n")
            data = b""
            while True:
                part = client.recv(4096)
                if not part: break
                data += part
        self.assertIn(b'{"body":"abc"}', data)


if __name__ == "__main__":
    unittest.main()
