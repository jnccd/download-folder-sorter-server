"""Background service that watches the download folder and sorts files.

The service runs three daemon threads per *generation*:

``sorter-worker``
    Applies the configured sort delay and moves matching files.
``sorter-watcher``
    Wraps ``watchfiles.watch()`` (``ReadDirectoryChangesW`` on Windows,
    inotify on Linux, FSEvents on macOS) and queues a rescan when files change.
``sorter-supervisor``
    Detects a worker or watcher that exited and brings the service back up.

Every generation gets fresh ``threading.Event`` objects.  The previous
implementation set the stop flags in ``refresh()`` and then cleared them again
in ``start()`` before the old threads had a chance to observe them, so a
"restart" could leave the previous watcher running (or leave nothing running at
all if the worker had already died).  Generation-scoped events make a restart
deterministic.

A long-running server can also lose its folder watch without raising anything:
a NAS share is remounted, inotify drops the watch, or the worker thread dies on
a transient ``OSError`` while moving a file.  :meth:`SorterService.restart`
tears everything down and comes back with a fresh OS-level watch; that is what
the "Restart sorting service" button and ``POST /api/restart`` call.
"""

import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from watchfiles import watch

from app.config import AppConfig
from app.matcher import matches_rule

#: Files still being written by a browser/download client.
PARTIAL_SUFFIXES = (".part", ".crdownload", ".tmp", ".download", ".opdownload")
MAX_RECENT_EVENTS = 20
JOIN_TIMEOUT_SECONDS = 5.0
#: How long ``watchfiles`` waits before handing control back to the loop, so a
#: stopped/rehooked watcher is noticed quickly instead of after its default 5s.
WATCH_POLL_TIMEOUT_MS = 1000
WATCH_DEBOUNCE_MS = 800

#: ``(thread, stop_event)`` for one background component.
Component = Optional[Tuple[threading.Thread, threading.Event]]


def _isoformat(timestamp: Optional[float]) -> Optional[str]:
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")


class SorterService:
    #: How often the supervisor checks that the background threads are alive.
    supervise_interval_seconds = 2.0
    #: Never auto-restart more often than this, so a fatal error cannot spin.
    min_auto_restart_seconds = 5.0

    def __init__(self, config: AppConfig) -> None:
        self.config = config

        # Guards component handles and the watcher generation counter.
        self._lifecycle_lock = threading.RLock()
        # Serializes directory scans across generations: two threads must never
        # try to move the same file at the same time.
        self._scan_lock = threading.Lock()
        # Guards the debounce flags and the event log.
        self._state_lock = threading.Lock()

        self._worker: Component = None
        self._watcher: Component = None
        self._supervisor: Component = None
        self._last_auto_restart = 0.0

        self.status = "idle"
        self.last_message = ""
        self.last_error = ""
        self.recent_events: List[str] = []
        self.started_at: Optional[float] = None
        self.last_run_at: Optional[float] = None

        self._pending_scan = False
        self._pending_since = 0.0

        self.watcher_state = "stopped"
        self.watcher_generation = 0
        self.watcher_started_at: Optional[float] = None
        self.watcher_last_event_at: Optional[float] = None
        self.restart_count = 0
        self.last_restart_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> bool:
        """Start whichever background components are not running.

        Returns ``True`` when something was actually (re)started.
        """
        with self._lifecycle_lock:
            started = self._ensure_components_locked()
        if started and self.started_at is None:
            self.started_at = time.time()
        return started

    def stop(self, timeout: float = JOIN_TIMEOUT_SECONDS) -> None:
        with self._lifecycle_lock:
            detached = [self._worker, self._watcher, self._supervisor]
            self._worker = None
            self._watcher = None
            self._supervisor = None
            for component in detached:
                if component:
                    component[1].set()
            self.watcher_state = "stopped"
        self._join(detached, timeout)

    def restart(self, reason: str = "manual") -> int:
        """Restart the worker, watcher and supervisor and rehook the watcher.

        Returns the new watcher generation.  This is the operation behind the
        "Restart sorting service" button: the old ``watchfiles`` handle is
        closed and a fresh one is created for the current download folder.
        """
        with self._lifecycle_lock:
            detached = [self._worker, self._watcher, self._supervisor]
            for component in detached:
                if component:
                    component[1].set()
            self._worker = None
            self._watcher = None
            self._supervisor = None

            self._clear_pending_scan()
            self._ensure_components_locked()
            self.restart_count += 1
            self.last_restart_at = time.time()
            generation = self.watcher_generation

        # Join outside the lock: an old worker may still be finishing a move.
        self._join(detached, JOIN_TIMEOUT_SECONDS)
        # Scan the backlog soon: files that piled up while nothing was
        # watching should not wait for the next filesystem event.
        self._schedule_scan()
        self.last_message = "Sorting service restarted; folder watcher rehooked"
        self._record_event(
            "Restarted sorting service ({0}); watcher rehooked as generation {1}".format(reason, generation)
        )
        return generation

    def refresh(self) -> int:
        """Backwards-compatible alias for :meth:`restart`."""
        return self.restart(reason="config change")

    def rehook(self) -> int:
        """Replace only the folder watcher, leaving the worker untouched.

        Returns the new watcher generation.
        """
        with self._lifecycle_lock:
            old_watcher = self._watcher
            if old_watcher:
                old_watcher[1].set()
            self._watcher = None
            self._ensure_components_locked()
            generation = self.watcher_generation

        self._join([old_watcher], JOIN_TIMEOUT_SECONDS)
        self.last_message = "Folder watcher rehooked (generation {0})".format(generation)
        return generation

    # ------------------------------------------------------------------
    # Worker / scanning
    # ------------------------------------------------------------------
    def run_once(self) -> None:
        """Scan the download folder immediately, in the calling thread."""
        self._clear_pending_scan()
        self._process_directory()

    def _worker_loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self._process_pending_files()
            except Exception as exc:  # keep the worker alive no matter what
                self._record_error("Sort loop error; continuing", exc)
            stop_event.wait(0.5)

    def _process_pending_files(self) -> None:
        with self._state_lock:
            if not self._pending_scan:
                return
            if self._pending_since > time.time() - self._sort_delay_seconds():
                return
            self._pending_scan = False
            self._pending_since = 0.0
        self._process_directory()

    def _process_directory(self) -> None:
        download_dir = self.download_dir()
        if download_dir is None:
            return

        with self._scan_lock:
            self.status = "sorting"
            try:
                try:
                    if not download_dir.exists():
                        return
                    files = sorted(path for path in download_dir.iterdir() if path.is_file())
                except OSError as exc:
                    self._record_error("Could not read the download folder", exc)
                    return
                for path in files:
                    self._process_file(path)
            finally:
                self.status = "idle"
                self.last_run_at = time.time()

    def _process_file(self, path: Path) -> None:
        try:
            if path.name.endswith(PARTIAL_SUFFIXES) or self._is_blacklisted(path):
                return
            if not path.exists():
                return
            self._sort_file(path)
        except Exception as exc:
            # One locked or vanished file must not abort the whole scan (and
            # must never kill the worker thread).
            self._record_error("Could not sort {0}".format(path.name), exc)

    def _sort_file(self, path: Path) -> None:
        if not path.exists():
            return
        name = path.name
        for rule in self.config.rules:
            if not matches_rule(name, rule):
                continue
            target_dir = Path(rule.target).expanduser()
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = self._unique_target(target_dir, name)
            shutil.move(str(path), str(target_path))
            self._record_event("Moved {0} -> {1}".format(name, target_path))
            self.last_message = "Moved {0}".format(name)
            return
        self.last_message = "No matching rule for {0}".format(name)

    @staticmethod
    def _unique_target(target_dir: Path, name: str) -> Path:
        target_path = target_dir / name
        if not target_path.exists():
            return target_path
        stem = target_path.stem
        suffix = target_path.suffix
        counter = 1
        while True:
            candidate = target_dir / "{0} ({1}){2}".format(stem, counter, suffix)
            if not candidate.exists():
                return candidate
            counter += 1

    def _is_blacklisted(self, path: Path) -> bool:
        if not self.config.blacklisted_files:
            return False
        name = path.name.lower()
        for pattern in self.config.blacklisted_files:
            if not pattern:
                continue
            normalized = pattern.strip().lower()
            if not normalized:
                continue
            if normalized.startswith("*") and normalized.endswith("*"):
                if normalized[1:-1] in name:
                    return True
            elif normalized.startswith("*"):
                if name.endswith(normalized[1:]):
                    return True
            elif normalized.endswith("*"):
                if name.startswith(normalized[:-1]):
                    return True
            elif normalized == name:
                return True
        return False

    def _sort_delay_seconds(self) -> float:
        try:
            return max(float(self.config.sort_delay_seconds), 0.0)
        except (TypeError, ValueError):
            return 0.0

    # ------------------------------------------------------------------
    # Watcher
    # ------------------------------------------------------------------
    def _watch_loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            download_dir = self.download_dir()
            if download_dir is None or not self._path_exists(download_dir):
                self._set_watcher_state(stop_event, "waiting-for-folder")
                if stop_event.wait(1.0):
                    break
                continue

            if self._set_watcher_state(stop_event, "watching"):
                self.watcher_started_at = time.time()
            # Close the gap between the previous watch and this one: anything
            # that landed while no watcher was running has no event waiting.
            self._schedule_scan()
            try:
                for changes in watch(
                    str(download_dir),
                    stop_event=stop_event,
                    force_polling=False,
                    rust_timeout=WATCH_POLL_TIMEOUT_MS,
                    yield_on_timeout=True,
                    debounce=WATCH_DEBOUNCE_MS,
                    ignore_permission_denied=True,
                ):
                    if stop_event.is_set():
                        break
                    # A NAS share that got remounted leaves the watch pointing
                    # at a path that no longer exists; rehook instead of
                    # silently waiting for events that can never arrive.
                    if not self._path_exists(download_dir):
                        self._set_watcher_state(stop_event, "rehooking")
                        break
                    if changes:
                        self.watcher_last_event_at = time.time()
                        for _, changed_path in changes:
                            self._handle_change(changed_path)
            except Exception as exc:
                self._set_watcher_state(stop_event, "retrying")
                self._record_error("Watcher error; rehooking", exc)
                if stop_event.wait(1.0):
                    break
            else:
                if not stop_event.is_set():
                    # The watch handle ended without being asked to; rehook.
                    self._set_watcher_state(stop_event, "rehooking")
                    if stop_event.wait(1.0):
                        break
        self._set_watcher_state(stop_event, "stopped")

    def _set_watcher_state(self, stop_event: threading.Event, state: str) -> bool:
        """Update the watcher state, but only for the current generation.

        A retired watcher thread can finish *after* its replacement already
        reported ``watching``; without this guard it would overwrite the new
        state (typically with ``stopped``), so the UI would claim the watcher
        was down while it was actually hooked.
        """
        component = self._watcher
        if component is not None and component[1] is stop_event:
            self.watcher_state = state
            return True
        return False

    def _handle_change(self, changed_path: str) -> None:
        try:
            path = Path(changed_path)
            if path.name.endswith(PARTIAL_SUFFIXES):
                return
            if not path.is_file():
                return
        except OSError:
            return
        self._queue_pending_file(path)

    def _queue_pending_file(self, path: Path) -> None:
        if not self._path_exists(path):
            return
        with self._state_lock:
            self._pending_scan = True
            self._pending_since = time.time()

    def _clear_pending_scan(self) -> None:
        with self._state_lock:
            self._pending_scan = False
            self._pending_since = 0.0

    def _schedule_scan(self) -> None:
        with self._state_lock:
            self._pending_scan = True
            self._pending_since = 0.0

    # ------------------------------------------------------------------
    # Supervisor
    # ------------------------------------------------------------------
    def _supervisor_loop(self, stop_event: threading.Event) -> None:
        while not stop_event.wait(self.supervise_interval_seconds):
            try:
                with self._lifecycle_lock:
                    healthy = all(
                        self._is_alive(component)
                        for component in (self._worker, self._watcher, self._supervisor)
                    )
                if healthy:
                    continue
                if time.time() - self._last_auto_restart < self.min_auto_restart_seconds:
                    continue
                self._last_auto_restart = time.time()
                self._record_event("A background thread stopped; restarting the sorting service")
                self.restart(reason="watchdog")
            except Exception as exc:
                self._record_error("Supervisor error", exc)

    # ------------------------------------------------------------------
    # Helpers / introspection
    # ------------------------------------------------------------------
    def download_dir(self) -> Optional[Path]:
        folder = (self.config.download_folder or "").strip()
        if not folder:
            return None
        try:
            return Path(folder).expanduser()
        except (OSError, RuntimeError):
            return None

    @staticmethod
    def _path_exists(path: Path) -> bool:
        try:
            return path.exists()
        except OSError:
            return False

    def _ensure_components_locked(self) -> bool:
        """Spawn any component that is missing or dead. Caller holds the lock."""
        started = False
        if not self._is_alive(self._worker):
            started = True
            self._retire(self._worker)
            self._worker = self._spawn("sorter-worker", self._worker_loop)
            self._worker[0].start()
        if not self._is_alive(self._watcher):
            started = True
            self._retire(self._watcher)
            self.watcher_generation += 1
            self.watcher_started_at = time.time()
            self.watcher_state = "starting"
            self._watcher = self._spawn("sorter-watcher", self._watch_loop)
            # Published before the thread runs: _set_watcher_state compares the
            # stop event against the current component.
            self._watcher[0].start()
        if not self._is_alive(self._supervisor):
            started = True
            self._retire(self._supervisor)
            self._supervisor = self._spawn("sorter-supervisor", self._supervisor_loop)
            self._supervisor[0].start()
        return started

    @staticmethod
    def _retire(component: Component) -> None:
        if component:
            component[1].set()

    @staticmethod
    def _spawn(name: str, target) -> Tuple[threading.Thread, threading.Event]:
        """Build (but do not start) one background component."""
        stop_event = threading.Event()
        thread = threading.Thread(target=target, args=(stop_event,), name=name, daemon=True)
        return thread, stop_event

    @staticmethod
    def _is_alive(component: Component) -> bool:
        return bool(component and component[0].is_alive())

    @staticmethod
    def _join(components, timeout: float) -> None:
        current = threading.current_thread()
        for component in components:
            if not component or component[0] is current:
                continue
            component[0].join(timeout)

    def threads_alive(self) -> Dict[str, bool]:
        with self._lifecycle_lock:
            return {
                "worker": self._is_alive(self._worker),
                "watcher": self._is_alive(self._watcher),
                "supervisor": self._is_alive(self._supervisor),
            }

    def health(self) -> Dict[str, Any]:
        return {
            "watcher": {
                "state": self.watcher_state,
                "generation": self.watcher_generation,
                "started_at": _isoformat(self.watcher_started_at),
                "last_event_at": _isoformat(self.watcher_last_event_at),
                "last_run_at": _isoformat(self.last_run_at),
                "restart_count": self.restart_count,
                "last_restart_at": _isoformat(self.last_restart_at),
                "threads": self.threads_alive(),
            },
            "last_error": self.last_error,
        }

    # ------------------------------------------------------------------
    # Event log
    # ------------------------------------------------------------------
    def _record_event(self, message: str) -> None:
        with self._state_lock:
            self.recent_events.append(message)
            if len(self.recent_events) > MAX_RECENT_EVENTS:
                del self.recent_events[:-MAX_RECENT_EVENTS]

    def _record_error(self, message: str, exc: Optional[BaseException] = None) -> None:
        if exc is None:
            detail = message
        else:
            text = str(exc).strip() or exc.__class__.__name__
            detail = "{0}: {1}".format(message, text)
        self.last_error = detail
        self.last_message = detail
        self._record_event(detail)
