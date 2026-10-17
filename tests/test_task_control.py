import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from photo_backup import HIDE_CONSOLE_WINDOW, TaskCancelled, TaskControl, _video_resolution


class TaskControlTests(unittest.TestCase):
    def test_stop_raises_at_next_checkpoint(self):
        control = TaskControl()
        control.checkpoint()
        control.stop()
        with self.assertRaises(TaskCancelled):
            control.checkpoint()
        control.reset()
        control.checkpoint()

    def test_pause_blocks_until_resumed(self):
        control = TaskControl()
        control.pause()
        passed = threading.Event()
        worker = threading.Thread(target=lambda: (control.checkpoint(), passed.set()), daemon=True)
        worker.start()
        self.assertFalse(passed.wait(0.3))
        control.resume()
        self.assertTrue(passed.wait(2))

    def test_stop_releases_a_paused_worker(self):
        control = TaskControl()
        control.pause()
        outcome = []

        def work():
            try:
                control.checkpoint()
            except TaskCancelled:
                outcome.append("cancelled")

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        control.stop()
        worker.join(2)
        self.assertEqual(outcome, ["cancelled"])

    @unittest.skipUnless(HIDE_CONSOLE_WINDOW, "console hiding applies to Windows only")
    def test_ffprobe_runs_without_a_console_window(self):
        completed = subprocess.CompletedProcess([], 0, stdout=b'{"streams":[{"width":4,"height":3}]}', stderr=b"")
        with patch("photo_backup.find_ffprobe_executable", return_value=Path("ffprobe.exe")), patch(
            "photo_backup.subprocess.run", return_value=completed
        ) as run:
            self.assertEqual(_video_resolution(Path("clip.mov")), (4, 3))
        self.assertEqual(run.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)


if __name__ == "__main__":
    unittest.main()
