import gzip
import unittest

from limbo import Limbo, Request, Response, access_log, cors, gzip_compress, rate_limit, request_id, security_headers


class RequestNegotiationTests(unittest.TestCase):
    def test_accept_quality_and_specificity(self):
        request = Request("GET", "/", {"accept": "text/*;q=1, text/html;q=0, application/json;q=0.8"})
        self.assertFalse(request.accepts("text/html"))
        self.assertTrue(request.accepts("text/plain"))
        self.assertTrue(request.accepts("application/json"))
        self.assertFalse(request.accepts("image/png"))

    def test_response_redirect_and_vary_helpers(self):
        response = Response.redirect("/login", 302)
        self.assertEqual(response.status, 302)
        self.assertEqual(response.headers["Location"], "/login")
        with self.assertRaises(ValueError):
            Response.redirect("/bad\r\nLocation: https://evil.example")
        response.add_vary("Origin").add_vary("Accept", "origin")
        self.assertEqual(response.headers["Vary"], "Origin, Accept")


class MiddlewareTests(unittest.TestCase):
    def test_cors_exact_origin_and_preflight(self):
        app = Limbo()
        app.middleware(cors(allow_origins=("https://app.example",),
                            allow_methods=("GET", "POST"),
                            allow_headers=("X-Token",),
                            allow_credentials=True, max_age=300))
        @app.get("/data")
        def data(req):
            return Response.json({"ok": True})
        response = app.dispatch(Request("GET", "/data", {"origin": "https://app.example"}))
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "https://app.example")
        self.assertEqual(response.headers["Access-Control-Allow-Credentials"], "true")
        self.assertIn("Origin", response.headers["Vary"])
        preflight = app.dispatch(Request("OPTIONS", "/data", {
            "origin": "https://app.example",
            "access-control-request-method": "POST",
            "access-control-request-headers": "x-token",
        }))
        self.assertEqual(preflight.status, 204)
        self.assertEqual(preflight.headers["Access-Control-Max-Age"], "300")
        denied = app.dispatch(Request("GET", "/data", {"origin": "https://evil.example"}))
        self.assertNotIn("Access-Control-Allow-Origin", denied.headers)

    def test_cors_rejects_unsafe_configuration_and_disallowed_preflight(self):
        with self.assertRaises(ValueError):
            cors(allow_origins=("*",), allow_credentials=True)
        with self.assertRaises(ValueError):
            cors(allow_origins=("https://example.com:99999",))
        app = Limbo()
        app.middleware(cors(allow_origins=("https://app.example",), allow_headers=("X-Allowed",)))
        response = app.dispatch(Request("OPTIONS", "/missing", {
            "origin": "https://app.example",
            "access-control-request-method": "POST",
            "access-control-request-headers": "x-forbidden",
        }))
        self.assertEqual(response.status, 403)

    def test_cors_denials_also_vary_by_origin_and_validate_requested_headers(self):
        middleware = cors(allow_origins=("https://app.example",), allow_headers=("X-Allowed",))
        denied_origin = middleware(Request("GET", "/", {"origin": "https://evil.example"}),
                                   lambda req: Response.text("ok"))
        self.assertIn("Origin", denied_origin.headers["Vary"])
        bad_header = middleware(Request("OPTIONS", "/", {
            "origin": "https://app.example",
            "access-control-request-method": "GET",
            "access-control-request-headers": "X-Allowed, bad header",
        }), lambda req: Response.text("unused"))
        self.assertEqual(bad_header.status, 403)
        self.assertIn("Origin", bad_header.headers["Vary"])

    def test_security_headers_do_not_override_application_policy(self):
        app = Limbo()
        app.middleware(security_headers(content_security_policy="default-src 'self'"))
        @app.get("/")
        def home(req):
            return Response("ok", headers={"X-Frame-Options": "SAMEORIGIN"})
        response = app.dispatch(Request("GET", "/", {}))
        self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Content-Security-Policy"], "default-src 'self'")

    def test_request_id_validation_and_access_log_sink(self):
        records = []
        app = Limbo()
        app.middleware(request_id())
        app.middleware(access_log(sink=records.append))
        @app.get("/")
        def home(req):
            return {"id": req.state["request_id"]}
        response = app.dispatch(Request("GET", "/", {"x-request-id": "client-123"}))
        self.assertEqual(response.headers["X-Request-ID"], "client-123")
        self.assertEqual(records[0]["request_id"], "client-123")
        invalid = app.dispatch(Request("GET", "/", {"x-request-id": "bad\r\nvalue"}))
        self.assertNotEqual(invalid.headers["X-Request-ID"], "bad\r\nvalue")
        self.assertLessEqual(len(invalid.headers["X-Request-ID"]), 128)
        self.assertEqual(len(records), 2)

    def test_gzip_compresses_bytes_and_streams_only_when_accepted(self):
        middleware = gzip_compress(minimum_size=32)
        payload = "Limbo response compression " * 30
        request = Request("GET", "/", {"accept-encoding": "gzip"})
        compressed = middleware(request, lambda req: Response.text(payload))
        self.assertEqual(compressed.headers["Content-Encoding"], "gzip")
        self.assertEqual(gzip.decompress(compressed.body).decode(), payload)
        self.assertNotIn("Content-Length", compressed.headers)
        self.assertIn("Accept-Encoding", compressed.headers["Vary"])

        streamed = middleware(request, lambda req: Response(
            iter([b"streamed ", b"payload " * 20]), 200, {"Content-Type": "text/plain"}))
        self.assertEqual(gzip.decompress(b"".join(streamed.body)), b"streamed " + b"payload " * 20)

        identity = middleware(Request("GET", "/", {"accept-encoding": "gzip;q=0"}),
                              lambda req: Response.text(payload))
        self.assertNotIn("Content-Encoding", identity.headers)
        self.assertEqual(identity.body, payload.encode())

    def test_access_log_sink_failure_does_not_replace_response(self):
        app = Limbo()
        def broken_sink(record):
            raise RuntimeError("logging backend unavailable")
        app.middleware(access_log(sink=broken_sink))
        @app.get("/")
        def home(req):
            return Response.text("still served")
        response = app.dispatch(Request("GET", "/", {}))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"still served")

    def test_rate_limit_is_bounded_and_returns_retry_after(self):
        limiter = rate_limit(capacity=1, refill_rate=0.01, max_keys=2)
        request = Request("GET", "/", {}, client=("192.0.2.1", 1234))
        first = limiter(request, lambda req: Response.text("ok"))
        second = limiter(request, lambda req: Response.text("ok"))
        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 429)
        self.assertIn("Retry-After", second.headers)
        with self.assertRaises(ValueError):
            rate_limit(capacity=0)


if __name__ == "__main__":
    unittest.main()
