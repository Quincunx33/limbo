import socket
import threading
import time
import unittest

from limbo import Limbo, Request
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
                b"GET /ws HTTP/1.1\\r\\nHost: localhost\\r\\n"
                b"Upgrade: websocket\\r\\nConnection: Upgrade\\r\\n"
                b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\\r\\n"
                b"Sec-WebSocket-Version: 13\\r\\n\\r\\n"
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
            b"--BOUND\\r\\n"
            b'Content-Disposition: form-data; name="upload"; filename="x.bin"\\r\\n'
            b"Content-Type: application/octet-stream\\r\\n\\r\\n"
            + payload + b"\\r\\n--BOUND--\\r\\n"
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

    def test_upload_limit_configuration_validation(self):
        for kwargs in (
            {"max_body_bytes": -1},
            {"max_upload_file_bytes": 0},
            {"max_upload_files": 0},
            {"max_form_fields": 0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Limbo(**kwargs)


if __name__ == "__main__":
    unittest.main()
