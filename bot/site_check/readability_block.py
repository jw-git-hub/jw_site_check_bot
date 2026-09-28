"""«Удобство чтения» (ТЗ, 5.8): контраст, подписи картинок, язык страницы — из категории accessibility.

Фильтры ложных срабатываний: плашки конструкторов (их владелец не правит), элементы нулевого размера (скрытые,
лайтбоксы), значки меньше ALT_MIN_SIDE_PX. «Плохо» не бывает; ссылки и кнопки без названия и поля без подписи —
только счёт для «Подробных замеров» (ТЗ, 7.5).
"""
import re
from dataclasses import dataclass
from typing import Any

from bot.site_check.audits import WHITESPACE, AuditState, audit_items, audit_state, file_name, is_number
from bot.site_check.findings import Block, BlockVerdict, Finding, FindingItem, Grade, graded, not_checked
from bot.site_check.thresholds import ALT_EXAMPLES, ALT_MIN_SIDE_PX, CONTRAST_EXAMPLES

# Плашки конструкторов (ТЗ, 5.8). Список дополняется по живому прогону (задача 33).
PLATFORM_BADGE_MARKERS = ("t-tildalabel", "tildacopy", "WIX_ADS", "Made with Wix")
NODE_TEXT_KEYS = ("snippet", "selector", "nodeLabel")
SRC_ATTRIBUTE = re.compile(r'\bsrc="([^"]*)"')
LIGHTHOUSE_CUT_MARK = "…"    # так Lighthouse обрезает длинный snippet — имени из такого адреса нет
DATA_URI_PREFIX = "data:"
LANG_AUDITS = ("html-has-lang", "html-lang-valid")
CONTROL_AUDITS = ("link-name", "button-name")


@dataclass(frozen=True)
class ReadabilityFacts:
    contrast: AuditState
    contrast_examples: tuple[str, ...]   # видимый текст мест с бледным текстом, после фильтров
    contrast_count: int
    alt: AuditState
    alt_missing: tuple[str, ...]         # имена картинок без подписи — только из необрезанных адресов
    alt_missing_count: int
    alt_icons_skipped: int               # сколько мелких значков не в счёт
    lang: AuditState
    unnamed_controls: int                # ссылки и кнопки без названия — только владельцу
    unlabeled_fields: int                # поля без подписи — только владельцу


def parse_readability(audits: dict[str, Any]) -> ReadabilityFacts:
    contrast = _visible_nodes(audits, "color-contrast")
    images = _visible_nodes(audits, "image-alt")
    big = [node for node in images if _big_enough(node)]
    content = _unique_by_src(big)
    return ReadabilityFacts(
        contrast=audit_state(audits.get("color-contrast")),
        contrast_examples=tuple(label for node in contrast if (label := _label(node))),
        contrast_count=len(contrast),
        alt=audit_state(audits.get("image-alt")),
        alt_missing=tuple(name for node in content if (name := _image_name(node))),
        alt_missing_count=len(content),
        alt_icons_skipped=len(images) - len(big),
        lang=_lang_state(audits),
        unnamed_controls=sum(len(_visible_nodes(audits, name)) for name in CONTROL_AUDITS),
        unlabeled_fields=len(_visible_nodes(audits, "label")),
    )


def judge_readability(facts: ReadabilityFacts | None) -> BlockVerdict:
    """Главный источник — color-contrast или image-alt (ТЗ, 6.1): нет обоих — «неизвестно», блок не печатается."""
    if facts is None or (facts.contrast is AuditState.UNKNOWN and facts.alt is AuditState.UNKNOWN):
        return not_checked(Block.READABILITY)
    return graded(Block.READABILITY, [*_contrast(facts), *_alt(facts), *_lang(facts)])


def _contrast(facts: ReadabilityFacts) -> list[FindingItem]:
    if not facts.contrast_count:
        return []
    return [FindingItem(Finding.LOW_CONTRAST, Grade.FIX, count=facts.contrast_count,
                        examples=facts.contrast_examples[:CONTRAST_EXAMPLES])]


def _alt(facts: ReadabilityFacts) -> list[FindingItem]:
    if not facts.alt_missing_count:
        return []
    return [FindingItem(Finding.NO_ALT, Grade.FIX, count=facts.alt_missing_count,
                        examples=facts.alt_missing[:ALT_EXAMPLES])]


def _lang(facts: ReadabilityFacts) -> list[FindingItem]:
    return [FindingItem(Finding.NO_LANG, Grade.FIX)] if facts.lang is AuditState.FAILED else []


def _visible_nodes(audits: dict[str, Any], name: str) -> list[dict[str, Any]]:
    nodes = [item.get("node") for item in audit_items(audits, name)]
    return [node for node in nodes if isinstance(node, dict) and not _is_badge(node) and not _is_zero_size(node)]


def _is_badge(node: dict[str, Any]) -> bool:
    text = " ".join(str(node.get(key, "")) for key in NODE_TEXT_KEYS)
    return any(marker in text for marker in PLATFORM_BADGE_MARKERS)


def _sides(node: dict[str, Any]) -> tuple[float, float] | None:
    rect = node.get("boundingRect")
    if not isinstance(rect, dict) or not (is_number(rect.get("width")) and is_number(rect.get("height"))):
        return None
    return float(rect["width"]), float(rect["height"])


def _is_zero_size(node: dict[str, Any]) -> bool:
    sides = _sides(node)
    return sides is not None and min(sides) == 0


def _big_enough(node: dict[str, Any]) -> bool:
    """Значки меньше ALT_MIN_SIDE_PX не в счёт; размера нет — картинку считаем (ТЗ, 5.8)."""
    sides = _sides(node)
    return sides is None or min(sides) >= ALT_MIN_SIDE_PX


def _src(node: dict[str, Any]) -> str | None:
    found = SRC_ATTRIBUTE.search(str(node.get("snippet", "")))
    return found.group(1) if found else None


def _unique_by_src(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Одинаковый адрес картинки считается один раз (ТЗ, 5.8) — карусели повторяют слайды."""
    seen: set[str] = set()
    unique = []
    for node in nodes:
        key = _src(node) or str(node.get("snippet", ""))
        if key not in seen:
            seen.add(key)
            unique.append(node)
    return unique


def _image_name(node: dict[str, Any]) -> str | None:
    src = _src(node)
    if not src or LIGHTHOUSE_CUT_MARK in src or src.startswith(DATA_URI_PREFIX):
        return None
    return file_name(src) or None


def _label(node: dict[str, Any]) -> str:
    return WHITESPACE.sub(" ", str(node.get("nodeLabel", ""))).strip()


def _lang_state(audits: dict[str, Any]) -> AuditState:
    states = [audit_state(audits.get(name)) for name in LANG_AUDITS]
    return AuditState.FAILED if AuditState.FAILED in states else states[0]
