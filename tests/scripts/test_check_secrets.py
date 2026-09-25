import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fakes import fake_telegram_token

ROOT = Path(__file__).resolve().parents[2]
COPIED = ("scripts/check_secrets.py", "bot/core/secret_patterns.py")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def check(repo: Path, *args: str) -> subprocess.CompletedProcess:
    command = [sys.executable, "-B", "scripts/check_secrets.py", *args]
    return subprocess.run(command, cwd=repo, capture_output=True, text=True)


def stage(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content, encoding="utf-8")
    git(repo, "add", "-f", name)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for relative in COPIED:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, target)
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "test")
    return tmp_path


def test_clean_staged_file_passes(repo):
    stage(repo, "notes.md", "просто текст\n")
    assert check(repo, "--staged").returncode == 0


def test_token_is_refused_and_never_printed(repo):
    stage(repo, "notes.md", f"token={fake_telegram_token()}\n")
    result = check(repo, "--staged")
    assert result.returncode == 1
    assert "notes.md: похоже на токен бота Telegram" in result.stderr
    assert fake_telegram_token() not in result.stderr


def test_env_file_is_refused_by_path(repo):
    stage(repo, ".env", "X=1\n")
    assert ".env: файл окружения" in check(repo, "--staged").stderr


def test_server_path_from_deploy_local_env_is_refused(repo):
    (repo / "deploy").mkdir()
    (repo / "deploy" / "deploy.local.env").write_text('DEPLOY_PATH="/home/someone/bot folder"\n', encoding="utf-8")
    stage(repo, "docs.md", "cd /home/someone/bot folder\n")
    assert "docs.md: содержит значение из .env или deploy.local.env" in check(repo, "--staged").stderr


def test_history_mode_finds_secret_in_old_commit(repo):
    stage(repo, "a.md", f"{fake_telegram_token()}\n")
    git(repo, "commit", "-q", "-m", "one", "--no-verify")
    stage(repo, "a.md", "чисто\n")
    git(repo, "commit", "-q", "-m", "two", "--no-verify")
    result = check(repo, "--all")
    assert result.returncode == 1
    assert "a.md: похоже на токен бота Telegram" in result.stderr
