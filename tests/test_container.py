"""Контейнер (ТЗ, 13.3): образ, compose и .dockerignore проверяются статически — Docker на Маке нет,
сборка образа — на сервере (задача 23)."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
BOT = COMPOSE["services"]["bot"]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")

# Поправка 1 к задаче 20: до этой версии ipaddress.is_global давал неверный ответ для части адресов,
# на нём стоит защита сети (проверка задачи 8).
MIN_PYTHON_VERSION = (3, 12, 4)


def test_project_name_is_fixed():
    assert COMPOSE["name"] == "jw_site_check_bot"


def test_container_is_isolated_and_limited():
    assert "network_mode" not in BOT
    assert BOT["read_only"] is True
    assert BOT["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in BOT["security_opt"]
    assert BOT["mem_limit"] == BOT["memswap_limit"] == "256m"
    assert BOT["pids_limit"] == 64
    assert BOT["dns"] == ["1.1.1.1", "8.8.8.8"]
    network = COMPOSE["networks"]["site_check"]
    assert network["enable_ipv6"] is False
    assert network["ipam"]["config"][0]["subnet"] == "172.30.99.0/24"


def test_secrets_come_only_at_runtime():
    assert BOT["env_file"] == ".env"
    rules = [line for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
             if line and not line.startswith("#")]
    assert rules[0] == "*"
    assert not [rule for rule in rules if rule.startswith("!") and ".env" in rule]


def test_image_runs_as_unprivileged_user():
    assert "USER 10001:10001" in DOCKERFILE


def test_base_python_version_is_pinned_and_not_older_than_required():
    match = re.search(r"^FROM python:(\d+)\.(\d+)\.(\d+)-slim$", DOCKERFILE, re.MULTILINE)
    assert match, "версия python в базовом образе должна быть закреплена точно, вида X.Y.Z-slim"
    version = tuple(int(part) for part in match.groups())
    assert version >= MIN_PYTHON_VERSION


def test_certificates_are_not_stripped_from_base_image():
    # Поправка 2 к задаче 20: хранилище сертификатов — системное, из пакета ca-certificates. В slim-образе
    # он есть по умолчанию; смотрим только инструкции, не комментарии, — иначе их же слова роняли бы тест.
    instructions = "\n".join(line for line in DOCKERFILE.splitlines() if not line.strip().startswith("#"))
    assert "alpine" not in instructions
    assert "apt-get remove" not in instructions
    assert "apt-get purge" not in instructions
