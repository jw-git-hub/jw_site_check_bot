"""Задача 33: разбор страницы в отдельном процессе (parse_worker.py) — лёгкий импорт, срок, коды выхода."""
import asyncio
import json
import os
import sys
import time

import pytest

from bot.site_check import page_fetch
from bot.site_check.head_tags import parse_head
from bot.site_check.page_contacts import parse_contacts
from bot.site_check.page_fetch import PARSE_WORKER_COMMAND, run_parse_worker

# Наследуем окружение теста (scripts/test.sh уже ставит PYTHONDONTWRITEBYTECODE=1), но не полагаемся на это
# неявно — подпроцессы здесь не должны оставлять __pycache__ в папке проекта (Syncthing).
WORKER_ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
LINUX_ONLY = pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS не соблюдается на Маке")
NORMAL_BODY = (b"<html lang='ru'><head><title>Shop</title>"
              b"<meta property='og:image' content='https://cdn.example/og.jpg'></head>"
              b"<body><footer><a href=\"tel:+79127127004\">call</a></footer></body>")


async def test_subprocess_parsing_matches_direct_parsing():
    """Обычная страница через настоящий подпроцесс — те же HeadTags/ContactFacts, что прямой разбор."""
    parsed = await run_parse_worker(PARSE_WORKER_COMMAND, None, True, NORMAL_BODY)
    html = NORMAL_BODY.decode()
    assert parsed == (parse_head(html), parse_contacts(html, True))


async def test_import_does_not_pull_in_aiogram_or_aiohttp():
    """Задача 33: модуль и то, что он импортирует, не тянут aiogram/aiohttp — процесс лёгкий и быстрый."""
    script = ("import sys, json; import bot.site_check.parse_worker; "
             "print(json.dumps({name: name in sys.modules for name in ('aiohttp', 'aiogram', 'sqlalchemy')}))")
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", script, stdout=asyncio.subprocess.PIPE,
                                                    env=WORKER_ENV)
    stdout, _ = await process.communicate()
    assert json.loads(stdout) == {"aiohttp": False, "aiogram": False, "sqlalchemy": False}


async def test_a_crashing_process_is_a_failure_not_an_exception():
    command = (sys.executable, "-c", "import sys; sys.exit(1)")
    assert await run_parse_worker(command, None, True, NORMAL_BODY) is None


async def test_a_process_that_writes_garbage_is_a_failure_not_an_exception():
    command = (sys.executable, "-c", "import sys; sys.stdout.write('не json совсем')")
    assert await run_parse_worker(command, None, True, NORMAL_BODY) is None


async def test_a_process_that_exits_before_reading_a_large_body_is_not_an_exception():
    """Ребёнок вышел раньше, чем прочитал stdin, — канал обрывается, наружу не течёт (задача 33)."""
    command = (sys.executable, "-c", "import sys; sys.exit(1)")
    assert await run_parse_worker(command, None, True, b"x" * 2_000_000) is None


async def test_a_hanging_process_is_killed_not_awaited_forever(monkeypatch):
    monkeypatch.setattr(page_fetch, "PARSE_TIMEOUT_SECONDS", 0.05)
    command = (sys.executable, "-c", "import time; time.sleep(10)")
    started = time.monotonic()
    assert await run_parse_worker(command, None, True, NORMAL_BODY) is None
    assert time.monotonic() - started < 5


@LINUX_ONLY
async def test_the_memory_limit_mechanism_stops_an_oversized_allocation():
    """Проверка самого механизма RLIMIT_AS (не константы parse_worker): на Линуксе ядро действительно не даёт
    процессу выйти за поставленный предел — на Маке тот же сценарий пропускается (см. parse_worker.limit_memory)."""
    script = "import resource; resource.setrlimit(resource.RLIMIT_AS, (20 * 1024 * 1024,) * 2); bytearray(2**28)"
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", script, env=WORKER_ENV)
    await process.wait()
    assert process.returncode != 0
