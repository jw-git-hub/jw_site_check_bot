#!/usr/bin/env python3
"""Не пускает секреты в git.

Проверяет пути (.env, база, локальные настройки выкладки, конфликтные копии Syncthing)
и содержимое: токен Telegram, ключ Google, приватные ключи, имена устройств Tailscale
и любое значение из локальных файлов .env и deploy/deploy.local.env (адрес, пользователь, путь сервера).
Сами найденные значения никогда не печатает — только файл и на что похоже.

  check_secrets.py --staged      файлы, добавленные в коммит (хук pre-commit)
  check_secrets.py --rev <sha>   файлы одного коммита (перед выкладкой)
  check_secrets.py --all         всё, что когда-либо попадало в коммиты (перед отправкой в GitHub)
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # папка синхронизируется Syncthing: никакого __pycache__

LOCAL_VALUE_FILES = (".env", "deploy/deploy.local.env")  # их значения в git попадать не должны
MIN_LOCAL_VALUE_LENGTH = 8
SHORT_SHA_LENGTH = 12
STAGED_REVISION = ""
ALLOWED_PATHS = {".env.example", "deploy/deploy.local.env.example"}

# Шаблоны собраны из частей, чтобы этот файл сам проходил проверку.
SECRET_PATTERNS = {
    # Не \b: в адресе запроса токен идёт сразу после «bot» — /bot123…:AA…/getMe.
    "похоже на токен бота Telegram": re.compile(r"(?<![0-9])\d{8,12}:" + r"[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"),
    "похоже на ключ Google API": re.compile("AI" + "za" + r"[0-9A-Za-z_-]{35}"),
    "приватный ключ": re.compile("-----BEGIN " + r"[A-Z ]*" + "PRIVATE KEY-----"),
    "имя устройства в Tailscale": re.compile(r"\b[a-z0-9-]+\.[a-z0-9-]+\." + r"ts\.net\b", re.IGNORECASE),
}

FORBIDDEN_PATHS = {
    "файл окружения": re.compile(r"(^|/)\.env(\.[^/]+)?$"),
    "локальные настройки выкладки": re.compile(r"(^|/)[^/]+\.local\.env$"),
    "папка с базой": re.compile(r"^data/"),
    "файл базы": re.compile(r"\.(db|sqlite3?)(-wal|-shm|-journal)?$"),
    "конфликтная копия Syncthing": re.compile(r"sync-conflict"),
}


def run_git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def split_paths(raw: bytes) -> list[str]:
    return [path for path in raw.decode().split("\0") if path]


def staged_paths() -> list[str]:
    return split_paths(run_git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"))


def commit_paths(commit: str) -> list[str]:
    return split_paths(run_git("ls-tree", "-r", "--name-only", "-z", commit))


def changed_paths(commit: str) -> list[str]:
    """Файлы, которые коммит добавил или изменил; -m — чтобы не пропустить слияния."""
    raw = run_git("diff-tree", "--no-commit-id", "--name-only", "-r", "-m", "-z", "--root",
                  "--diff-filter=ACMR", commit)
    return sorted(set(split_paths(raw)))


def all_commits() -> list[str]:
    return run_git("rev-list", "--all").decode().split()


def read_file(revision: str, path: str) -> str:
    return run_git("show", f"{revision}:{path}").decode(errors="ignore")


def parse_value(line: str) -> str:
    _, _, value = line.partition("=")
    return value.strip().strip("'\"")


def read_values(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    values = {parse_value(line) for line in lines if "=" in line and not line.lstrip().startswith("#")}
    return {value for value in values if len(value) >= MIN_LOCAL_VALUE_LENGTH}


def load_local_values(repo_root: Path) -> set[str]:
    values: set[str] = set()
    for name in LOCAL_VALUE_FILES:
        values |= read_values(repo_root / name)
    return values


def find_path_problems(path: str) -> list[str]:
    if path in ALLOWED_PATHS:
        return []
    return [f"{path}: {kind}" for kind, pattern in FORBIDDEN_PATHS.items() if pattern.search(path)]


def find_content_problems(path: str, text: str, local_values: set[str]) -> list[str]:
    problems = [f"{path}: {kind}" for kind, pattern in SECRET_PATTERNS.items() if pattern.search(text)]
    if any(value in text for value in local_values):
        problems.append(f"{path}: содержит значение из .env или deploy.local.env")
    return problems


def check_files(revision: str, paths: list[str], local_values: set[str]) -> list[str]:
    problems = []
    for path in paths:
        problems += find_path_problems(path)
        problems += find_content_problems(path, read_file(revision, path), local_values)
    return problems


def check_history(local_values: set[str]) -> list[str]:
    problems = []
    for commit in all_commits():
        found = check_files(commit, changed_paths(commit), local_values)
        problems += [f"{commit[:SHORT_SHA_LENGTH]} {problem}" for problem in found]
    return problems


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true", help="проверить файлы, добавленные в коммит")
    group.add_argument("--rev", help="проверить файлы указанного коммита")
    group.add_argument("--all", action="store_true", help="проверить всю историю")
    return parser.parse_args()


def collect_problems(args: argparse.Namespace, local_values: set[str]) -> list[str]:
    if args.all:
        return check_history(local_values)
    if args.staged:
        return check_files(STAGED_REVISION, staged_paths(), local_values)
    return check_files(args.rev, commit_paths(args.rev), local_values)


def main() -> int:
    args = parse_args()
    repo_root = Path(run_git("rev-parse", "--show-toplevel").decode().strip())
    problems = collect_problems(args, load_local_values(repo_root))
    if not problems:
        return 0
    print("Секреты не пропущены:", *problems, sep="\n  ", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
