"""Общие типы оценок (ТЗ, раздел 6): оценка, блок, находка, вердикт блока, пункт «что поправить». Общие для сборщика
оценок (verdict.py) и модулей новых блоков (*_block.py); правил здесь нет, кроме сборки оценки блока."""
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from bot.site_check.tls_check import TlsOutcome


class Grade(StrEnum):
    GOOD = "good"
    FIX = "fix"
    BAD = "bad"
    UNKNOWN = "unknown"


GRADE_WEIGHT = {Grade.GOOD: 0, Grade.FIX: 1, Grade.BAD: 2}


class Block(StrEnum):
    SPEED = "speed"
    MOBILE = "mobile"
    SECURITY = "security"
    IMAGES = "images"


class Cause(StrEnum):
    IMAGES = "images"
    SCRIPTS = "scripts"
    SERVER = "server"
    UNKNOWN = "unknown"


class Finding(StrEnum):
    """Порядок внутри блока — как в таблицах ТЗ, раздел 5: главная находка блока — первая."""
    SLOW = "slow"
    NO_MOBILE = "no_mobile"
    FIXED_WIDTH = "fixed_width"
    TAP_TARGETS = "tap_targets"
    NO_ZOOM = "no_zoom"
    NO_HTTPS = "no_https"
    CERT_INVALID = "cert_invalid"
    CERT_UNTRUSTED = "cert_untrusted"
    CERT_EXPIRING = "cert_expiring"
    HTTPS_AFTER_REDIRECT = "https_after_redirect"
    NO_REDIRECT = "no_redirect"
    MIXED_CONTENT = "mixed_content"
    INCOMPLETE_CHAIN = "incomplete_chain"
    HEAVY_PAGE = "heavy_page"


FINDING_ORDER = tuple(Finding)


class UnknownReason(StrEnum):
    CERT_BLOCKS = "cert_blocks"
    NO_DATA = "no_data"
    OWN_CHECKS_FAILED = "own_checks_failed"


class FixKey(StrEnum):
    ENABLE_HTTPS = "fix_enable_https"
    REPLACE_CERT = "fix_replace_cert"
    CHECK_RENEWAL = "fix_check_renewal"
    HTTPS_AFTER_REDIRECT = "fix_https_after_redirect"
    ENABLE_REDIRECT = "fix_enable_redirect"
    SECURE_FILES = "fix_secure_files"
    FULL_CHAIN = "fix_full_chain"
    MAKE_MOBILE = "fix_make_mobile"
    FIT_WIDTH = "fix_fit_width"
    SPACE_BUTTONS = "fix_space_buttons"
    ALLOW_ZOOM = "fix_allow_zoom"
    COMPRESS_IMAGES = "fix_compress_images"
    TRIM_SCRIPTS = "fix_trim_scripts"
    FIX_SERVER = "fix_server"
    FIND_SLOWDOWN = "fix_find_slowdown"


@dataclass(frozen=True)
class FindingItem:
    finding: Finding
    grade: Grade
    cause: Cause = Cause.UNKNOWN
    cert_problem: TlsOutcome | None = None
    until: date | None = None
    days_left: int | None = None
    server_ms: float | None = None


@dataclass(frozen=True)
class BlockVerdict:
    block: Block
    grade: Grade
    findings: tuple[FindingItem, ...] = ()
    unknown_reason: UnknownReason | None = None


@dataclass(frozen=True)
class FixItem:
    key: FixKey
    source: FindingItem


def graded(block: Block, findings: list[FindingItem]) -> BlockVerdict:
    """Оценка блока — худшая из находок; находки — в порядке таблиц ТЗ, раздел 5 (ТЗ, 6.1)."""
    ordered = tuple(sorted(findings, key=lambda item: FINDING_ORDER.index(item.finding)))
    grade = max((item.grade for item in ordered), key=GRADE_WEIGHT.__getitem__, default=Grade.GOOD)
    return BlockVerdict(block, grade, ordered)
