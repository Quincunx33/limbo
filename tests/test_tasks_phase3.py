import threading
import time
import unittest

from limbo import BackgroundTasks


class BackgroundTaskTests(unittest.TestCase):
    def test_submit_result_and_exception_reporting(self):
        errors = []
        tasks = BackgroundTasks(max_workers=2, on_error=errors.append)
        try:
            self.assertEqual(tasks.submit(lambda a, b: a + b, 2, 3).result(timeout=2), 5)
            failed = tasks.submit(lambda: (_ for _ in ()).throw(ValueError("task failed")))
            with self.assertRaises(ValueError):
                failed.result(timeout=2)
            deadline = time.monotonic() + 1
            while not errors and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(str(errors[0]), "task failed")
        finally:
            tasks.shutdown()

    def test_capacity_is_bounded_and_cancel_releases_slot(self):
        started = threading.Event()
        release = threading.Event()
        tasks = BackgroundTasks(max_workers=1, max_pending=1)
        try:
            running = tasks.submit(lambda: (started.set(), release.wait(2)))
            self.assertTrue(started.wait(1))
            queued = tasks.submit(lambda: "queued")
            with self.assertRaisesRegex(RuntimeError, "capacity is full"):
                tasks.submit(lambda: "overflow")
            self.assertTrue(tasks.cancel(queued))
            replacement = tasks.submit(lambda: "replacement")
            release.set()
            running.result(timeout=2)
            self.assertEqual(replacement.result(timeout=2), "replacement")
        finally:
            release.set()
            tasks.shutdown()

    def test_shutdown_rejects_new_work_and_cancels_pending(self):
        started = threading.Event()
        release = threading.Event()
        tasks = BackgroundTasks(max_workers=1, max_pending=2)
        running = tasks.submit(lambda: (started.set(), release.wait(2)))
        self.assertTrue(started.wait(1))
        queued = tasks.submit(lambda: "never")
        tasks.shutdown(wait=False, cancel_pending=True)
        self.assertTrue(queued.cancelled())
        with self.assertRaisesRegex(RuntimeError, "shut down"):
            tasks.submit(lambda: None)
        release.set()
        running.result(timeout=2)
        tasks.shutdown(wait=True)

    def test_invalid_configuration(self):
        with self.assertRaises(ValueError):
            BackgroundTasks(max_workers=0)
        with self.assertRaises(ValueError):
            BackgroundTasks(max_pending=-1)


if __name__ == "__main__":
    unittest.main()
