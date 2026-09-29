"""Формат обмена с parse_worker.py (задача 33): страница разбирается в отдельном процессе, здесь — только
кодирование запроса и разбор ответа, без сети и без предела памяти (это забота page_fetch.py и parse_worker.py).

Вход процесса (stdin): первая строка — JSON `{"charset": str|null, "complete": bool}`, дальше без разделителя —
сырое тело страницы. Выход (stdout): JSON `{"head": {...}, "contacts": {...}, "complete": true}` — третье поле
пишет только сам конец разбора: его отсутствие или ложное значение — как исключение внутри процесса, до этого
места дело не дошло (пустой/кривой вывод, задача 33).
"""
import json
from dataclasses import asdict

from bot.site_check.head_tags import HeadTags
from bot.site_check.page_contacts import ContactFacts

REQUEST_HEADER_END = b"\n"
BAD_RESPONSE_ERRORS = (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError)


class BadResponse(ValueError):
    """Пустой/кривой вывод процесса — разбор внутри него не дошёл до конца или вывод испорчен."""


def encode_request(charset: str | None, complete: bool, body: bytes) -> bytes:
    header = json.dumps({"charset": charset, "complete": complete}).encode()
    return header + REQUEST_HEADER_END + body


def decode_request(raw: bytes) -> tuple[str | None, bool, bytes]:
    header, _, body = raw.partition(REQUEST_HEADER_END)
    meta = json.loads(header)
    return meta["charset"], meta["complete"], body


def encode_response(head: HeadTags, contacts: ContactFacts) -> bytes:
    payload = {"head": asdict(head), "contacts": _contacts_payload(contacts), "complete": True}
    return json.dumps(payload).encode()


def _contacts_payload(contacts: ContactFacts) -> dict:
    return {**asdict(contacts), "markers": sorted(contacts.markers)}


def decode_response(raw: bytes) -> tuple[HeadTags, ContactFacts]:
    try:
        payload = json.loads(raw)
        head = HeadTags(**payload["head"])
        contacts = _contacts_from_payload(payload["contacts"])
        finished = payload["complete"]
    except BAD_RESPONSE_ERRORS as error:
        raise BadResponse(type(error).__name__) from None
    if finished is not True:
        raise BadResponse("незавершённый вывод")
    return head, contacts


def _contacts_from_payload(data: dict) -> ContactFacts:
    fields = {**data, "markers": frozenset(data["markers"]), "short_call_links": tuple(data["short_call_links"])}
    return ContactFacts(**fields)
