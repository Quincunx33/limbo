"""Optional Cloudflare Tunnel supervision.

Limbo detects and supervises an official ``cloudflared`` executable the user
already installed. It does not download binaries, does not speak the tunnel
protocol, and does not create Cloudflare credentials.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

_QUICK_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_SECRET = re.compile(
    r"(?i)\b(token|password|secret|authorization|api[_-]?key|tunnel-credentials-file|credentials-file)\b(\s*[=:]\s*|\s+)(\S+)"
)
_BEARER = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9\-._~+/]+=*)")
_FLAG = re.compile(r"(?i)(--token|--credentials-file)(=|\s+)(\S+)")


class TunnelError(RuntimeError):
    """The tunnel cannot be started or the request is refused."""


@dataclass
class TunnelSupervisor:
    """Run one cloudflared process and keep a bounded redacted log."""

    executable: str
    target: str
    mode: str = "quick"
    name: str | None = None
    config_path: str | None = None
    log_limit: int = 8192
    popen: object = subprocess.Popen
    warnings: tuple[str, ...] = ()
    url: str | None = None
    proc: subprocess.Popen | None = None
    _chunks: list[str] = field(default_factory=list)
    _thread: threading.Thread | None = None

    def argv(self) -> list[str]:
        if self.mode == "quick":
            return [self.executable, "tunnel", "--no-autoupdate", "--url", self.target]
        if self.mode != "named":
            raise TunnelError(f"Unknown tunnel mode: {self.mode}")
        if not self.name or not self.config_path:
            raise TunnelError(
                "Named tunnels require [tunnel].name and [tunnel].config pointing at your "
                "existing cloudflared configuration. Limbo does not create credentials."
            )
        return [self.executable, "tunnel", "--config", self.config_path, "--no-autoupdate", "run", self.name]

    def start(self, argv: list[str] | None = None) -> None:
        if self.proc is not None:
            raise TunnelError("Tunnel process is already running.")
        command = argv if argv is not None else self.argv()
        try:
            self.proc = self.popen(  # type: ignore[operator]
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except OSError as exc:
            raise TunnelError(f"Could not start tunnel process: {exc}") from exc
        if self.proc.stdout is not None:
            self._thread = threading.Thread(target=self._read, name="limbo-tunnel-log", daemon=True)
            self._thread.start()

    def _read(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            redacted = redact_secrets(line)
            self._chunks.append(redacted)
            blob = "".join(self._chunks)
            if len(blob) > self.log_limit:
                self._chunks = [blob[-self.log_limit:]]
            if self.url is None:
                found = _QUICK_URL.search(line)
                if found:
                    self.url = found.group(0)

    def wait_for_url(self, timeout: float = 2.0) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.url:
                return self.url
            if self.proc is not None and self.proc.poll() is not None:
                break
            time.sleep(0.02)
        return self.url

    def log_text(self) -> str:
        return "".join(self._chunks)

    def stop(self) -> None:
        proc = self.proc
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        if proc.stdout is not None:
            proc.stdout.close()
        if self._thread is not None:
            self._thread.join(timeout=1)


def detect_cloudflared(explicit: str | None = None) -> str | None:
    """Return a local cloudflared path. Never downloads one."""
    if explicit:
        candidate = Path(explicit).expanduser()
        return str(candidate) if candidate.is_file() else None
    return shutil.which("cloudflared")


def cloudflared_version(executable: str) -> str:
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TunnelError(f"Could not read cloudflared version: {exc}") from exc
    text = redact_secrets((result.stdout or result.stderr or "").strip())
    line = text.splitlines()[0] if text else "version unavailable"
    return line[:200]


def redact_secrets(text: str) -> str:
    """Remove credential-shaped values from tunnel logs and errors."""
    text = _FLAG.sub(lambda match: match.group(1) + match.group(2) + "[redacted]", text)
    text = _BEARER.sub(lambda match: match.group(1) + "[redacted]", text)
    text = _SECRET.sub(lambda match: match.group(1) + match.group(2) + "[redacted]", text)
    return text


def exposure_warnings(*, host: str, debug: bool = False, csrf: bool | None = None) -> list[str]:
    lines = ["Public tunnel: this process can expose the local server to the internet."]
    if host not in {"127.0.0.1", "localhost", "::1"}:
        lines.append(f"Server host is {host}, not loopback. Confirm that is intentional.")
    if debug:
        lines.append("Debug mode is enabled. Turn it off before publishing a tunnel.")
    if csrf is False:
        lines.append("CSRF protection is explicitly disabled.")
    return lines


def missing_tool_message() -> str:
    return (
        "cloudflared was not found. Limbo does not download it. Install the official "
        "cloudflared binary from Cloudflare's documentation, then rerun `limbo dev --public`."
    )


def plan_delete(*, confirm: bool, name: str) -> list[str]:
    """Return the destructive argv only after an explicit confirmation."""
    if not name or any(char.isspace() for char in name):
        raise TunnelError("Tunnel name must be a single non-empty token.")
    if not confirm:
        raise TunnelError(
            "Refusing to delete a named tunnel without --confirm. "
            "This can remove a Cloudflare tunnel and is not run automatically."
        )
    return ["cloudflared", "tunnel", "delete", name]


def prepare_quick_tunnel(executable: str | None, *, host: str, port: int, debug: bool = False,
                         csrf: bool | None = None) -> TunnelSupervisor:
    if not executable:
        raise TunnelError(missing_tool_message())
    warnings = tuple(exposure_warnings(host=host, debug=debug, csrf=csrf))
    target = f"http://{host}:{port}"
    return TunnelSupervisor(executable, target, mode="quick", warnings=warnings)
