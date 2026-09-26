"""Замеры → оценки блоков, итог и «что поправить» (ТЗ, раздел 6). Правила в коде, без нейросети; слова — в report.py."""
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from urllib.parse import urlsplit

from bot.site_check import thresholds
from bot.site_check.lighthouse import AuditState, PageFacts, SpeedFacts
from bot.site_check.tls_check import HTTPS_PREFIX, CertInfo, RedirectState, TlsFacts, TlsOutcome

MAX_TROUBLES = 2
MAX_FIXES = 3
FIXED_WIDTH = re.compile(r"width\s*=\s*\d", re.IGNORECASE)


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


REPORT_ORDER = (Block.SPEED, Block.MOBILE, Block.SECURITY, Block.IMAGES)
PRIORITY_ORDER = (Block.SECURITY, Block.MOBILE, Block.SPEED, Block.IMAGES)  # ТЗ, 6.3


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


class SummaryKind(StrEnum):
    ALL_GOOD = "all_good"
    GOOD_WITH_UNKNOWN = "good_with_unknown"
    HAS_BAD = "has_bad"
    ONLY_FIX = "only_fix"
    CERT_BLOCKS = "cert_blocks"


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


@dataclass(frozen=True)
class SecurityFacts:
    tls: tuple[TlsFacts, ...]  # присланный хост и итоговый, если отличается
    redirects: tuple[RedirectState, ...]
    insecure_urls: tuple[str, ...]
    cert_blocks: bool = False


@dataclass(frozen=True)
class Verdict:
    blocks: dict[Block, BlockVerdict]
    summary: SummaryKind
    troubles: tuple[FindingItem, ...]
    fixes: tuple[FixItem, ...]


TLS_INVALID = frozenset({TlsOutcome.EXPIRED, TlsOutcome.NOT_YET_VALID, TlsOutcome.WRONG_HOST, TlsOutcome.SELF_SIGNED})
TLS_UNKNOWN = frozenset({TlsOutcome.HANDSHAKE_FAILED, TlsOutcome.CONNECT_FAILED})
FIX_KEYS = {
    Finding.NO_HTTPS: FixKey.ENABLE_HTTPS, Finding.CERT_INVALID: FixKey.REPLACE_CERT,
    Finding.CERT_UNTRUSTED: FixKey.REPLACE_CERT, Finding.CERT_EXPIRING: FixKey.CHECK_RENEWAL,
    Finding.HTTPS_AFTER_REDIRECT: FixKey.HTTPS_AFTER_REDIRECT,
    Finding.NO_REDIRECT: FixKey.ENABLE_REDIRECT, Finding.MIXED_CONTENT: FixKey.SECURE_FILES,
    Finding.INCOMPLETE_CHAIN: FixKey.FULL_CHAIN, Finding.NO_MOBILE: FixKey.MAKE_MOBILE,
    Finding.FIXED_WIDTH: FixKey.FIT_WIDTH, Finding.TAP_TARGETS: FixKey.SPACE_BUTTONS,
    Finding.NO_ZOOM: FixKey.ALLOW_ZOOM, Finding.HEAVY_PAGE: FixKey.COMPRESS_IMAGES,
}
SLOW_FIX_KEYS = {Cause.IMAGES: FixKey.COMPRESS_IMAGES, Cause.SCRIPTS: FixKey.TRIM_SCRIPTS,
                 Cause.SERVER: FixKey.FIX_SERVER, Cause.UNKNOWN: FixKey.FIND_SLOWDOWN}


def judge(page: PageFacts | None, security: SecurityFacts, today: date) -> Verdict:
    blocks = {
        Block.SPEED: _speed_block(page, security),
        Block.MOBILE: _mobile_block(page, security),
        Block.SECURITY: _security_block(page, security, today),
        Block.IMAGES: _images_block(page, security),
    }
    return Verdict(blocks, _summary_kind(blocks, security), _troubles(blocks), _fixes(blocks))


def _unknown(block: Block, security: SecurityFacts) -> BlockVerdict:
    reason = UnknownReason.CERT_BLOCKS if security.cert_blocks else UnknownReason.NO_DATA
    return BlockVerdict(block, Grade.UNKNOWN, unknown_reason=reason)


def _graded(block: Block, findings: list[FindingItem]) -> BlockVerdict:
    ordered = tuple(sorted(findings, key=lambda item: FINDING_ORDER.index(item.finding)))
    grade = max((item.grade for item in ordered), key=GRADE_WEIGHT.__getitem__, default=Grade.GOOD)
    return BlockVerdict(block, grade, ordered)


def _speed_block(page: PageFacts | None, security: SecurityFacts) -> BlockVerdict:
    if page is None or page.speed.lcp_ms is None:
        return _unknown(Block.SPEED, security)
    lcp = page.speed.lcp_ms
    if lcp <= thresholds.LCP_GOOD_MS:
        return BlockVerdict(Block.SPEED, Grade.GOOD)
    grade = Grade.FIX if lcp <= thresholds.LCP_FIX_MS else Grade.BAD
    item = FindingItem(Finding.SLOW, grade, cause=main_cause(page.speed), server_ms=page.speed.server_ms)
    return _graded(Block.SPEED, [item])


def main_cause(speed: SpeedFacts) -> Cause:
    lost = {
        Cause.IMAGES: speed.image_savings_ms,
        Cause.SCRIPTS: speed.script_savings_ms + _excess(speed.tbt_ms, thresholds.TBT_ALLOWANCE_MS),
        Cause.SERVER: speed.server_savings_ms + _excess(speed.server_ms, thresholds.SERVER_ALLOWANCE_MS),
    }
    cause, milliseconds = max(lost.items(), key=lambda pair: pair[1])
    return cause if milliseconds >= thresholds.CAUSE_MIN_MS else Cause.UNKNOWN


def _excess(value: float | None, allowance: float) -> float:
    return max(0.0, (value or 0.0) - allowance)


def _mobile_block(page: PageFacts | None, security: SecurityFacts) -> BlockVerdict:
    if page is None or page.mobile.viewport is AuditState.UNKNOWN:
        return _unknown(Block.MOBILE, security)
    mobile = page.mobile
    findings = []
    if mobile.viewport is AuditState.FAILED:
        fixed = bool(mobile.viewport_snippet and FIXED_WIDTH.search(mobile.viewport_snippet))
        findings.append(FindingItem(Finding.FIXED_WIDTH if fixed else Finding.NO_MOBILE, Grade.BAD))
    if mobile.target_size is AuditState.FAILED:
        findings.append(FindingItem(Finding.TAP_TARGETS, Grade.FIX))
    if mobile.meta_viewport is AuditState.FAILED:
        findings.append(FindingItem(Finding.NO_ZOOM, Grade.FIX))
    return _graded(Block.MOBILE, findings)


def _security_block(page: PageFacts | None, security: SecurityFacts, today: date) -> BlockVerdict:
    known = [facts for facts in security.tls if facts.outcome not in TLS_UNKNOWN]
    if security.cert_blocks:
        return _graded(Block.SECURITY, [_blocking_cert_finding(known)])
    if not known:
        return BlockVerdict(Block.SECURITY, Grade.UNKNOWN, unknown_reason=UnknownReason.OWN_CHECKS_FAILED)
    findings = [item for facts in known for item in _tls_findings(facts, page, today)]
    findings += _transport_findings(security, known, page)
    return _graded(Block.SECURITY, _unique(findings))


def _cert_until(cert: CertInfo | None) -> date | None:
    return cert.not_after.date() if cert else None


def _invalid_cert(facts: TlsFacts) -> FindingItem:
    return FindingItem(Finding.CERT_INVALID, Grade.BAD, cert_problem=facts.outcome, until=_cert_until(facts.cert))


def _blocking_cert_finding(known: list[TlsFacts]) -> FindingItem:
    """Браузер не открыл сайт из-за сертификата (ТЗ, 6.4). Своя проверка мягче — всё равно «не доверяет»."""
    for facts in known:
        if facts.outcome in TLS_INVALID:
            return _invalid_cert(facts)
    return FindingItem(Finding.CERT_UNTRUSTED, Grade.BAD)


def _tls_findings(facts: TlsFacts, page: PageFacts | None, today: date) -> list[FindingItem]:
    if facts.outcome is TlsOutcome.NO_HTTPS:
        if _no_https_blocks_transport(facts, page):
            return [FindingItem(Finding.NO_HTTPS, Grade.BAD)]
        return [FindingItem(Finding.HTTPS_AFTER_REDIRECT, Grade.FIX)]
    if facts.outcome in TLS_INVALID:
        return [_invalid_cert(facts)]
    if facts.outcome is TlsOutcome.OTHER:
        return [FindingItem(Finding.CERT_UNTRUSTED, Grade.BAD)]
    if facts.outcome is TlsOutcome.INCOMPLETE_CHAIN:
        return [FindingItem(Finding.INCOMPLETE_CHAIN, Grade.FIX, until=_cert_until(facts.cert))] + \
            _expiry_findings(facts.cert, today)
    return _expiry_findings(facts.cert, today)


def _expiry_findings(cert: CertInfo | None, today: date) -> list[FindingItem]:
    if cert is None:
        return []
    days_left = (cert.not_after.date() - today).days
    lifetime = (cert.not_after - cert.not_before).days
    short = lifetime <= thresholds.SHORT_CERT_DAYS
    warn_days = lifetime * thresholds.SHORT_CERT_WARN_SHARE if short else thresholds.CERT_WARN_DAYS
    if days_left >= warn_days:
        return []
    return [FindingItem(Finding.CERT_EXPIRING, Grade.FIX, until=cert.not_after.date(), days_left=days_left)]


def _transport_findings(security: SecurityFacts, known: list[TlsFacts], page: PageFacts | None) -> list[FindingItem]:
    if any(_no_https_blocks_transport(facts, page) for facts in known):
        return []
    findings = []
    if RedirectState.NO_REDIRECT in security.redirects:
        findings.append(FindingItem(Finding.NO_REDIRECT, Grade.FIX))
    if security.insecure_urls:
        findings.append(FindingItem(Finding.MIXED_CONTENT, Grade.FIX))
    return findings


def _no_https_blocks_transport(facts: TlsFacts, page: PageFacts | None) -> bool:
    """NO_HTTPS остаётся «плохо» и прячет находки транспорта («нет переадресации», «файлы без защиты»), только
    если с этого хоста нет своей переадресации на https в другом месте. Голый домен у регистратора, который сам
    переводит на защищённую версию на другом хосте, — «стоит поправить», а не «плохо» (задача 13a, решение
    владельца 26.09.2026).
    """
    if facts.outcome is not TlsOutcome.NO_HTTPS:
        return False
    if page is None or not page.final_url.startswith(HTTPS_PREFIX):
        return True
    return urlsplit(page.final_url).hostname == facts.host


def _unique(findings: list[FindingItem]) -> list[FindingItem]:
    seen: set[Finding] = set()
    result = []
    for item in findings:
        if item.finding not in seen:
            seen.add(item.finding)
            result.append(item)
    return result


def _images_block(page: PageFacts | None, security: SecurityFacts) -> BlockVerdict:
    if page is None or page.images.page_bytes is None:
        return _unknown(Block.IMAGES, security)
    size = page.images.page_bytes
    if size <= thresholds.PAGE_GOOD_BYTES:
        return BlockVerdict(Block.IMAGES, Grade.GOOD)
    grade = Grade.FIX if size <= thresholds.PAGE_FIX_BYTES else Grade.BAD
    return _graded(Block.IMAGES, [FindingItem(Finding.HEAVY_PAGE, grade)])


def _summary_kind(blocks: dict[Block, BlockVerdict], security: SecurityFacts) -> SummaryKind:
    grades = {verdict.grade for verdict in blocks.values()}
    if security.cert_blocks:
        return SummaryKind.CERT_BLOCKS
    if Grade.BAD in grades:
        return SummaryKind.HAS_BAD
    if Grade.FIX in grades:
        return SummaryKind.ONLY_FIX
    return SummaryKind.GOOD_WITH_UNKNOWN if Grade.UNKNOWN in grades else SummaryKind.ALL_GOOD


def _troubles(blocks: dict[Block, BlockVerdict]) -> tuple[FindingItem, ...]:
    """Главная находка каждого проблемного блока: сперва «плохо», внутри — по важности блоков (ТЗ, 6.3)."""
    troubled = [blocks[block] for block in PRIORITY_ORDER if blocks[block].findings]
    ordered = sorted(troubled, key=lambda verdict: -GRADE_WEIGHT[verdict.grade])
    return tuple(verdict.findings[0] for verdict in ordered)[:MAX_TROUBLES]


def _fixes(blocks: dict[Block, BlockVerdict]) -> tuple[FixItem, ...]:
    findings = [item for block in PRIORITY_ORDER for item in blocks[block].findings]
    ordered = sorted(findings, key=lambda item: -GRADE_WEIGHT[item.grade])
    fixes: list[FixItem] = []
    for item in ordered:
        key = fix_key(item)
        if key not in {fix.key for fix in fixes}:
            fixes.append(FixItem(key, item))
    return tuple(fixes[:MAX_FIXES])


def fix_key(item: FindingItem) -> FixKey:
    """Медленно из-за картинок и тяжёлые картинки — один пункт (ТЗ, 6.5)."""
    return SLOW_FIX_KEYS[item.cause] if item.finding is Finding.SLOW else FIX_KEYS[item.finding]
