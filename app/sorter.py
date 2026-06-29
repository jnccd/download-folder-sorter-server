import shutil
import threading
import time
from pathlib import Path
from typing import List, Optional

from watchfiles import watch

from app.config import AppConfig
from app.matcher import matches_rule


class SorterService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._stop_event = threading.Event()
        self._watch_stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._watch_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.status = "idle"
        self.last_message = ""
        self.recent_events: List[str] = []
        self._pending_scan = False
        self._pending_since = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._watch_stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._watch_thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()
        self._watch_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._watch_stop_event.set()

    def refresh(self) -> None:
        self._pending_scan = False
        self._pending_since = 0.0
        self.stop()
        self.start()

    def run_once(self) -> None:
        self._process_directory()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._process_pending_files()
            time.sleep(0.5)

    def _watch_loop(self) -> None:
        while not self._watch_stop_event.is_set():
            download_dir = Path(self.config.download_folder).expanduser() if self.config.download_folder else None
            if not download_dir or not download_dir.exists():
                time.sleep(1)
                continue
            try:
                for changes in watch(str(download_dir), stop_event=self._watch_stop_event, force_polling=False):
                    for _, changed_path in changes:
                        path = Path(changed_path)
                        if path.is_file() and not path.name.endswith((".part", ".crdownload", ".tmp", ".download", ".opdownload")):
                            self._queue_pending_file(path)
            except Exception as exc:  # pragma: no cover - best effort watcher loop
                self.last_message = f"Watcher error: {exc}"
                time.sleep(1)

    def _queue_pending_file(self, path: Path) -> None:
        if not path.exists():
            return
        with self._lock:
            self._pending_scan = True
            self._pending_since = time.time()

    def _process_pending_files(self) -> None:
        if not self._pending_scan:
            return
        cutoff = time.time() - max(self.config.sort_delay_seconds, 0)
        if self._pending_since > cutoff:
            return
        with self._lock:
            self._pending_scan = False
            self._pending_since = 0.0
        self._process_directory()

    def _process_directory(self) -> None:
        if not self.config.download_folder:
            return
        download_dir = Path(self.config.download_folder).expanduser()
        if not download_dir.exists():
            return
        self.status = "sorting"
        try:
            files = sorted(p for p in download_dir.iterdir() if p.is_file())
            for path in files:
                if path.name.endswith((".part", ".crdownload", ".tmp", ".download", ".opdownload")):
                    continue
                with self._lock:
                    self._sort_file(path)
        finally:
            self.status = "idle"

    def _sort_file(self, path: Path) -> None:
        if not path.exists():
            return
        name = path.name
        for rule in self.config.rules:
            if matches_rule(name, rule):
                target_dir = Path(rule.target).expanduser()
                target_dir.mkdir(parents=True, exist_ok=True)
                target_path = target_dir / name
                if target_path.exists():
                    stem = target_path.stem
                    suffix = target_path.suffix
                    counter = 1
                    while True:
                        candidate = target_dir / f"{stem} ({counter}){suffix}"
                        if not candidate.exists():
                            target_path = candidate
                            break
                        counter += 1
                shutil.move(str(path), str(target_path))
                self.recent_events.append(f"Moved {name} -> {target_path}")
                if len(self.recent_events) > 20:
                    self.recent_events = self.recent_events[-20:]
                self.last_message = f"Moved {name}"
                return
        self.last_message = f"No matching rule for {name}"
