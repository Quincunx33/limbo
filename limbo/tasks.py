"""Bounded local background task execution."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable


class BackgroundTasks:
    """A bounded, process-local executor with explicit cancellation and shutdown.

    Capacity includes running plus queued tasks. This is a lightweight local
    task primitive, not a durable queue or distributed worker system.
    """
    def __init__(self, *, max_workers: int = 4, max_pending: int = 32,
                 on_error: Callable[[BaseException], None] | None = None,
                 thread_name_prefix: str = "limbo-task") -> None:
        if not isinstance(max_workers, int) or max_workers < 1:
            raise ValueError("max_workers must be positive")
        if not isinstance(max_pending, int) or max_pending < 0:
            raise ValueError("max_pending cannot be negative")
        self.max_workers = max_workers
        self.max_pending = max_pending
        self._capacity = threading.BoundedSemaphore(max_workers + max_pending)
        self._executor = ThreadPoolExecutor(max_workers=max_workers,
                                            thread_name_prefix=thread_name_prefix)
        self._on_error = on_error
        self._logger = logging.getLogger("limbo.tasks")
        self._futures: set[Future] = set()
        self._lock = threading.RLock()
        self._closed = False

    @property
    def outstanding(self) -> int:
        with self._lock:
            return len(self._futures)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def submit(self, function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
        if not callable(function):
            raise TypeError("Background task must be callable")
        with self._lock:
            if self._closed:
                raise RuntimeError("Background task executor is shut down")
            if not self._capacity.acquire(blocking=False):
                raise RuntimeError("Background task capacity is full")
            try:
                future = self._executor.submit(function, *args, **kwargs)
            except BaseException:
                self._capacity.release()
                raise
            self._futures.add(future)
            future.add_done_callback(self._completed)
            return future

    def cancel(self, future: Future) -> bool:
        """Cancel a task if it has not started running."""
        return future.cancel()

    def _completed(self, future: Future) -> None:
        with self._lock:
            self._futures.discard(future)
        self._capacity.release()
        if future.cancelled():
            return
        error = future.exception()
        if error is not None:
            if self._on_error is not None:
                try:
                    self._on_error(error)
                except Exception:
                    self._logger.exception("Background task error handler failed")
            else:
                self._logger.error("Background task failed", exc_info=(
                    type(error), error, error.__traceback__
                ))

    def shutdown(self, *, wait: bool = True, cancel_pending: bool = True) -> None:
        """Reject new tasks, optionally cancel queued work, and join workers.

        Calling shutdown again with wait=True is supported after an earlier
        non-blocking shutdown so applications can later join remaining workers.
        """
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=cancel_pending)

    def __enter__(self) -> "BackgroundTasks":
        if self.closed:
            raise RuntimeError("Background task executor is shut down")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.shutdown(wait=True, cancel_pending=True)
