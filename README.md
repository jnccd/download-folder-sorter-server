# Download Folder Sorter

A small NAS-friendly Python server that watches a download folder and moves files into target folders based on configurable matching rules. It includes a simple web UI for setup, status, and manual sorting.

## Features

- Watches a configured download folder for new or changed files
- Applies rules in order, so the first matching rule wins
- Supports simple matching syntax with substring matches, `&` for AND, and `|` for OR
- Persists configuration in `config.json`
- Includes a browser-based interface for configuration and status
- Supports a configurable sort delay and startup scan
- Allows blacklisting files or patterns that should never be moved

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

3. Start the server
   ```bash
   python main.py
   ```

4. Open the web UI
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

## Manual Sort

You can trigger a manual scan from the UI or by calling the API endpoint:

```bash
curl -X POST http://localhost:8000/api/sort
```

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
