import json
from pathlib import Path
from typing import Any, Dict, List, Optional

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


class MatchRule:
    def __init__(self, name: str = "", match: str = "", target: str = "") -> None:
        self.name = name
        self.match = match
        self.target = target

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name, "match": self.match, "target": self.target}

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "MatchRule":
        return cls(name=payload.get("name", ""), match=payload.get("match", ""), target=payload.get("target", ""))


class AppConfig:
    def __init__(self) -> None:
        self.download_folder: str = ""
        self.sort_delay_seconds: int = 3
        self.rules: List[MatchRule] = []
        self.last_error: str = ""
        self.last_run: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "download_folder": self.download_folder,
            "sort_delay_seconds": self.sort_delay_seconds,
            "rules": [rule.to_dict() for rule in self.rules],
            "last_error": self.last_error,
            "last_run": self.last_run,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AppConfig":
        config = cls()
        config.download_folder = payload.get("download_folder", "")
        config.sort_delay_seconds = int(payload.get("sort_delay_seconds", 3))
        config.rules = [MatchRule.from_dict(item) for item in payload.get("rules", [])]
        config.last_error = payload.get("last_error", "")
        config.last_run = payload.get("last_run", "")
        return config


def load_config(path: Optional[Path] = None) -> AppConfig:
    config_path = path or CONFIG_PATH
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as handle:
            return AppConfig.from_dict(json.load(handle))
    return AppConfig()


def save_config(config: AppConfig, path: Optional[Path] = None) -> None:
    config_path = path or CONFIG_PATH
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(config.to_dict(), handle, indent=2)
