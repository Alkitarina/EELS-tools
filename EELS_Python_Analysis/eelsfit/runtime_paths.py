"""Dedicated locations for disposable work, separate from data and UI drafts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_runs_dir() -> Path:
    folder = ROOT / "test_runs"
    folder.mkdir(exist_ok=True)
    return folder


def runtime_work_dir() -> Path:
    folder = ROOT / "web_runs"
    folder.mkdir(exist_ok=True)
    return folder
