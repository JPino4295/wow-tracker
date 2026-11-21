"""The pinned lint and typecheck commands must run cleanly from the repo root."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_documents_lint_and_typecheck() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "ruff check ." in readme
    assert "mypy app" in readme


def test_mypy_app_exits_cleanly() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "app"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_ruff_command_is_available() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "--version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_ruff_config_and_main_are_clean() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "app/main.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_ruff_other_app_modules_are_clean() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "app/config.py", "app/resets.py", "app/schemas.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_ruff_tests_are_clean() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "tests/test_api.py", "tests/test_resets.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_ruff_check_exits_cleanly() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "."],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
