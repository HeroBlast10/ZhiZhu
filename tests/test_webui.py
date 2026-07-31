import queue
import sys
import threading
import unittest

from webui import _QueueWriter, _run_in_thread


class WebUiConcurrencyTests(unittest.TestCase):
    def test_concurrent_tasks_restore_stdout(self):
        original = sys.stdout
        first_entered = threading.Event()
        release_first = threading.Event()

        def first():
            first_entered.set()
            release_first.wait(timeout=5)

        def second():
            return None

        first_thread = threading.Thread(
            target=_run_in_thread,
            args=(first, queue.Queue()),
        )
        second_thread = threading.Thread(
            target=_run_in_thread,
            args=(second, queue.Queue()),
        )
        try:
            first_thread.start()
            self.assertTrue(first_entered.wait(timeout=5))
            second_thread.start()
            release_first.set()
            first_thread.join(timeout=5)
            second_thread.join(timeout=5)
            self.assertFalse(first_thread.is_alive())
            self.assertFalse(second_thread.is_alive())
            self.assertIs(sys.stdout, original)
            self.assertNotIsInstance(sys.stdout, _QueueWriter)
        finally:
            release_first.set()
            first_thread.join(timeout=5)
            second_thread.join(timeout=5)
            sys.stdout = original


if __name__ == "__main__":
    unittest.main()
