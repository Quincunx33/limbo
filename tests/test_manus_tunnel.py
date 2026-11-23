import tempfile
import unittest
from pathlib import Path

from limbo.cli import main
from limbo.manus_tunnel import ManusTunnelClient, _b64url, _unb64url


class ManusTunnelTests(unittest.TestCase):
    def test_base64url_roundtrip(self):
        value = b"Limbo tunnel payload"
        self.assertEqual(_unb64url(_b64url(value)), value)

    def test_private_key_client_exposes_canonical_der_public_key(self):
        try:
            from cryptography.hazmat.primitives.asymmetric import ed25519
            from cryptography.hazmat.primitives import serialization
        except ImportError:
            self.skipTest("cryptography optional dependency is not installed")
        key = ed25519.Ed25519PrivateKey.generate()
        client = ManusTunnelClient("ws://127.0.0.1:3000/tunnel", "client", key)
        expected = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.assertEqual(client.public_key, expected)
        client.stop()

    def test_cli_manus_requires_a_real_key_path(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "missing.pem")
            self.assertEqual(main(["tunnel", "manus", "--url", "ws://127.0.0.1:3000/tunnel", "--client-id", "client", "--key", missing, "--no-reconnect"]), 2)


if __name__ == "__main__":
    unittest.main()
