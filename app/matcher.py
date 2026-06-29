from pathlib import Path
from typing import List, Optional

from app.config import MatchRule


class MatchResult:
    def __init__(self, rule: MatchRule, file_name: str, target_path: str) -> None:
        self.rule = rule
        self.file_name = file_name
        self.target_path = target_path


def _split_or_terms(pattern: str) -> List[str]:
    return [part.strip() for part in pattern.split("|") if part.strip()]


def _split_and_terms(pattern: str) -> List[str]:
    return [part.strip() for part in pattern.split("&") if part.strip()]


def matches_rule(file_name: str, rule: MatchRule) -> bool:
    if not rule.match:
        return False
    for branch in _split_or_terms(rule.match):
        if all(term.lower() in file_name.lower() for term in _split_and_terms(branch)):
            return True
    return False


def resolve_target_path(download_folder: str, rule: MatchRule, file_name: str) -> str:
    target_dir = Path(rule.target).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / file_name
    if target_path.exists():
        stem = target_path.stem
        suffix = target_path.suffix
        counter = 1
        while True:
            candidate = target_dir / f"{stem} ({counter}){suffix}"
            if not candidate.exists():
                return str(candidate)
            counter += 1
    return str(target_path)


def find_matching_rule(file_name: str, rules: List[MatchRule]) -> Optional[MatchResult]:
    for rule in rules:
        if matches_rule(file_name, rule):
            return MatchResult(rule=rule, file_name=file_name, target_path=resolve_target_path("", rule, file_name))
    return None
