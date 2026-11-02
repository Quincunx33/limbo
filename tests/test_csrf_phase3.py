import unittest

from limbo import Limbo, Request, csrf_protect


class CSRFTests(unittest.TestCase):
    def setUp(self):
        self.app = Limbo()
        self.app.middleware(csrf_protect(b"c" * 32, secure=False))

        @self.app.get("/form")
        def form(request):
            return {"token": request.csrf_token}

        @self.app.post("/submit")
        def submit(request):
            return {"accepted": True}

    @staticmethod
    def cookie(response):
        return next(item.split(";", 1)[0].split("=", 1)[1]
                    for item in response.cookies if item.startswith("limbo_csrf="))

    def test_safe_request_issues_signed_readable_cookie_and_token(self):
        response = self.app.dispatch(Request("GET", "/form", {}))
        self.assertEqual(response.status, 200)
        self.assertIn("token", response.body.decode())
        self.assertIn("SameSite=Lax", response.cookies[0])
        self.assertNotIn("HttpOnly", response.cookies[0])
        self.assertEqual(len(self.cookie(response).split(".")), 2)

    def test_valid_header_token_allows_unsafe_request(self):
        first = self.app.dispatch(Request("GET", "/form", {}))
        cookie = self.cookie(first)
        token = cookie.split(".", 1)[0]
        response = self.app.dispatch(Request("POST", "/submit", {
            "cookie": "limbo_csrf=" + cookie,
            "x-csrf-token": token,
        }))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b'{"accepted":true}')

    def test_missing_or_wrong_token_is_rejected(self):
        self.assertEqual(self.app.dispatch(Request("POST", "/submit", {})).status, 403)
        first = self.app.dispatch(Request("GET", "/form", {}))
        cookie = self.cookie(first)
        response = self.app.dispatch(Request("POST", "/submit", {
            "cookie": "limbo_csrf=" + cookie,
            "x-csrf-token": "wrong",
        }))
        self.assertEqual(response.status, 403)

    def test_urlencoded_form_token_is_supported(self):
        first = self.app.dispatch(Request("GET", "/form", {}))
        cookie = self.cookie(first)
        token = cookie.split(".", 1)[0]
        response = self.app.dispatch(Request("POST", "/submit", {
            "cookie": "limbo_csrf=" + cookie,
            "content-type": "application/x-www-form-urlencoded",
        }, b"_csrf=" + token.encode("ascii")))
        self.assertEqual(response.status, 200)

    def test_tampered_cookie_is_rejected(self):
        first = self.app.dispatch(Request("GET", "/form", {}))
        cookie = self.cookie(first)
        token = cookie.split(".", 1)[0]
        bad_sig = "A" * 43 if cookie.split(".", 1)[1] != "A" * 43 else "B" * 43
        response = self.app.dispatch(Request("POST", "/submit", {
            "cookie": "limbo_csrf=" + token + "." + bad_sig,
            "x-csrf-token": token,
        }))
        self.assertEqual(response.status, 403)

    def test_configuration_requires_strong_secret_and_valid_cookie_policy(self):
        with self.assertRaises(ValueError):
            csrf_protect(b"weak")
        with self.assertRaises(ValueError):
            csrf_protect(b"x" * 32, same_site="bad")
        app = Limbo()
        app.middleware(csrf_protect(b"x" * 32, same_site="None"))
        @app.get("/")
        def home(request):
            return "ok"
        self.assertIn("Secure", app.dispatch(Request("GET", "/", {})).cookies[0])


if __name__ == "__main__":
    unittest.main()
