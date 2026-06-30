import base64
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import AppConfig, MatchRule, load_config, save_config
from app.sorter import SorterService

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

app_config = load_config(CONFIG_PATH)
sorter = SorterService(app_config)


@asynccontextmanager
async def lifespan(_: FastAPI):
    sorter.start()
    sorter.run_once()
    yield
    sorter.stop()


app = FastAPI(title="Download Folder Sorter", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def _get_expected_auth() -> Tuple[str, str]:
    username = os.getenv("DOWNLOAD_FOLDER_SORTER_USER", "").strip()
    password = os.getenv("DOWNLOAD_FOLDER_SORTER_PASS", "").strip()
    return username, password


def _is_auth_enabled() -> bool:
    username, password = _get_expected_auth()
    return bool(username and password)


def _parse_basic_auth(header_value: str) -> Optional[Tuple[str, str]]:
    if not header_value or not header_value.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header_value.split(" ", 1)[1]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if ":" not in decoded:
        return None
    username, password = decoded.split(":", 1)
    return username, password


@app.middleware("http")
async def require_authentication(request: Request, call_next):
    if not _is_auth_enabled() or request.method == "OPTIONS":
        return await call_next(request)

    credentials = _parse_basic_auth(request.headers.get("authorization", ""))
    expected_user, expected_password = _get_expected_auth()
    if credentials and credentials[0] == expected_user and credentials[1] == expected_password:
        return await call_next(request)

    response = JSONResponse({"detail": "Unauthorized"}, status_code=401)
    response.headers["WWW-Authenticate"] = 'Basic realm="Download Folder Sorter"'
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "config": app_config.to_dict(),
        },
    )


@app.get("/api/config")
async def get_config() -> JSONResponse:
    return JSONResponse(app_config.to_dict())


@app.post("/api/config")
async def update_config(payload: dict) -> JSONResponse:
    global app_config
    app_config.download_folder = payload.get("download_folder", app_config.download_folder)
    app_config.sort_delay_seconds = int(payload.get("sort_delay_seconds", app_config.sort_delay_seconds))
    app_config.rules = [MatchRule(**rule) for rule in payload.get("rules", [])]
    app_config.blacklisted_files = [str(item) for item in payload.get("blacklisted_files", app_config.blacklisted_files)]
    save_config(app_config, CONFIG_PATH)
    sorter.refresh()
    return JSONResponse(app_config.to_dict())


@app.post("/api/sort")
async def sort_now() -> JSONResponse:
    sorter.refresh()
    sorter.run_once()
    return JSONResponse({"status": sorter.status, "message": sorter.last_message})


@app.get("/api/status")
async def get_status() -> JSONResponse:
    return JSONResponse(
        {
            "status": sorter.status,
            "message": sorter.last_message,
            "recent_events": sorter.recent_events,
            "download_folder": app_config.download_folder,
            "rules": len(app_config.rules),
        }
    )


if __name__ == "__main__":
    import uvicorn

    reload_enabled = os.getenv("UVICORN_RELOAD", "0").lower() in {"1", "true", "yes", "on"}
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=reload_enabled)
