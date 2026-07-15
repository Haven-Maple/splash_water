from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import inspector  # noqa: F401
from app.utils import logging_utils


def tearDownModule() -> None:
    logging_utils.setup_logging("default", force=True)


class LoggingUtilsTests(unittest.TestCase):
    def test_log_role_maps_to_expected_filename(self) -> None:
        self.assertEqual(logging_utils.get_log_filename("backend"), "api-backend.log")
        self.assertEqual(logging_utils.get_log_filename("inspector"), "api-inspector.log")
        self.assertEqual(logging_utils.get_log_filename("unknown-role"), "api-default.log")
        self.assertEqual(logging_utils.get_log_filename("replay-worker", process_id=1234), "api-replay-worker-p1234.log")

    def test_read_recent_vendor_logs_uses_requested_file(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            log_dir = Path(temp_dir)
            backend_log = log_dir / "api-backend.log"
            replay_log = log_dir / "api-replay-worker-p222.log"
            backend_log.write_text(
                "\n".join(
                    [
                        "2026-07-10 00:00:00 INFO [pid=1 process=MainProcess role=backend] "
                        + json.dumps({"traceId": "backend-1"}),
                        "2026-07-10 00:00:01 INFO [pid=1 process=MainProcess role=backend] "
                        + json.dumps({"traceId": "backend-2"}),
                    ]
                ),
                encoding="utf-8",
            )
            replay_log.write_text(
                "2026-07-10 00:00:02 INFO [pid=2 process=ReplayWorker role=replay-worker] "
                + json.dumps({"traceId": "replay-1"}),
                encoding="utf-8",
            )

            fake_settings = SimpleNamespace(log_dir=log_dir)
            with patch.object(logging_utils, "settings", fake_settings):
                backend_entries = logging_utils.read_recent_vendor_logs(10, log_file="api-backend.log")
                replay_entries = logging_utils.read_recent_vendor_logs(10, log_file="api-replay-worker-p222.log")

            self.assertEqual([entry["traceId"] for entry in backend_entries], ["backend-2", "backend-1"])
            self.assertEqual([entry["traceId"] for entry in replay_entries], ["replay-1"])

    def test_get_available_log_filenames_lists_role_logs_and_legacy(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            log_dir = Path(temp_dir)
            (log_dir / "api-inspector.log").write_text("", encoding="utf-8")
            (log_dir / "api-replay-worker-p222.log").write_text("", encoding="utf-8")
            fake_settings = SimpleNamespace(log_dir=log_dir)
            with patch.object(logging_utils, "settings", fake_settings):
                filenames = logging_utils.get_available_log_filenames()

            self.assertIn("api-backend.log", filenames)
            self.assertIn("api-inspector.log", filenames)
            self.assertIn("api-replay-worker-p222.log", filenames)
            self.assertIn("api.log", filenames)

    def test_setup_logging_writes_to_role_specific_file(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            log_dir = Path(temp_dir)
            fake_settings = SimpleNamespace(log_dir=log_dir)
            with patch.object(logging_utils, "settings", fake_settings), patch("app.utils.logging_utils.os.getpid", return_value=4321):
                logger = logging_utils.setup_logging("replay-worker", force=True)
                logger.info("replay worker test")
                for handler in logger.handlers:
                    handler.flush()

            self.assertTrue((log_dir / "api-replay-worker-p4321.log").exists())
            logging_utils.setup_logging("default", force=True)

    def test_replay_worker_log_filenames_are_per_process(self) -> None:
        self.assertEqual(logging_utils.get_log_filename("replay-worker", process_id=101), "api-replay-worker-p101.log")
        self.assertEqual(logging_utils.get_log_filename("replay-worker", process_id=202), "api-replay-worker-p202.log")


class ReplayWorkerLoggingTests(unittest.TestCase):
    def test_replay_worker_uses_dedicated_log_role(self) -> None:
        import inspector.replay_worker as replay_worker

        with patch.object(replay_worker, "setup_logging") as mock_setup_logging:
            mock_store = Mock()
            with patch.object(replay_worker, "ReplayStore", return_value=mock_store):
                exit_code = replay_worker.main(["--handoff", "demo.json"])

        self.assertEqual(exit_code, 0)
        mock_setup_logging.assert_called_once_with("replay-worker", force=True)
        mock_store.write_from_handoff.assert_called_once()


if __name__ == "__main__":
    unittest.main()
