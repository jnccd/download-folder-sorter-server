# Download Folder Sorter

> ⚠️ **Warning**: This is an AI slop project.

A small NAS-friendly Python server that watches a download folder and moves files into target folders based on configurable matching rules. It includes a simple web UI for setup, status, and manual sorting.

## Features

- Watches a configured download folder for new or changed files
- Applies rules in order, so the first matching rule wins
- Supports simple matching syntax with substring matches, `&` for AND, and `|` for OR
- Persists configuration in `config.json`
- Includes a browser-based interface for configuration and status
- Supports a configurable sort delay and startup scan
- Allows blacklisting files or patterns that should never be moved
- Includes a **Restart sorting service** button that rehooks the folder watcher
  and rescans the backlog when a long-running server stops sorting
- Revives the background threads automatically if one of them dies

## Requirements

- Python 3.8+
- Internet access to install Python packages

## Quick Start

1. Create and activate a virtual environment
   - Windows:
     ```bash
     py -3 -m venv .venv
     .\.venv\Scripts\activate
     ```
   - macOS/Linux:
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     ```

2. Install dependencies

   ```bash
   pip install -r requirements.txt
   ```

3. Set authentication environment variables

   Windows:

   ```bash
   set DOWNLOAD_FOLDER_SORTER_USER=admin
   set DOWNLOAD_FOLDER_SORTER_PASS=change-me
   ```

   macOS/Linux:

   ```bash
   export DOWNLOAD_FOLDER_SORTER_USER=admin
   export DOWNLOAD_FOLDER_SORTER_PASS=change-me
   ```

4. Start the server

   ```bash
   python main.py
   ```

5. Open the web UI
   - Visit http://localhost:8000

## How It Works

The app watches your download folder and processes files after a short delay. Files are scanned on startup and after filesystem changes. Each file is checked against your rules in order, and the first matching rule determines the destination folder.

### Matching syntax

- Simple substring match:
  - `movie`
- AND match:
  - `movie&2024`
- OR match:
  - `movie|show`

Rules are evaluated in the order you define them. If no rule matches, the file is left in place.

## Configuration

The app stores its settings in `config.json`.

You can configure:

- Download folder
- Sort delay in seconds
- Rules with Name / Match / Target
- Blacklisted files or patterns

The web UI saves these values automatically.

## Authentication

The app supports simple basic-auth protection for single-user use. Set the following environment variables before starting the server:

- `DOWNLOAD_FOLDER_SORTER_USER`
- `DOWNLOAD_FOLDER_SORTER_PASS`

When these are configured, every endpoint requires valid basic-auth credentials. Browsers will automatically prompt for the username and password when you visit the web UI.

## Manual Sort

You can trigger a manual scan from the UI or by calling the API endpoint:

```bash
curl -X POST http://localhost:8000/api/sort
```

Using the UI button also refreshes the filesystem watcher so the folder watcher is rehooked after manual sync.

## Restarting the Sorting Service

A server that runs for weeks can stop sorting even though it is still responding:
the NAS share gets remounted, the OS drops the folder watch (`inotify` on Linux,
`ReadDirectoryChangesW` on Windows, FSEvents on macOS), or the background worker
thread exits after an unexpected error. In that state the UI still loads but new
files are never picked up.

Press **Restart sorting service** in the UI (section *Background service*), or call:

```bash
curl -X POST http://localhost:8000/api/restart
```

The restart:

- stops the worker, the folder watcher and the supervisor thread,
- starts a fresh generation of them and rehooks a new OS-level folder watch,
- clears the old event debounce and scans the download folder once, so files that
  arrived while nothing was watching are sorted without waiting for the next event,
- keeps a single scan in flight at a time, so a file is never moved twice.

The response lists the new watcher generation and the state of every background thread:

```json
{
  "message": "Sorting service restarted; folder watcher rehooked",
  "watcher_generation": 4,
  "watcher": {
    "state": "watching",
    "generation": 4,
    "threads": { "worker": true, "watcher": true, "supervisor": true }
  },
  "last_error": ""
}
```

Watcher states reported by `/api/status`:

- `watching` — the folder watch is active
- `waiting-for-folder` — the download folder does not exist yet (for example the NAS share is not mounted)
- `retrying` / `rehooking` — the watch failed or ended and is being re-created
- `stopped` — the service is stopped

A supervisor thread checks every couple of seconds and restarts the service on its
own if the worker or watcher disappears, so a crash recovers without the button.
`last_error` shows the most recent failure, if any.

## Status and Logs

The UI shows current status and recent events. You can also inspect status from:

```bash
curl http://localhost:8000/api/status
```

## Running on a NAS

This project is intended to be run as a lightweight service on a NAS or similar headless environment.

Useful notes:

- The app defaults to no auto-reload to avoid confusion with watcher behavior.
- To enable reload during development, set:
  ```bash
  set UVICORN_RELOAD=1
  ```
  on Windows or:
  ```bash
  export UVICORN_RELOAD=1
  ```
  on macOS/Linux.

## Development

Run the test suite:

```bash
python -m unittest discover -s tests -v
```
