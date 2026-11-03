"""Phase 9 optional cloudflared supervision."""
import sys
import unittest
from unittest.mock import patch

from limbo.cli import main
from limbo.tunnel import (
    TunnelSupervisor, missing_tool_message, plan_delete, prepare_quick_tunnel, redact_secrets,
)


class TunnelTests(unittest.TestCase):
    def test_redaction_hides_tokens_but_keeps_the_quick_url(self):
        raw = "ready https://demo-box.trycloudflare.com\ntoken=super-secret-value\nAuthorization: Bearer abc.def\n--token=anothersecret\n"
        cleaned = redact_secrets(raw)
        self.assertIn("https://demo-box.trycloudflare.com", cleaned)
        self.assertNotIn("super-secret-value", cleaned)
        self.assertNotIn("abc.def", cleaned)
        self.assertNotIn("anothersecret", cleaned)
        self.assertIn("[redacted]", cleaned)

    def test_quick_tunnel_process_is_stopped_and_url_is_captured(self):
        code = (
            "import time\n"
            "print('https://unit-test.trycloudflare.com', flush=True)\n"
            "print('token=s3cr3tvalue', flush=True)\n"
            "time.sleep(30)\n"
        )
        supervisor = TunnelSupervisor(sys.executable, "http://127.0.0.1:8000")
        supervisor.start(argv=[sys.executable, "-c", code])
        try:
            self.assertEqual(supervisor.wait_for_url(2), "https://unit-test.trycloudflare.com")
            self.assertNotIn("s3cr3tvalue", supervisor.log_text())
            self.assertIsNone(supervisor.proc.poll())
        finally:
            supervisor.stop()
        self.assertIsNotNone(supervisor.proc.poll())

    def test_missing_tool_and_unconfirmed_delete(self):
        with self.assertRaisesRegex(Exception, "does not download"):
            prepare_quick_tunnel(None, host="127.0.0.1", port=8000)
        with self.assertRaisesRegex(Exception, "--confirm"):
            plan_delete(confirm=False, name="prod")
        argv = plan_delete(confirm=True, name="prod")
        self.assertEqual(argv[-2:], ["delete", "prod"])
        self.assertIn("cloudflared", missing_tool_message())

    def test_cli_public_and_delete_do_not_download_or_execute(self):
        with patch("limbo.cli.detect_cloudflared", return_value=None):
            errors_public = __import__("io").StringIO()
            with __import__("contextlib").redirect_stderr(errors_public):
                status = main(["dev", "--public"])
            self.assertEqual(status, 2)
            self.assertIn("does not download", errors_public.getvalue())
            errors_delete = __import__("io").StringIO()
            with __import__("contextlib").redirect_stderr(errors_delete):
                status = main(["tunnel", "delete", "prod"])
            self.assertEqual(status, 2)
            self.assertIn("--confirm", errors_delete.getvalue())


class FuzzTests(unittest.TestCase):
    def test_redact_and_sql_split_do_not_crash(self):
        import random
        from limbo.css_scanner import scan_css_dependencies
        from limbo.migrations import split_sql
        rng = random.Random(9)
        for _ in range(40):
            blob = "".join(chr(rng.randrange(32, 127)) for _ in range(rng.randrange(0, 120)))
            redact_secrets(blob)
            split_sql(blob)
            scan_css_dependencies(blob)


if __name__ == "__main__":
    unittest.main()
