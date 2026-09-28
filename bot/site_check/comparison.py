"""Прошлая проверка того же адреса → заметные изменения (ТЗ, 5.12). Без сети и базы: на входе строка из базы."""
from dataclasses import dataclass
from datetime import date

from bot.site_check import thresholds
from bot.site_check.audits import is_number
from bot.site_check.checks import GRADE_COLUMNS, DomainCheck
from bot.site_check.findings import Block, Grade
from bot.site_check.pipeline import CheckResult

GRADE_PREFIX = "grade_"
KNOWN_GRADES = frozenset({Grade.GOOD.value, Grade.FIX.value, Grade.BAD.value})
NO_MINIMUM = 0


@dataclass(frozen=True)
class Comparison:
    day: date
    lcp_ms: tuple[float, float] | None
    page_bytes: tuple[int, int] | None
    grades: tuple[tuple[Block, Grade, Grade], ...]


def compare(previous: DomainCheck, current: CheckResult) -> Comparison | None:
    """Только заметное; ничего — None, и абзаца в отчёте нет (ТЗ, 5.12)."""
    if current.page is None:
        return None
    before = previous.metrics
    lcp = _changed(before.get("lcp_ms"), current.page.speed.lcp_ms, thresholds.LCP_CHANGE_SHARE,
                   thresholds.LCP_CHANGE_MIN_MS)
    weight = _changed(before.get("page_bytes"), current.page.images.page_bytes, thresholds.WEIGHT_CHANGE_SHARE,
                      NO_MINIMUM)
    same_rules = before.get("rules") == thresholds.RULES_VERSION
    grades = _grade_changes(previous, current) if same_rules else ()
    if not (lcp or weight or grades):
        return None
    return Comparison(previous.created_at.date(), lcp, weight, grades)


def _changed(old, new, share: float, minimum: float):
    if not is_number(old) or not is_number(new) or old <= 0:
        return None
    delta = abs(new - old)
    return (old, new) if delta >= old * share and delta >= minimum else None


def _grade_changes(previous: DomainCheck, current: CheckResult) -> tuple[tuple[Block, Grade, Grade], ...]:
    changes = []
    for column, old in zip(GRADE_COLUMNS, previous.grades, strict=False):
        block = Block(column.removeprefix(GRADE_PREFIX))
        new = current.verdict.blocks[block].grade
        if old in KNOWN_GRADES and new is not Grade.UNKNOWN and Grade(old) is not new:
            changes.append((block, Grade(old), new))
    return tuple(changes)
