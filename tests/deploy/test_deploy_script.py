import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COPIED = ("deploy/deploy.sh", "scripts/check_secrets.py", "bot/core/secret_patterns.py")
LOCAL_CONFIG = 'DEPLOY_HOST=test-host\nDEPLOY_USER=tester\nDEPLOY_PATH="/srv/bot folder"\n'
FAKE_SSH = """#!/usr/bin/env bash
command="${@: -1}"
printf '%s\\n' "$command" >> "$SSH_LOG"
case "$command" in
  *"git rev-parse HEAD"*) printf '%s\\n' "$FAKE_REMOTE_HEAD" ;;
esac
exit 0
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", name)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    for relative in COPIED:
        (work / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, work / relative)
    (work / ".gitignore").write_text("/deploy/*.local.env\n", encoding="utf-8")
    (work / "deploy" / "deploy.local.env").write_text(LOCAL_CONFIG, encoding="utf-8")
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.email", "t@example.invalid")
    git(work, "config", "user.name", "t")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "one")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "main")
    return work


def deploy(project: Path, *args: str, remote_head: str | None = None) -> tuple[subprocess.CompletedProcess, list[str]]:
    fake_bin, log = project.parent / "bin", project.parent / "ssh.log"
    fake_bin.mkdir(exist_ok=True)
    (fake_bin / "ssh").write_text(FAKE_SSH, encoding="utf-8")
    (fake_bin / "ssh").chmod(0o755)
    environ = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "SSH_LOG": str(log),
               "FAKE_REMOTE_HEAD": remote_head or git(project, "rev-parse", "HEAD")}
    result = subprocess.run(["bash", "deploy/deploy.sh", *args], cwd=project, env=environ, capture_output=True,
                            text=True, timeout=60)
    return result, log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def deploy_with_ssh_exit_code(project: Path, exit_code: int) -> subprocess.CompletedProcess:
    """Своя подмена ssh: всегда завершается заданным кодом, без записи лога и без имитации сервера."""
    fake_bin = project.parent / "bin"
    fake_bin.mkdir(exist_ok=True)
    (fake_bin / "ssh").write_text(f"#!/usr/bin/env bash\nexit {exit_code}\n", encoding="utf-8")
    (fake_bin / "ssh").chmod(0o755)
    environ = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
    return subprocess.run(["bash", "deploy/deploy.sh"], cwd=project, env=environ, capture_output=True,
                          text=True, timeout=60)


def test_happy_path_checks_out_exact_commit_then_restarts(project):
    result, commands = deploy(project)
    assert result.returncode == 0, result.stderr
    sha = git(project, "rev-parse", "HEAD")
    steps = ["/.env", "stat -c %u", "git status --porcelain", f"git checkout --quiet --detach {sha}",
             "docker compose build", "docker compose logs"]
    positions = [next(index for index, command in enumerate(commands) if step in command) for step in steps]
    assert positions == sorted(positions)
    assert "/srv/bot\\ folder" in commands[0]


def test_dirty_tree_stops_before_touching_server(project):
    (project / "new.txt").write_text("x", encoding="utf-8")
    result, commands = deploy(project)
    assert result.returncode == 1
    assert "есть незакоммиченные изменения" in result.stderr
    assert commands == []


def test_unpushed_commit_stops(project):
    commit(project, "two.txt")
    result, commands = deploy(project)
    assert result.returncode == 1
    assert "нет в origin/main" in result.stderr
    assert commands == []


def test_missing_local_config_stops(project):
    (project / "deploy" / "deploy.local.env").unlink()
    result, _ = deploy(project)
    assert result.returncode == 1
    assert "нет deploy/deploy.local.env" in result.stderr


def test_rollback_to_older_pushed_commit(project):
    first = git(project, "rev-parse", "HEAD")
    commit(project, "two.txt")
    git(project, "push", "-q", "origin", "main")
    result, commands = deploy(project, first, remote_head=first)
    assert result.returncode == 0, result.stderr
    assert any(f"git checkout --quiet --detach {first}" in command for command in commands)


def test_deploy_never_copies_env():
    script = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    assert "scp" not in script and "< .env" not in script and "cat .env" not in script


def test_health_wait_is_sixty_seconds_per_c17():
    """Поправка 1: решение C17 (ТЗ 13.2 п.5) — ждать healthy до 60 секунд, не 90."""
    script = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    assert "HEALTH_WAIT_SECONDS=60" in script


def test_unreachable_host_hints_at_tailscale(project):
    """Поправка 2: имя сервера в Tailscale может не находиться, если Tailscale на Маке выключен."""
    result = deploy_with_ssh_exit_code(project, 255)
    assert result.returncode == 1
    assert "Tailscale" in result.stderr


def test_remote_check_failure_keeps_specific_message_when_not_a_connection_error(project):
    """Код ssh 255 — обрыв связи; остальные коды — сам удалённый вызов, сообщение должно остаться точным."""
    result = deploy_with_ssh_exit_code(project, 1)
    assert result.returncode == 1
    assert "нет .env на сервере" in result.stderr
    assert "Tailscale" not in result.stderr
