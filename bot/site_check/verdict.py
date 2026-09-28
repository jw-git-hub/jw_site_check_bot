"""Замеры → оценки блоков, итог и «что поправить» (ТЗ, раздел 6). Правила в коде, без нейросети; слова — в report.py."""
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from urllib.parse import urlsplit

from bot.site_check import thresholds
from bot.site_check.contacts_block import judge_contacts
from bot.site_check.findings import (Block, BlockVerdict, Cause, Finding, FindingItem, FixItem, FixKey, Grade,
                                     UnknownReason, graded)
from bot.site_check.lighthouse import AuditState, PageFacts, SpeedFacts
from bot.site_check.page_fetch import PagePreview
from bot.site_check.preview_block import judge_preview
from bot.site_check.readability_block import judge_readability
from bot.site_check.search_block import judge_search
from bot.site_check.tls_check import HTTPS_PREFIX, CertInfo, RedirectState, TlsFacts, TlsOutcome
from bot.site_check.url_input import to_ascii_host

MAX_TROUBLES = 2
MAX_FIXES = 3
FIXED_WIDTH = re.compile(r"width\s*=\s*\d", re.IGNORECASE)

CORE_BLOCKS = (Block.SPEED, Block.MOBILE, Block.SECURITY, Block.IMAGES)
NEW_BLOCKS = (Block.CONTACTS, Block.SEARCH, Block.PREVIEW, Block.READABILITY)  # ТЗ, 7.1: контакты — после картинок
REPORT_ORDER = (*CORE_BLOCKS, *NEW_BLOCKS)  # ТЗ, 7.1
# ТЗ, 6.3: сперва все «плохо», потом все «стоит поправить», у каждой группы свой порядок блоков. У заявок
# и контактов, ссылки в мессенджерах и удобства чтения «плохо» не бывает (ТЗ, 6.1) — в первом порядке их нет.
BAD_PRIORITY = (Block.SECURITY, Block.SEARCH, Block.MOBILE, Block.SPEED, Block.IMAGES)
FIX_PRIORITY = (Block.SECURITY, Block.MOBILE, Block.SPEED, Block.IMAGES, Block.CONTACTS, Block.SEARCH, Block.PREVIEW,
                Block.READABILITY)
LAST_RESORT = frozenset({Finding.NO_ANALYTICS})  # ТЗ, 6.3 п. 7: последствие для владельца, не для посетителя


class SummaryKind(StrEnum):
    ALL_GOOD = "all_good"
    GOOD_WITH_UNKNOWN = "good_with_unknown"
    HAS_BAD = "has_bad"
    ONLY_FIX = "only_fix"
    CERT_BLOCKS = "cert_blocks"


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
    Finding.CLOSED_META: FixKey.UNBLOCK_META, Finding.CLOSED_HEADER: FixKey.UNBLOCK_HEADER,
    Finding.CLOSED_ROBOTS: FixKey.UNBLOCK_ROBOTS, Finding.ROBOTS_UNREACHABLE: FixKey.REPAIR_ROBOTS,
    Finding.ROBOTS_ERRORS: FixKey.FIX_ROBOTS_ERRORS, Finding.NO_TITLE: FixKey.ADD_TITLE,
    Finding.NO_DESCRIPTION: FixKey.ADD_DESCRIPTION, Finding.CANONICAL_FOREIGN: FixKey.OWN_CANONICAL,
    Finding.NO_PREVIEW_IMAGE: FixKey.ADD_PREVIEW_IMAGE, Finding.PREVIEW_IMAGE_BROKEN: FixKey.REPLACE_PREVIEW_IMAGE,
    Finding.PREVIEW_IMAGE_SVG: FixKey.RASTER_PREVIEW_IMAGE,
    Finding.PREVIEW_IMAGE_RELATIVE: FixKey.FULL_PREVIEW_IMAGE_URL,
    Finding.NO_PREVIEW_TITLE: FixKey.ADD_PREVIEW_TITLE, Finding.LOW_CONTRAST: FixKey.RAISE_CONTRAST,
    Finding.NO_ALT: FixKey.ADD_ALT, Finding.NO_LANG: FixKey.SET_LANG,
    Finding.NO_CONTACTS: FixKey.ADD_CONTACTS, Finding.PHONE_NOT_LINK: FixKey.LINK_PHONE,
    Finding.CALL_WITHOUT_CODE: FixKey.FULL_CALL_NUMBER, Finding.NO_PRIVACY_POLICY: FixKey.ADD_POLICY_LINK,
    Finding.NO_ANALYTICS: FixKey.ADD_COUNTER,
}
SLOW_FIX_KEYS = {Cause.IMAGES: FixKey.COMPRESS_IMAGES, Cause.SCRIPTS: FixKey.TRIM_SCRIPTS,
                 Cause.SERVER: FixKey.FIX_SERVER, Cause.UNKNOWN: FixKey.FIND_SLOWDOWN}


def judge(page: PageFacts | None, security: SecurityFacts, today: date,
          preview: PagePreview | None = None) -> Verdict:
    blocks = {
        Block.SPEED: _speed_block(page, security),
        Block.MOBILE: _mobile_block(page, security),
        Block.SECURITY: _security_block(page, security, today),
        Block.IMAGES: _images_block(page, security),
        Block.CONTACTS: judge_contacts(preview, page.service_markers if page else frozenset()),
        Block.SEARCH: judge_search(page.search if page else None, preview),
        Block.PREVIEW: judge_preview(preview),
        Block.READABILITY: judge_readability(page.readability if page else None),
    }
    return Verdict(blocks, _summary_kind(blocks, security), pick_troubles(blocks), pick_fixes(blocks))


def _unknown(block: Block, security: SecurityFacts) -> BlockVerdict:
    reason = UnknownReason.CERT_BLOCKS if security.cert_blocks else UnknownReason.NO_DATA
    return BlockVerdict(block, Grade.UNKNOWN, unknown_reason=reason)


def _speed_block(page: PageFacts | None, security: SecurityFacts) -> BlockVerdict:
    if page is None or page.speed.lcp_ms is None:
        return _unknown(Block.SPEED, security)
    lcp = page.speed.lcp_ms
    if lcp <= thresholds.LCP_GOOD_MS:
        return BlockVerdict(Block.SPEED, Grade.GOOD)
    grade = Grade.FIX if lcp <= thresholds.LCP_FIX_MS else Grade.BAD
    item = FindingItem(Finding.SLOW, grade, cause=main_cause(page.speed), server_ms=page.speed.server_ms)
    return graded(Block.SPEED, [item])


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
    return graded(Block.MOBILE, findings)


def _security_block(page: PageFacts | None, security: SecurityFacts, today: date) -> BlockVerdict:
    known = [facts for facts in security.tls if facts.outcome not in TLS_UNKNOWN]
    if security.cert_blocks:
        return graded(Block.SECURITY, [_blocking_cert_finding(known)])
    if not known:
        return BlockVerdict(Block.SECURITY, Grade.UNKNOWN, unknown_reason=UnknownReason.OWN_CHECKS_FAILED)
    findings = [item for facts in known for item in _tls_findings(facts, page, today)]
    findings += _transport_findings(security, known, page)
    return graded(Block.SECURITY, _unique(findings))


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
    переводит на защищённую версию на другом хосте, — «стоит поправить», а не «плохо» (решение владельца).

    Хост итогового адреса переводится в ASCII (IDNA) той же функцией, что и в pipeline.py: `facts.host` уже в
    ASCII, а `final_url` от Lighthouse может прийти юникодом — иначе один и тот же хост выглядел бы «разным».
    Хост не перевёлся — считаем его неизвестным и не другим: остаётся «плохо».
    """
    if facts.outcome is not TlsOutcome.NO_HTTPS:
        return False
    if page is None or not page.final_url.startswith(HTTPS_PREFIX):
        return True
    final_host = to_ascii_host(urlsplit(page.final_url).hostname or "")
    return final_host is None or final_host == facts.host


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
    return graded(Block.IMAGES, [FindingItem(Finding.HEAVY_PAGE, grade)])


def _summary_kind(blocks: dict[Block, BlockVerdict], security: SecurityFacts) -> SummaryKind:
    """«Не удалось проверить» в итоге — только у прежних четырёх блоков (ТЗ, 6.2)."""
    if security.cert_blocks:
        return SummaryKind.CERT_BLOCKS
    grades = {verdict.grade for verdict in blocks.values()}
    if Grade.BAD in grades:
        return SummaryKind.HAS_BAD
    if Grade.FIX in grades:
        return SummaryKind.ONLY_FIX
    core_unknown = any(blocks[block].grade is Grade.UNKNOWN for block in CORE_BLOCKS)
    return SummaryKind.GOOD_WITH_UNKNOWN if core_unknown else SummaryKind.ALL_GOOD


def pick_troubles(blocks: dict[Block, BlockVerdict]) -> tuple[FindingItem, ...]:
    """Главная находка каждого проблемного блока: сперва «плохо», потом «стоит поправить» (ТЗ, 6.2–6.3)."""
    bad = [blocks[block] for block in BAD_PRIORITY if blocks[block].grade is Grade.BAD]
    fix = [blocks[block] for block in FIX_PRIORITY if blocks[block].grade is Grade.FIX]
    return tuple(_last_resort_last(verdict.findings[0] for verdict in bad + fix))[:MAX_TROUBLES]


def pick_fixes(blocks: dict[Block, BlockVerdict]) -> tuple[FixItem, ...]:
    ordered = _last_resort_last(_by_grade(blocks, BAD_PRIORITY, Grade.BAD) + _by_grade(blocks, FIX_PRIORITY, Grade.FIX))
    fixes: list[FixItem] = []
    for item in ordered:
        key = fix_key(item)
        if key not in {fix.key for fix in fixes}:
            fixes.append(FixItem(key, item))
    return tuple(fixes[:MAX_FIXES])


def _last_resort_last(items) -> list[FindingItem]:
    """«Нет счётчика» — после всех остальных находок (ТЗ, 6.3); порядок прочих не меняется (сортировка устойчива)."""
    return sorted(items, key=lambda item: item.finding in LAST_RESORT)


def _by_grade(blocks: dict[Block, BlockVerdict], order: tuple[Block, ...], grade: Grade) -> list[FindingItem]:
    return [item for block in order for item in blocks[block].findings if item.grade is grade]


def fix_key(item: FindingItem) -> FixKey:
    """Медленно из-за картинок и тяжёлые картинки — один пункт (ТЗ, 6.5)."""
    return SLOW_FIX_KEYS[item.cause] if item.finding is Finding.SLOW else FIX_KEYS[item.finding]
