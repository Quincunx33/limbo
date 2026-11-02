import base64
import io
import json
import os
import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path

from limbo import (Database, Limbo, Request, Response, SessionSigner, UploadFile,
                   hash_password, session_cookie, verify_password)
from limbo.server import LimboServer
from limbo.http import HTTPError
from limbo.websocket import WebSocket, handshake_response


class UploadTests(unittest.TestCase):
    def test_multipart_text_file_and_safe_save(self):
        body = (b'--BOUND\r\nContent-Disposition: form-data; name="title"\r\n\r\nhello\r\n'
                b'--BOUND\r\nContent-Disposition: form-data; name="attachment"; filename="../unsafe.txt"\r\n'
                b'Content-Type: text/plain\r\n\r\npayload\r\n--BOUND--\r\n')
        req = Request("POST", "/upload", {"content-type": "multipart/form-data; boundary=BOUND"}, body)
        self.assertEqual(req.form["title"], ["hello"])
        upload = req.files["attachment"][0]
        self.assertIsInstance(upload, UploadFile)
        self.assertEqual(upload.filename, "unsafe.txt")
        with tempfile.TemporaryDirectory() as directory:
            saved = upload.save(directory)
            self.assertEqual(saved.read_bytes(), b"payload")
            with self.assertRaises(FileExistsError): upload.save(directory)


class DatabaseTests(unittest.TestCase):
    def test_transactions_commit_and_rollback(self):
        db = Database(":memory:")
        db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)")
        with db.transaction():
            db.execute("INSERT INTO items(value) VALUES (?)", ("kept",))
        with self.assertRaises(RuntimeError):
            with db.transaction():
                db.execute("INSERT INTO items(value) VALUES (?)", ("rolled back",))
                raise RuntimeError("rollback")
        rows = db.execute("SELECT value FROM items ORDER BY id").fetchall()
        self.assertEqual([row["value"] for row in rows], ["kept"])
        db.close()


class AuthTests(unittest.TestCase):
    def test_scrypt_password_and_signed_expiring_session(self):
        hashed = hash_password("correct horse")
        self.assertTrue(verify_password("correct horse", hashed))
        self.assertFalse(verify_password("incorrect", hashed))
        self.assertFalse(verify_password("x", "malformed"))
        signer = SessionSigner(b"x" * 32, max_age=60)
        token = signer.dumps({"user_id": 9}, now=1000)
        self.assertEqual(signer.loads(token, now=1020), {"user_id": 9})
        self.assertIsNone(signer.loads(token[:-1] + ("A" if token[-1] != "A" else "B"), now=1020))
        self.assertIsNone(signer.loads(token, now=1061))
        cookie = session_cookie("session", token, secure=True)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Strict", cookie)
        with self.assertRaises(ValueError): SessionSigner(b"weak")


class TemplateTests(unittest.TestCase):
    def test_escaped_render_and_path_containment(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "page.html").write_text("<p>$name</p>", encoding="utf-8")
            app = Limbo(); app.template_directory(directory)
            response = app.render_template("page.html", name="<script>alert(1)</script>")
            self.assertIn(b"&lt;script&gt;", response.body)
            with self.assertRaises(ValueError): app.templates.render("../outside.html")


class WebSocketTests(unittest.TestCase):
    def test_rfc_handshake_known_vector(self):
        status, headers = handshake_response({
            "connection": "keep-alive, Upgrade", "upgrade": "websocket",
            "sec-websocket-version": "13", "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ==",
        })
        self.assertEqual(status, 101)
        self.assertEqual(headers["Sec-WebSocket-Accept"], "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")
        self.assertEqual(handshake_response({"upgrade": "websocket"})[0], 400)

    def test_masked_client_frame_receive_and_server_reply(self):
        server, client = socket.socketpair()
        try:
            ws = WebSocket(server)
            payload, mask = b"hey", b"abcd"
            masked = bytes(ch ^ mask[i % 4] for i, ch in enumerate(payload))
            client.sendall(bytes([0x81, 0x80 | len(payload)]) + mask + masked)
            self.assertEqual(ws.receive(), "hey")
            ws.send("hello")
            header = client.recv(2)
            self.assertEqual(header, bytes([0x81, 5]))
            self.assertEqual(client.recv(5), b"hello")
        finally:
            server.close(); client.close()

    def test_real_socket_upgrade_and_echo(self):
        app = Limbo()
        @app.websocket("/ws/{room}")
        def echo(req, ws):
            ws.send(req.path_params["room"] + ":" + str(ws.receive()))
        server = LimboServer(app.dispatch, "127.0.0.1", 0, websocket_handler=app.dispatch_websocket)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        deadline = time.time() + 2
        while server.port == 0 and time.time() < deadline: time.sleep(.01)
        try:
            client = socket.create_connection(("127.0.0.1", server.port), timeout=2)
            client.settimeout(2)
            key = "dGhlIHNhbXBsZSBub25jZQ=="
            client.sendall(("GET /ws/general HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                            "Connection: Upgrade\r\nSec-WebSocket-Key: " + key + "\r\n"
                            "Sec-WebSocket-Version: 13\r\n\r\n").encode())
            response = b""
            while b"\r\n\r\n" not in response: response += client.recv(4096)
            self.assertIn(b"101 Switching Protocols", response)
            self.assertIn(b"s3pPLMBiTxaQ9kYGzzhZRbK+xOo=", response)
            payload, mask = b"hi", b"WXYZ"
            masked = bytes(ch ^ mask[i % 4] for i, ch in enumerate(payload))
            client.sendall(bytes([0x81, 0x80 | len(payload)]) + mask + masked)
            header = client.recv(2)
            self.assertEqual(header[0] & 0x0F, 1)
            data = client.recv(header[1] & 0x7F)
            self.assertEqual(data, b"general:hi")
            client.close()
        finally:
            server.shutdown(); thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

class HTTPParserSecurityTests(unittest.TestCase):
    def setUp(self):
        self.server = LimboServer(lambda request: Response.text("ok"), max_header_bytes=4096,
                                  max_body_bytes=32, max_header_count=4)

    def parse(self, raw):
        return self.server._read_request(io.BytesIO(raw), ("127.0.0.1", 12345))

    def assert_rejected(self, raw, status=400):
        with self.assertRaises(HTTPError) as caught:
            self.parse(raw)
        self.assertEqual(caught.exception.status, status)

    def test_rejects_duplicate_content_length_and_host(self):
        self.assert_rejected(
            b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 1\r\n"
            b"Content-Length: 1\r\n\r\nx"
        )
        self.assert_rejected(b"GET / HTTP/1.1\r\nHost: a\r\nHost: b\r\n\r\n")

    def test_rejects_conflicting_or_unsupported_transfer_encoding(self):
        self.assert_rejected(
            b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
        )
        self.assert_rejected(
            b"POST / HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: gzip, chunked\r\n\r\n",
            501,
        )

    def test_rejects_non_decimal_content_length_and_body_overflow(self):
        self.assert_rejected(b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: +1\r\n\r\nx")
        self.assert_rejected(
            b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 33\r\n\r\n",
            413,
        )

    def test_rejects_lf_only_and_malformed_request_targets(self):
        self.assert_rejected(b"GET / HTTP/1.1\nHost: localhost\n\n")
        self.assert_rejected(b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n")
        self.assert_rejected(b"GET /path#fragment HTTP/1.1\r\nHost: localhost\r\n\r\n")

    def test_rejects_empty_host_and_too_many_headers(self):
        self.assert_rejected(b"GET / HTTP/1.1\r\nHost: \r\n\r\n")
        self.assert_rejected(
            b"GET / HTTP/1.1\r\nHost: localhost\r\nA: 1\r\nB: 2\r\n"
            b"C: 3\r\nD: 4\r\n\r\n",
            431,
        )

    def test_chunked_body_and_trailers_are_validated(self):
        valid = self.parse(
            b"POST / HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"3\r\nabc\r\n0\r\nX-Checksum: yes\r\n\r\n"
        )
        self.assertEqual(valid.body, b"abc")
        self.assert_rejected(
            b"POST / HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"z\r\n",
        )
        self.assert_rejected(
            b"POST / HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"0\r\nContent-Length: 0\r\n\r\n",
        )

    def test_chunked_body_limit_is_enforced(self):
        self.assert_rejected(
            b"POST / HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"21\r\n",
            413,
        )

    def test_server_rejects_invalid_resource_limits(self):
        with self.assertRaises(ValueError):
            LimboServer(lambda request: Response.text("ok"), request_timeout=0)
        with self.assertRaises(ValueError):
            LimboServer(lambda request: Response.text("ok"), max_header_count=0)



class HTTPCompletionTests(unittest.TestCase):
    def setUp(self):
        self.server = LimboServer(lambda request: Response.text("ok"), max_header_bytes=4096)

    def parse(self, raw, trusted_hosts=None):
        server = LimboServer(lambda request: Response.text("ok"), max_header_bytes=4096, trusted_hosts=trusted_hosts)
        return server._read_request(io.BytesIO(raw), ("127.0.0.1", 1234))

    def test_host_authority_validation_and_allowlist(self):
        valid = self.parse(b"GET / HTTP/1.1\\r\\nHost: localhost:8080\\r\\n\\r\\n", ["localhost"])
        self.assertEqual(valid.host, "localhost:8080")
        for host in (b"user@localhost", b"localhost/path", b"localhost:99999", b"bad host", b"-invalid.example"):
            with self.subTest(host=host):
                with self.assertRaises(HTTPError):
                    self.parse(b"GET / HTTP/1.1\\r\\nHost: " + host + b"\\r\\n\\r\\n")
        with self.assertRaises(HTTPError):
            self.parse(b"GET / HTTP/1.1\\r\\nHost: evil.example\\r\\n\\r\\n", ["localhost"])

    def test_deadline_rejects_slow_drip_header(self):
        left, right = socket.socketpair()
        stream = left.makefile("rb")
        try:
            left.settimeout(1)
            right.sendall(b"GET / HTTP/1.1\\r\\nHost:")
            started = time.monotonic()
            with self.assertRaises((TimeoutError, OSError)):
                self.server._read_request(stream, ("127.0.0.1", 1), deadline=time.monotonic() + 0.05)
            self.assertLess(time.monotonic() - started, 0.5)
        finally:
            stream.close(); left.close(); right.close()

    def test_response_framing_rejects_conflicts_and_wrong_lengths(self):
        for response in (
            Response(b"x", 200, {"Content-Length": "2"}),
            Response(iter([b"x"]), 200, {"Content-Length": "1", "Transfer-Encoding": "chunked"}),
            Response(iter([b"x"]), 200, {"Content-Length": "2"}),
            Response(iter([b"xx"]), 200, {"Content-Length": "1"}),
        ):
            left, right = socket.socketpair()
            try:
                with self.assertRaises(ValueError):
                    self.server._write_response(left, response, "GET", False)
            finally:
                left.close(); right.close()

    def test_streaming_response_uses_valid_chunked_framing(self):
        left, right = socket.socketpair()
        try:
            self.server._write_response(left, Response(iter([b"abc", b"de"])), "GET", False)
            data = right.recv(4096)
            self.assertIn(b"transfer-encoding: chunked", data.lower())
            self.assertTrue(data.endswith(b"3\\r\\nabc\\r\\n2\\r\\nde\\r\\n0\\r\\n\\r\\n"))
        finally:
            left.close(); right.close()

    def test_route_middleware_and_custom_error_handlers(self):
        app = Limbo()
        calls = []
        def auth(req, next_handler):
            calls.append("before")
            result = next_handler(req)
            calls.append("after")
            return result
        @app.get("/private", middleware=(auth,))
        def private(req):
            calls.append("handler")
            return "secret"
        self.assertEqual(app.dispatch(Request("GET", "/private", {})).body, b"secret")
        self.assertEqual(calls, ["before", "handler", "after"])
        @app.exception_handler(404)
        def missing(error):
            return Response.json({"custom": True}, 404)
        self.assertEqual(app.dispatch(Request("GET", "/missing", {})).status, 404)
        self.assertEqual(app.dispatch(Request("GET", "/missing", {})).body, b'{"custom":true}')

    def test_exception_class_handler(self):
        app = Limbo()
        @app.get("/boom")
        def boom(req):
            raise LookupError("missing record")
        @app.exception_handler(LookupError)
        def lookup(error):
            return Response.text("handled: " + str(error), 418)
        result = app.dispatch(Request("GET", "/boom", {}))
        self.assertEqual(result.status, 418)
        self.assertEqual(result.body, b"handled: missing record")
