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
    CONTACTS = "contacts"
    SEARCH = "search"
    PREVIEW = "preview"
    READABILITY = "readability"


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
    CLOSED_META = "closed_meta"
    CLOSED_HEADER = "closed_header"
    CLOSED_ROBOTS = "closed_robots"
    ROBOTS_UNREACHABLE = "robots_unreachable"
    ROBOTS_ERRORS = "robots_errors"
    NO_TITLE = "no_title"
    NO_DESCRIPTION = "no_description"
    CANONICAL_FOREIGN = "canonical_foreign"
    NO_PREVIEW_IMAGE = "no_preview_image"
    PREVIEW_IMAGE_BROKEN = "preview_image_broken"
    PREVIEW_IMAGE_SVG = "preview_image_svg"
    PREVIEW_IMAGE_RELATIVE = "preview_image_relative"
    NO_PREVIEW_TITLE = "no_preview_title"
    LOW_CONTRAST = "low_contrast"
    NO_ALT = "no_alt"
    NO_LANG = "no_lang"
    NO_CONTACTS = "no_contacts"
    PHONE_NOT_LINK = "phone_not_link"
    CALL_WITHOUT_CODE = "call_without_code"
    NO_PRIVACY_POLICY = "no_privacy_policy"
    NO_ANALYTICS = "no_analytics"
    DOMAIN_EXPIRING = "domain_expiring"
    IMAGES_STRETCHED = "images_stretched"
    IMAGES_BLURRY = "images_blurry"


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
    UNBLOCK_META = "fix_unblock_meta"
    UNBLOCK_HEADER = "fix_unblock_header"
    UNBLOCK_ROBOTS = "fix_unblock_robots"
    REPAIR_ROBOTS = "fix_repair_robots"
    FIX_ROBOTS_ERRORS = "fix_robots_errors"
    ADD_TITLE = "fix_add_title"
    ADD_DESCRIPTION = "fix_add_description"
    OWN_CANONICAL = "fix_own_canonical"
    ADD_PREVIEW_IMAGE = "fix_add_preview_image"
    REPLACE_PREVIEW_IMAGE = "fix_replace_preview_image"
    RASTER_PREVIEW_IMAGE = "fix_raster_preview_image"
    FULL_PREVIEW_IMAGE_URL = "fix_full_preview_image_url"
    ADD_PREVIEW_TITLE = "fix_add_preview_title"
    RAISE_CONTRAST = "fix_raise_contrast"
    ADD_ALT = "fix_add_alt"
    SET_LANG = "fix_set_lang"
    ADD_CONTACTS = "fix_add_contacts"
    LINK_PHONE = "fix_link_phone"
    FULL_CALL_NUMBER = "fix_full_call_number"
    ADD_POLICY_LINK = "fix_add_policy_link"
    ADD_COUNTER = "fix_add_counter"
    RENEW_DOMAIN = "fix_renew_domain"
    FIX_PROPORTIONS = "fix_proportions"
    UPLOAD_LARGER = "fix_upload_larger"


@dataclass(frozen=True)
class FindingItem:
    finding: Finding
    grade: Grade
    cause: Cause = Cause.UNKNOWN
    cert_problem: TlsOutcome | None = None
    until: date | None = None
    days_left: int | None = None
    server_ms: float | None = None
    count: int | None = None          # сколько мест или картинок (5.8), сколько ошибок в robots.txt (5.6)
    examples: tuple[str, ...] = ()    # примеры с сайта: текст с бледным фоном, имена картинок (5.8)
    detail: str | None = None         # код ответа (robots.txt, картинка превью), чужой хост в canonical


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


def not_checked(block: Block) -> BlockVerdict:
    """Блок без главного источника — «неизвестно». У трёх новых блоков в отчёте не печатается (ТЗ, 6.1)."""
    return BlockVerdict(block, Grade.UNKNOWN, unknown_reason=UnknownReason.NO_DATA)
