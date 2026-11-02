import unittest

from limbo import Limbo, Request, authenticate, authorize


class AuthenticationExtensionTests(unittest.TestCase):
    def test_authenticate_attaches_user_and_authorize_checks_policy(self):
        app = Limbo()
        app.middleware(authenticate(lambda request: {"id": 7}))
        app.middleware(authorize(lambda request, user: user["id"] == 7))

        @app.get("/private")
        def private(request):
            return {"user": request.user["id"]}

        response = app.dispatch(Request("GET", "/private", {}))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b'{"user":7}')

    def test_missing_user_and_denied_policy_return_expected_status(self):
        app = Limbo()
        app.middleware(authenticate(lambda request: None))
        @app.get("/")
        def home(request):
            return "should not run"
        self.assertEqual(app.dispatch(Request("GET", "/", {})).status, 401)

        denied = Limbo()
        denied.middleware(authenticate(lambda request: "alice"))
        denied.middleware(authorize(lambda request, user: False))
        @denied.get("/")
        def secret(request):
            return "secret"
        self.assertEqual(denied.dispatch(Request("GET", "/", {})).status, 403)

    def test_custom_failure_responses_and_validation(self):
        with self.assertRaises(TypeError):
            authenticate(None)
        with self.assertRaises(TypeError):
            authorize(None)
        app = Limbo()
        app.middleware(authenticate(lambda request: None,
                                    unauthorized=lambda request: Limbo.json({"error": "login"}, 401)))
        @app.get("/")
        def home(request):
            return "never"
        response = app.dispatch(Request("GET", "/", {}))
        self.assertEqual(response.body, b'{"error":"login"}')


if __name__ == "__main__":
    unittest.main()
