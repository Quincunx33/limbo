import unittest

from limbo import Limbo, Request, Response, SessionStore, session_middleware


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
