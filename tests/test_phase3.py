import socket
import threading
import time
import unittest
from unittest import mock

from limbo import Limbo, Request, Response, SessionStore, session_middleware
from limbo.server import LimboServer


def cookie_value(response, name="limbo_session"):
    for item in response.cookies:
        if item.startswith(name + "="):
            return item.split(";", 1)[0].split("=", 1)[1]
    return None


class SessionMiddlewareTests(unittest.TestCase):
    def test_create_read_update_and_rotate_session(self):
        store = SessionStore(max_age=120)
        app = Limbo()
        app.middleware(session_middleware(store, secure=False))

        @app.post("/login")
        def login(request):
            request.session["user_id"] = 42
            return {"ok": True}

        @app.get("/me")
        def me(request):
            return {"user_id": request.session.get("user_id")}

        first = app.dispatch(Request("POST", "/login", {}))
        session_id = cookie_value(first)
        self.assertTrue(SessionStore.valid_id(session_id))
        self.assertIn("HttpOnly", first.cookies[0])
        self.assertIn("SameSite=Lax", first.cookies[0])

        read = app.dispatch(Request("GET", "/me", {"cookie": "limbo_session=" + session_id}))
        self.assertEqual(read.body, b'{"user_id":42}')
        self.assertEqual(read.cookies, [])

        @app.post("/rotate")
        def rotate(request):
            request.session.regenerate()
            return {"ok": True}

        rotated = app.dispatch(Request("POST", "/rotate", {"cookie": "limbo_session=" + session_id}))
        new_id = cookie_value(rotated)
        self.assertTrue(SessionStore.valid_id(new_id))
        self.assertNotEqual(session_id, new_id)
        self.assertIsNone(store.load(session_id))
        self.assertEqual(store.load(new_id), {"user_id": 42})

    def test_destroy_revokes_session_and_expires_cookie(self):
        store = SessionStore()
        session_id = "A" * 43
        store.save(session_id, {"user": "alice"})
        app = Limbo()
        app.middleware(session_middleware(store, secure=False))

        @app.post("/logout")
        def logout(request):
            request.session.destroy()
            return Response.text("bye")

        response = app.dispatch(Request("POST", "/logout", {
            "cookie": "limbo_session=" + session_id
        }))
        self.assertIsNone(store.load(session_id))
        self.assertIn("Max-Age=0", response.cookies[0])

    def test_invalid_cookie_is_expired_and_not_loaded(self):
        app = Limbo()
        app.middleware(session_middleware(secure=False))

        @app.get("/")
        def home(request):
            self.assertEqual(dict(request.session), {})
            return "ok"

        response = app.dispatch(Request("GET", "/", {"cookie": "limbo_session=bad"}))
        self.assertIn("Max-Age=0", response.cookies[0])

    def test_store_is_bounded_and_rejects_bad_ids(self):
        store = SessionStore(max_sessions=1)
        first, second = "A" * 43, "B" * 43
        store.save(first, {"n": 1})
        store.save(second, {"n": 2})
        self.assertIsNone(store.load(first))
        self.assertEqual(store.load(second), {"n": 2})
        with self.assertRaises(ValueError):
            store.save("../bad", {})

    def test_configuration_validation_and_samesite_none_requires_secure(self):
        with self.assertRaises(ValueError):
            session_middleware(cookie_name="bad;name")
        with self.assertRaises(ValueError):
            session_middleware(same_site="Invalid")
        response = Limbo()
        response.middleware(session_middleware(same_site="None"))
        @response.get("/")
        def home(request):
            request.session["x"] = 1
            return "ok"
        result = response.dispatch(Request("GET", "/", {}))
        self.assertIn("Secure", result.cookies[0])


if __name__ == "__main__":
    unittest.main()


class RequestConvenienceTests(unittest.TestCase):
    def test_header_query_path_cookie_and_form_accessors(self):
        request = Request("POST", "/items/7?q=first&q=second", {
            "X-Trace": "abc", "cookie": "theme=dark",
            "content-type": "application/x-www-form-urlencoded",
        }, b"name=limbo")
        request.path_params["item_id"] = "7"
        self.assertEqual(request.header("x-trace"), "abc")
        self.assertEqual(request.query("q"), "first")
        self.assertEqual(request.require_query("q"), "first")
        self.assertEqual(request.path_param("item_id"), "7")
        self.assertEqual(request.cookie("theme"), "dark")
        self.assertEqual(request.form_value("name"), "limbo")

    def test_required_query_and_json_validation_use_http_errors(self):
        from limbo.http import HTTPError
        with self.assertRaises(HTTPError) as missing:
            Request("GET", "/", {}).require_query("id")
        self.assertEqual(missing.exception.status, 400)
        with self.assertRaises(HTTPError) as malformed:
            Request("POST", "/", {"content-type": "application/json"}, b"{bad").json_as()
        self.assertEqual(malformed.exception.status, 400)
        with self.assertRaises(HTTPError) as wrong_type:
            Request("POST", "/", {}, b'["not","object"]').json_as(dict)
        self.assertEqual(wrong_type.exception.status, 422)

    def test_json_and_upload_accessors(self):
        request = Request("POST", "/", {"content-type": "application/json"}, b'{"ok":true}')
        self.assertEqual(request.json_as(), {"ok": True})
        self.assertIsNone(request.file("missing"))


class ApplicationLifecycleTests(unittest.TestCase):
    def test_startup_shutdown_hooks_and_task_cleanup(self):
        events = []
        app = Limbo()
        @app.on_startup
        def first():
            events.append("startup")

        @app.on_shutdown
        def shutdown_first():
            events.append("shutdown-first")

        @app.on_shutdown
        def shutdown_second():
            events.append("shutdown-second")

        with mock.patch("limbo.app.LimboServer") as server_class:
            server_class.return_value.serve_forever.return_value = None
            app.run()
        self.assertEqual(events, ["startup", "shutdown-second", "shutdown-first"])
        self.assertTrue(app.background_tasks.closed)

    def test_shutdown_requests_server_stop(self):
        app = Limbo()
        fake = mock.Mock()
        app._server = fake
        app.shutdown()
        fake.shutdown.assert_called_once_with()


class WebSocketOriginPolicyTests(unittest.TestCase):
    def test_exact_origin_allowlist_and_invalid_configuration(self):
        app = Limbo()
        @app.websocket("/ws", origins=("https://app.example",))
        def socket_handler(request, websocket):
            pass
        self.assertTrue(app.websocket_origin_allowed(Request("GET", "/ws", {"origin": "https://app.example"})))
        self.assertFalse(app.websocket_origin_allowed(Request("GET", "/ws", {"origin": "https://evil.example"})))
        self.assertFalse(app.websocket_origin_allowed(Request("GET", "/ws", {})))
        with self.assertRaises(ValueError):
            app.websocket("/bad", origins=("*",))

    def test_origin_policy_rejects_before_websocket_upgrade(self):
        app = Limbo()
        @app.websocket("/ws", origins=("https://allowed.example",))
        def socket_handler(request, websocket):
            websocket.send("should not run")
        server = LimboServer(app.dispatch, "127.0.0.1", 0,
                             websocket_handler=app.dispatch_websocket,
                             websocket_origin_check=app.websocket_origin_allowed)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        deadline = time.monotonic() + 2
        while server.port == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        try:
            with socket.create_connection(("127.0.0.1", server.port), timeout=2) as client:
                client.sendall(
                    b"GET /ws HTTP/1.1\r\nHost: localhost\r\nOrigin: https://evil.example\r\n"
                    b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                    b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    b"Sec-WebSocket-Version: 13\r\n\r\n"
                )
                response = client.recv(4096)
                self.assertIn(b"403 Forbidden", response)
                self.assertNotIn(b"101 Switching Protocols", response)
        finally:
            server.shutdown()
            thread.join(timeout=2)
