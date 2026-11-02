import socket
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from limbo import (Limbo, Request, SessionBackend, SessionStore, authenticate,
                   authorize, session_middleware)
from limbo.server import LimboServer
from limbo.websocket import WebSocket


class WebSocketPolicyTests(unittest.TestCase):
    def test_route_policy_validation_and_resolution(self):
        app = Limbo()

        with self.assertRaises(ValueError):
            app.websocket("/bad", max_message_bytes=0)
        with self.assertRaises(ValueError):
            app.websocket("/bad", idle_timeout=0)

        @app.websocket("/ws/{room}", origins=("https://app.example",),
                       max_message_bytes=1024, idle_timeout=0.25)
        def handler(request, websocket):
            pass

        request = Request("GET", "/ws/general", {"origin": "https://app.example"})
        self.assertTrue(app.websocket_origin_allowed(request))
        self.assertEqual(app.websocket_options(request), {
            "max_message_bytes": 1024, "idle_timeout": 0.25
        })

    def test_send_rejects_messages_over_route_limit(self):
        left, right = socket.socketpair()
        try:
            ws = WebSocket(left, max_message_bytes=3)
            with self.assertRaisesRegex(ValueError, "too large"):
                ws.send("four")
        finally:
            left.close()
            right.close()

    def test_receive_rejects_oversized_client_message(self):
        server_sock, client_sock = socket.socketpair()
        try:
            ws = WebSocket(server_sock, max_message_bytes=3)
            mask = b"abcd"
            payload = b"four"
            masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
            client_sock.sendall(bytes((0x81, 0x80 | len(payload))) + mask + masked)
            self.assertIsNone(ws.receive())
            close_frame = client_sock.recv(64)
            self.assertEqual(close_frame[0] & 0x0F, 8)
            self.assertEqual(int.from_bytes(close_frame[2:4], "big"), 1009)
        finally:
            server_sock.close()
            client_sock.close()

    def test_idle_timeout_closes_with_going_away_code(self):
        app = Limbo()

        @app.websocket("/ws", max_message_bytes=64, idle_timeout=0.15)
        def handler(request, websocket):
            websocket.receive()

        server = LimboServer(
            app.dispatch, "127.0.0.1", 0,
            websocket_handler=app.dispatch_websocket,
            websocket_origin_check=app.websocket_origin_allowed,
            websocket_options=app.websocket_options,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        deadline = time.monotonic() + 2
        while server.port == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        client = socket.create_connection(("127.0.0.1", server.port), timeout=2)
        client.settimeout(2)
        try:
            client.sendall(
                b"GET /ws HTTP/1.1\r\nHost: localhost\r\n"
                b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                b"Sec-WebSocket-Version: 13\r\n\r\n"
            )
            response = client.recv(4096)
            self.assertIn(b"101 Switching Protocols", response)
            frame = client.recv(4096)
            self.assertGreaterEqual(len(frame), 4)
            self.assertEqual(frame[0] & 0x0F, 8)
            self.assertEqual(int.from_bytes(frame[2:4], "big"), 1001)
        finally:
            client.close()
            server.shutdown()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())


class UploadConfigurationTests(unittest.TestCase):
    @staticmethod
    def multipart(payload):
        return (
            b"--BOUND\r\n"
            b'Content-Disposition: form-data; name="upload"; filename="x.bin"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\n"
            + payload + b"\r\n--BOUND--\r\n"
        )

    def test_application_upload_limits_are_applied_to_request_parser(self):
        app = Limbo(max_body_bytes=4096, max_upload_file_bytes=3,
                    max_upload_files=1, max_form_fields=2)

        @app.post("/upload")
        def upload(request):
            return {"size": request.file("upload").size}

        request = Request("POST", "/upload", {
            "content-type": "multipart/form-data; boundary=BOUND"
        }, self.multipart(b"four"))
        response = app.dispatch(request)
        self.assertEqual(response.status, 413)

    def test_configured_file_and_form_count_limits_are_enforced(self):
        two_files = (
            b'--BOUND\\r\\nContent-Disposition: form-data; name="a"; filename="a.txt"\\r\\n\\r\\na\\r\\n'
            b'--BOUND\\r\\nContent-Disposition: form-data; name="b"; filename="b.txt"\\r\\n\\r\\nb\\r\\n'
            b'--BOUND--\\r\\n'
        )
        files_app = Limbo(max_upload_files=1)
        @files_app.post("/upload")
        def upload_files(request):
            return {"count": len(request.files)}
        self.assertEqual(files_app.dispatch(Request("POST", "/upload", {
            "content-type": "multipart/form-data; boundary=BOUND"
        }, two_files)).status, 413)

        two_fields = (
            b'--BOUND\\r\\nContent-Disposition: form-data; name="a"\\r\\n\\r\\na\\r\\n'
            b'--BOUND\\r\\nContent-Disposition: form-data; name="b"\\r\\n\\r\\nb\\r\\n'
            b'--BOUND--\\r\\n'
        )
        fields_app = Limbo(max_form_fields=1)
        @fields_app.post("/form")
        def upload_fields(request):
            return {"count": len(request.form)}
        self.assertEqual(fields_app.dispatch(Request("POST", "/form", {
            "content-type": "multipart/form-data; boundary=BOUND"
        }, two_fields)).status, 413)

    def test_upload_limit_configuration_validation(self):
        for kwargs in (
            {"max_body_bytes": -1},
            {"max_upload_file_bytes": 0},
            {"max_upload_files": 0},
            {"max_form_fields": 0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Limbo(**kwargs)


class AuthenticationExtensionTests(unittest.TestCase):
    def test_authentication_and_authorization_hooks(self):
        app = Limbo()
        require_user = authenticate(lambda request: {"id": 4} if request.cookie("auth") == "valid" else None)
        admin_only = authorize(lambda request, user: user["id"] == 4)

        @app.get("/private", middleware=(require_user, admin_only))
        def private(request):
            return {"user": request.user["id"]}

        self.assertEqual(app.dispatch(Request("GET", "/private", {})).status, 401)
        response = app.dispatch(Request("GET", "/private", {"cookie": "auth=valid"}))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b'{"user":4}')

        denied = authorize(lambda request, user: False)
        @app.get("/denied", middleware=(require_user, denied))
        def denied_route(request):
            return "unreachable"
        self.assertEqual(app.dispatch(Request("GET", "/denied", {"cookie": "auth=valid"})).status, 403)


class SessionBackendContractTests(unittest.TestCase):
    def test_store_expiry_and_concurrent_access(self):
        store = SessionStore(max_age=1, max_sessions=500)
        session_id = "S" * 43
        with mock.patch("limbo.sessions.time.monotonic", side_effect=[10.0, 12.0]):
            store.save(session_id, {"user": 7})
            self.assertIsNone(store.load(session_id))

        def write(index):
            key = str(index).replace("-", "_")
            identifier = (key + "x" * 43)[:43]
            store.save(identifier, {"index": index})
            return store.load(identifier)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(write, range(200)))
        self.assertEqual(len(results), 200)
        self.assertTrue(all(item is not None for item in results))

    def test_custom_persistent_adapter_satisfies_contract(self):
        class Adapter:
            max_age = 1800

            def __init__(self):
                self.data = {}

            def load(self, session_id):
                value = self.data.get(session_id)
                return dict(value) if value is not None else None

            def save(self, session_id, data):
                self.data[session_id] = dict(data)

            def delete(self, session_id):
                self.data.pop(session_id, None)

        adapter = Adapter()
        self.assertIsInstance(adapter, SessionBackend)
        app = Limbo()
        app.middleware(session_middleware(adapter, secure=False))

        @app.post("/login")
        def login(request):
            request.session["user_id"] = 9
            return {"ok": True}

        response = app.dispatch(Request("POST", "/login", {}))
        self.assertEqual(response.status, 200)
        self.assertEqual(len(adapter.data), 1)


if __name__ == "__main__":
    unittest.main()
