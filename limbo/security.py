"""Small authentication primitives; applications must add policy and authorization."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from http.cookies import SimpleCookie
from typing import Any


_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 1 << 14, 8, 1


def hash_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 1 or len(password) > 1024:
        raise ValueError("Password must contain 1–1024 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.urlsafe_b64encode(salt).decode().rstrip("="),
        base64.urlsafe_b64encode(digest).decode().rstrip("="),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$")
        if algorithm != "scrypt": return False
        n, r, p = int(n), int(r), int(p)
        if n < 2 or n > (1 << 16) or n & (n - 1) or not (1 <= r <= 16 and 1 <= p <= 4): return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = base64.urlsafe_b64decode(digest_text + "=" * (-len(digest_text) % 4))
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, UnicodeError, MemoryError):
        return False


class SessionSigner:
    """HMAC-signed stateless sessions; contents are signed, not encrypted."""
    def __init__(self, secret: bytes | str, *, max_age: int = 3600) -> None:
        self.secret = secret.encode() if isinstance(secret, str) else secret
        if len(self.secret) < 32:
            raise ValueError("Session secret must be at least 32 bytes")
        if not 1 <= max_age <= 31_536_000:
            raise ValueError("max_age must be between 1 second and one year")
        self.max_age = max_age

    def dumps(self, data: dict[str, Any], *, now: int | None = None) -> str:
        payload = json.dumps({"iat": int(now if now is not None else time.time()), "data": data},
                             ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        signature = hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).digest()
        return body + "." + base64.urlsafe_b64encode(signature).decode().rstrip("=")

    def loads(self, token: str, *, now: int | None = None) -> dict[str, Any] | None:
        try:
            if not isinstance(token, str) or len(token) > 16_384: return None
            body, signature_text = token.split(".", 1)
            signature = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
            expected = hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected): return None
            payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
            value = json.loads(payload)
            issued = int(value["iat"])
            current = int(now if now is not None else time.time())
            if issued > current + 60 or current - issued > self.max_age: return None
            if not isinstance(value["data"], dict): return None
            return value["data"]
        except (ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError):
            return None


def session_cookie(name: str, value: str, *, max_age: int = 3600, secure: bool = True,
                   same_site: str = "Strict", path: str = "/") -> str:
    if not isinstance(max_age, int) or not 0 <= max_age <= 31_536_000:
        raise ValueError("Cookie max_age must be between 0 and one year")
    if not name or any(ch in name for ch in "\r\n;=") or any(ch in path for ch in "\r\n;"):
        raise ValueError("Invalid cookie name or path")
    if same_site not in ("Strict", "Lax", "None"):
        raise ValueError("SameSite must be Strict, Lax, or None")
    cookie = SimpleCookie()
    cookie[name] = value
    morsel = cookie[name]
    morsel["path"] = path
    morsel["max-age"] = str(max_age)
    morsel["httponly"] = True
    morsel["samesite"] = same_site
    if secure or same_site == "None": morsel["secure"] = True
    return morsel.OutputString()
