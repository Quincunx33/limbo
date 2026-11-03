"""Local process supervisor for an optional SSR sidecar.

This is a lifecycle contract: start, wait until a readiness URL answers, forward
one HTTP request, and stop the child. It does not mean Limbo executes Next.js,
Nuxt, SvelteKit, or Remix. Those stay unsupported until a real app is tested.
"""
from __future__ import annotations

import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .tunnel import redact_secrets


class SsrRuntimeError(RuntimeError):
    """The local SSR process failed to start, become ready, or shut down."""


@dataclass
class LocalProcessRuntime:
    command: list[str]
    ready_url: str
    cwd: str | None = None
    startup_timeout: float = 5.0
    log_limit: int = 8192
    popen: object = subprocess.Popen
    proc: subprocess.Popen | None = None
    _chunks: list[str] = field(default_factory=list)
    _thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.command or not isinstance(self.command[0], str) or self.command[0].startswith("-"):
            raise SsrRuntimeError("SSR command must be an explicit argv list, not a shell string.")
        if self.proc is not None:
            raise SsrRuntimeError("SSR process is already running.")
        try:
            self.proc = self.popen(  # type: ignore[operator]
                self.command,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except OSError as exc:
            raise SsrRuntimeError(f"Could not start SSR process: {exc}") from exc
        if self.proc.stdout is not None:
            self._thread = threading.Thread(target=self._read, name="limbo-ssr-log", daemon=True)
            self._thread.start()
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise SsrRuntimeError(
                    f"SSR process exited with status {self.proc.returncode} before it was ready. "
                    f"Log: {self.log_text()[:500]}"
                )
            try:
                with urllib.request.urlopen(self.ready_url, timeout=0.3) as response:
                    if response.status < 500:
                        return
            except (OSError, urllib.error.URLError):
                time.sleep(0.05)
        self.stop()
        raise SsrRuntimeError(f"SSR process did not become ready at {self.ready_url}.")

    def _read(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            self._chunks.append(redact_secrets(line))
            blob = "".join(self._chunks)
            if len(blob) > self.log_limit:
                self._chunks = [blob[-self.log_limit:]]

    def log_text(self) -> str:
        return "".join(self._chunks)

    def forward(self, method: str, path: str, body: bytes = b"", headers: dict[str, str] | None = None) -> tuple[int, bytes]:
        if self.proc is None or self.proc.poll() is not None:
            raise SsrRuntimeError("SSR process is not running.")
        if not path.startswith("/"):
            raise SsrRuntimeError("Forwarded path must start with '/'.")
        split = urllib.parse.urlsplit(self.ready_url)
        origin = f"{split.scheme}://{split.netloc}"
        request = urllib.request.Request(origin + path, data=body or None, method=method.upper(), headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except OSError as exc:
            raise SsrRuntimeError(f"SSR forward failed: {exc}") from exc

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
        if self._thread is not None:
            self._thread.join(timeout=1)
