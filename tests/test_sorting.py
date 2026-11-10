import asyncio
import os
import shutil
import threading
import time
import unittest
import uuid
from pathlib import Path
from typing import Callable, List
from unittest.mock import patch

from fastapi.testclient import TestClient

import main as main_module
from app.config import AppConfig, MatchRule
from app.matcher import matches_rule
from app.sorter import SorterService


def make_scratch_dir() -> Path:
    """Create a scratch directory under the repository root.

    ``tempfile.mkdtemp`` creates its directory with mode ``0o700``, which on
    Windows means a protected DACL.  The tests write into nested folders that
    the sorter creates itself, so they need the inherited permissions a plain
    ``mkdir`` produces.
    """
    directory = Path("sorter-test-{0}".format(uuid.uuid4().hex[:12]))
    directory.mkdir()
    return directory


def wait_for(predicate: Callable[[], bool], timeout: float = 10.0, interval: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def sorter_thread_names() -> List[str]:
    return [
        thread.name
        for thread in threading.enumerate()
        if thread.is_alive() and thread.name.startswith("sorter-")
    ]


class SortingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_scratch_dir()
        self.download_dir = self.temp_dir / "downloads"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.target_dir = self.temp_dir / "target"
        self.target_dir.mkdir(parents=True, exist_ok=True)

        config = AppConfig()
        config.download_folder = str(self.download_dir)
        config.sort_delay_seconds = 0
        config.rules = [MatchRule(name="Pictures", match=".png|.jpg", target=str(self.target_dir))]
        self.service = SorterService(config)

    def tearDown(self) -> None:
        self.service.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_matching_rule_supports_or_and_and(self) -> None:
        self.assertTrue(matches_rule("sunset.png", self.service.config.rules[0]))
        self.assertTrue(matches_rule("invoice_001.jpg", self.service.config.rules[0]))
        self.assertFalse(matches_rule("notes.pdf", self.service.config.rules[0]))

    def test_sorter_moves_matching_file_to_target(self) -> None:
        sample = self.download_dir / "photo.png"
        sample.write_text("hello", encoding="utf-8")

        self.service.run_once()

        self.assertFalse(sample.exists())
        self.assertTrue((self.target_dir / "photo.png").exists())

    def test_scan_continues_when_one_file_cannot_be_moved(self) -> None:
        broken = self.download_dir / "a-broken.png"
        broken.write_text("hello", encoding="utf-8")
        fine = self.download_dir / "b-fine.png"
        fine.write_text("hello", encoding="utf-8")

        real_move = shutil.move

        def flaky_move(src, dst, *args, **kwargs):
            if Path(src).name == broken.name:
                raise OSError("simulated network share error")
            return real_move(src, dst, *args, **kwargs)

        with patch("app.sorter.shutil.move", side_effect=flaky_move):
            self.service.run_once()

        self.assertTrue(broken.exists())
        self.assertTrue((self.target_dir / "b-fine.png").exists())
        self.assertIn("Could not sort a-broken.png", self.service.last_error)

    def test_worker_thread_survives_an_unexpected_error(self) -> None:
        self.service.start()
        with patch.object(self.service, "_process_pending_files", side_effect=RuntimeError("boom")):
            self.assertTrue(wait_for(lambda: "Sort loop error" in self.service.last_error, timeout=3.0))

        self.assertTrue(self.service.threads_alive()["worker"])
        self.assertTrue(wait_for(lambda: "Sort loop error" in self.service.last_error, timeout=1.0))

        # The worker keeps running, so a later file is still sorted.
        sample = self.download_dir / "after-error.png"
        sample.write_text("hello", encoding="utf-8")
        self.assertTrue(wait_for(lambda: (self.target_dir / "after-error.png").exists()))

    def test_update_config_persists_blacklisted_files(self) -> None:
        original_config = main_module.app_config
        main_module.app_config = AppConfig()
        try:
            asyncio.run(main_module.update_config({"blacklisted_files": ["desktop.ini", "*.tmp"]}))
            self.assertEqual(main_module.app_config.blacklisted_files, ["desktop.ini", "*.tmp"])
        finally:
            main_module.app_config = original_config
            main_module.sorter.stop()

    def test_ui_requires_basic_auth(self) -> None:
        with patch.dict(os.environ, {"DOWNLOAD_FOLDER_SORTER_USER": "demo", "DOWNLOAD_FOLDER_SORTER_PASS": "secret"}, clear=False):
            with TestClient(main_module.app) as client:
                response = client.get("/")
                self.assertEqual(response.status_code, 401)
                self.assertIn("WWW-Authenticate", response.headers)

                authenticated = client.get("/", auth=("demo", "secret"))
                self.assertEqual(authenticated.status_code, 200)

    def test_restart_endpoint_rehooks_watcher(self) -> None:
        with TestClient(main_module.app) as client:
            before = client.get("/api/status").json()["watcher"]["generation"]

            response = client.post("/api/restart")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertGreater(payload["watcher"]["generation"], before)
            self.assertEqual(payload["watcher_generation"], payload["watcher"]["generation"])
            self.assertIn("rehooked", payload["message"])
            self.assertTrue(all(payload["watcher"]["threads"].values()))
            self.assertIn("last_error", payload)

            status = client.get("/api/status").json()
            self.assertEqual(status["watcher"]["generation"], payload["watcher"]["generation"])


class RestartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_scratch_dir()
        self.download_dir = self.temp_dir / "downloads"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.target_dir = self.temp_dir / "target"
        self.target_dir.mkdir(parents=True, exist_ok=True)

        config = AppConfig()
        config.download_folder = str(self.download_dir)
        config.sort_delay_seconds = 0
        config.rules = [MatchRule(name="Pictures", match=".png", target=str(self.target_dir))]
        self.service = SorterService(config)

    def tearDown(self) -> None:
        self.service.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_restart_rehooks_watcher_and_keeps_every_thread_running(self) -> None:
        self.service.start()
        first_generation = self.service.watcher_generation

        generation = self.service.restart(reason="test")

        self.assertEqual(generation, first_generation + 1)
        self.assertEqual(self.service.watcher_generation, generation)
        self.assertEqual(self.service.restart_count, 1)
        self.assertTrue(wait_for(lambda: all(self.service.threads_alive().values())))
        self.assertTrue(wait_for(lambda: self.service.watcher_state == "watching"))

    def test_repeated_restarts_do_not_leak_background_threads(self) -> None:
        self.service.start()
        baseline = len(sorter_thread_names())
        self.assertEqual(baseline, 3)

        for _ in range(3):
            self.service.restart(reason="test")

        self.assertTrue(wait_for(lambda: len(sorter_thread_names()) == baseline, timeout=5.0))

    def test_restart_picks_up_files_left_while_the_watcher_was_down(self) -> None:
        self.service.start()
        self.service.stop()

        stranded = self.download_dir / "stranded.png"
        stranded.write_text("hello", encoding="utf-8")

        self.service.restart(reason="test")

        self.assertTrue(wait_for(lambda: (self.target_dir / "stranded.png").exists()))
        self.assertFalse(stranded.exists())

    def test_watcher_sorts_a_file_created_while_watching(self) -> None:
        self.service.start()
        self.assertTrue(wait_for(lambda: self.service.watcher_state == "watching"))

        # Let the watcher settle so the create event is actually delivered.
        time.sleep(0.4)
        sample = self.download_dir / "fresh.png"
        sample.write_text("hello", encoding="utf-8")

        self.assertTrue(wait_for(lambda: (self.target_dir / "fresh.png").exists()))
        self.assertFalse(sample.exists())

    def test_rehook_replaces_only_the_watcher(self) -> None:
        self.service.start()
        worker_thread = self.service._worker[0]
        generation = self.service.watcher_generation

        rehooked = self.service.rehook()

        self.assertEqual(rehooked, generation + 1)
        self.assertIs(self.service._worker[0], worker_thread)
        self.assertTrue(worker_thread.is_alive())
        self.assertTrue(self.service.threads_alive()["watcher"])

    def test_supervisor_revives_a_dead_worker(self) -> None:
        with patch.object(SorterService, "supervise_interval_seconds", 0.2), patch.object(
            SorterService, "min_auto_restart_seconds", 0.0
        ):
            self.service.start()
            worker, worker_stop = self.service._worker
            worker_stop.set()
            worker.join(5.0)
            self.assertFalse(worker.is_alive())

            self.assertTrue(wait_for(lambda: self.service.restart_count >= 1, timeout=5.0))
            self.assertTrue(self.service.threads_alive()["worker"])
            self.assertIn("restarting the sorting service", " ".join(self.service.recent_events))


if __name__ == "__main__":
    unittest.main()
