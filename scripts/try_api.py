#!/usr/bin/env python3
"""Разведка живого API перед кодом (план, задача 2). Запуск на сервере, из папки бота:

  python3 scripts/try_api.py telegram    rich-сообщение владельцу, правка, пилюли; ждёт нажатия колбэка
  python3 scripts/try_api.py pagespeed   ключ в заголовке, fields, версия Lighthouse, формат ошибок

Только стандартная библиотека. Токен и ключ читает из .env рядом и никуда не печатает.
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True

ENV_FILE = Path(".env")
TELEGRAM_URL = "https://api.telegram.org/bot{token}/{method}"
PAGESPEED_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
KEY_HEADER = "X-goog-api-key"
REQUEST_TIMEOUT_SECONDS = 120
EDIT_PAUSE_SECONDS = 5
CALLBACK_WAIT_SECONDS = 180
POLL_SECONDS = 25
CALLBACK_PREFIX = "spike:"
DM_LINK = "https://t.me/jw_dev_pro?text=" + urllib.parse.quote("Пришёл из проверки сайта: example.com")
SAMPLE_SITE = "https://jw-dev.pro/"
ERROR_SAMPLES = {
    "404": "https://jw-dev.pro/net-takoy-stranicy-404",
    "сертификат": "https://expired.badssl.com/",
    "нет домена": "https://net-takogo-domena-jw-check-2026.com/",
}
AUDIT_IDS = (
    "largest-contentful-paint", "first-contentful-paint", "total-blocking-time", "cumulative-layout-shift",
    "speed-index", "server-response-time", "image-delivery-insight", "lcp-discovery-insight",
    "render-blocking-insight", "unused-javascript", "legacy-javascript-insight", "duplicated-javascript-insight",
    "document-latency-insight", "third-parties-insight", "viewport-insight", "target-size", "meta-viewport",
    "is-on-https", "total-byte-weight", "resource-summary", "network-requests",
)
FIELDS = ("lighthouseResult(lighthouseVersion,requestedUrl,finalDisplayedUrl,runtimeError,runWarnings,audits("
          + ",".join(AUDIT_IDS) + "))")


def read_env() -> dict[str, str]:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    pairs = (line.split("=", 1) for line in lines if "=" in line and not line.lstrip().startswith("#"))
    return {name.strip(): value.strip().strip("'\"") for name, value in pairs}


def telegram(token: str, method: str, params: dict) -> dict:
    url = TELEGRAM_URL.format(token=token, method=method)
    request = urllib.request.Request(url, data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        return json.load(error)


def report(name: str, answer: dict) -> None:
    print(f"{name}: {'ok' if answer.get('ok') else answer.get('description')}")


def code(text: str) -> dict:
    return {"type": "code", "text": text}


def cell(text: str, is_header: bool = False) -> dict:
    return {"text": text, "is_header": is_header, "align": "left", "valign": "top"}


def sample_report() -> dict:
    pills = [{"type": "button", "button": {"text": "Ссылка в личку", "url": DM_LINK, "style": "primary"}}, "  ",
             {"type": "button", "button": {"text": "Колбэк", "callback_data": CALLBACK_PREFIX + "plain"}}, "  ",
             {"type": "button", "button": {"text": "Колбэк-ссылка", "callback_data": CALLBACK_PREFIX + "link",
                                           "style": "link"}}]
    table = {"type": "table", "cells": [[cell("Что", True), cell("Сколько", True)], [cell("LCP"), cell("1,4 с")]]}
    footer = [{"type": "url", "url": "https://jw-dev.pro", "text": code("jw-dev.pro")}, code(" · "),
              {"type": "url", "url": "https://t.me/jw_dev_pro", "text": code("@jw_dev_pro")}]
    return {"blocks": [
        {"type": "paragraph", "text": [code(">jw_ ~/site-check")]},
        {"type": "heading", "size": 1, "text": "Разведка API"},
        {"type": "paragraph", "text": ["Первая строка\nвторая строка после переноса"]},
        {"type": "heading", "size": 2, "text": "Скорость — хорошо"},
        {"type": "paragraph", "text": ["> пункт с маркером"]},
        {"type": "paragraph", "text": pills},
        {"type": "details", "summary": "Цифры для поста", "blocks": [table]},
        {"type": "divider"},
        {"type": "footer", "text": footer},
    ]}


def wait_for_callback(token: str) -> None:
    print(f"Нажмите «Колбэк» или «Колбэк-ссылка» в Telegram — жду {CALLBACK_WAIT_SECONDS} с")
    deadline, poll = time.monotonic() + CALLBACK_WAIT_SECONDS, {"timeout": POLL_SECONDS}
    while time.monotonic() < deadline:
        answer = telegram(token, "getUpdates", poll)
        for update in answer.get("result", []):
            poll["offset"] = update["update_id"] + 1
            callback = update.get("callback_query")
            if callback and callback.get("data", "").startswith(CALLBACK_PREFIX):
                telegram(token, "answerCallbackQuery", {"callback_query_id": callback["id"], "text": "Колбэк дошёл"})
                print("колбэк дошёл:", callback["data"])
                return
    print("колбэк не пришёл")


def try_telegram(env: dict[str, str]) -> None:
    token, owner = env["BOT_TOKEN"], int(env["ADMIN_ID"])
    status = telegram(token, "sendRichMessage", {"chat_id": owner, "rich_message": {"blocks": [
        {"type": "paragraph", "text": [code(">jw_ ~/site-check")]},
        {"type": "paragraph", "text": ["Проверяю example.com…"]}]}})
    report("sendRichMessage", status)
    if not status.get("ok"):
        return
    time.sleep(EDIT_PAUSE_SECONDS)
    message_id = status["result"]["message_id"]
    edited = telegram(token, "editMessageText", {"chat_id": owner, "message_id": message_id,
                                                 "rich_message": sample_report()})
    report("editMessageText → отчёт", edited)
    wait_for_callback(token)
    print("Посмотрите сообщение: перенос \\n в абзаце, вид пилюль и стилей, таблица, раскрывающийся блок, подвал.")
    print("Нажмите «Ссылка в личку»: подставился ли текст «Пришёл из проверки сайта: example.com»?")


def pagespeed(url: str, key: str, with_fields: bool) -> tuple[int, int, dict]:
    params = [("url", url), ("strategy", "mobile"), ("category", "performance"), ("category", "accessibility"),
              ("category", "best-practices"), ("locale", "en")]
    if with_fields:
        params.append(("fields", FIELDS))
    request = urllib.request.Request(PAGESPEED_URL + "?" + urllib.parse.urlencode(params), headers={KEY_HEADER: key})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status, body = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, body = error.code, error.read()
    return status, len(body), json.loads(body or b"{}")


def describe(payload: dict) -> None:
    result = payload.get("lighthouseResult", {})
    audits = result.get("audits", {})
    print("  Lighthouse:", result.get("lighthouseVersion"), "| итоговый адрес:", result.get("finalDisplayedUrl"))
    print("  нет в ответе:", [name for name in AUDIT_IDS if name not in audits] or "всё на месте")
    items = audits.get("viewport-insight", {}).get("details", {}).get("items") or [{}]
    print("  текст метатега viewport:", (items[0].get("node") or {}).get("snippet"))
    for name in ("viewport-insight", "target-size", "meta-viewport", "image-delivery-insight"):
        audit = audits.get(name, {})
        print(f"  {name}: score={audit.get('score')} mode={audit.get('scoreDisplayMode')} "
              f"savings={audit.get('metricSavings')}")


def error_text(payload: dict) -> str:
    message = (payload.get("error") or {}).get("message")
    runtime = (payload.get("lighthouseResult") or {}).get("runtimeError")
    return str(message or runtime)[:300]


def try_pagespeed(env: dict[str, str]) -> None:
    key = env["PAGESPEED_API_KEY"]
    status, size, payload = pagespeed(SAMPLE_SITE, key, with_fields=True)
    print(f"ключ в заголовке, с fields: HTTP {status}, {size} байт")
    describe(payload)
    status, size, _ = pagespeed(SAMPLE_SITE, key, with_fields=False)
    print(f"без fields: HTTP {status}, {size} байт")
    for name, url in ERROR_SAMPLES.items():
        status, _, payload = pagespeed(url, key, with_fields=True)
        print(f"ошибка «{name}»: HTTP {status} — {error_text(payload)}")


def main() -> int:
    actions = {"telegram": try_telegram, "pagespeed": try_pagespeed}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        print(__doc__)
        return 2
    actions[sys.argv[1]](read_env())
    return 0


if __name__ == "__main__":
    sys.exit(main())
