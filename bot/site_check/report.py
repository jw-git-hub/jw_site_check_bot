"""Отчёт → rich-сообщение (ТЗ, 7.1–7.3). Какие находки — решает verdict.py; здесь только слова и порядок блоков."""
from dataclasses import dataclass
from datetime import date, datetime

from bot.core import rich
from bot.core.commands import Brand
from bot.core.i18n import Lang, Texts
from bot.site_check.lighthouse import ImageFacts, PageFacts
from bot.site_check.post_numbers import post_numbers
from bot.site_check.thresholds import COMPRESS_MIN_RATIO
from bot.site_check.tls_check import RedirectState, TlsOutcome
from bot.site_check.verdict import (REPORT_ORDER, Block, BlockVerdict, Cause, Finding, FindingItem, FixItem, Grade,
                                    FixKey, SecurityFacts, SummaryKind, Verdict)

TITLE_SIZE = 1
SECTION_SIZE = 2
FIX_MARKER = "> "
AGAIN_CALLBACK = "again"
SENTENCE_GAP = " "
ITEMS_JOIN = ", "


@dataclass(frozen=True)
class ReportRequest:
    display: str  # домен и путь для заголовка
    domain: str   # домен для текста в личку
    verdict: Verdict
    page: PageFacts | None
    security: SecurityFacts
    is_admin: bool
    measured_at: datetime


def build_report(texts: Texts, lang: Lang, brand: Brand, request: ReportRequest) -> dict:
    blocks = [rich.header(brand.section), rich.heading(request.display, TITLE_SIZE),
              rich.paragraph(summary_text(texts, lang, request.verdict))]
    for block in REPORT_ORDER:
        verdict = request.verdict.blocks[block]
        blocks += [rich.heading(_section_title(texts, lang, verdict), SECTION_SIZE),
                   rich.paragraph(block_text(texts, lang, request, verdict))]
    blocks += _fixes_section(texts, lang, request.verdict)
    blocks.append(_buttons(texts, lang, brand, request.domain))
    if request.is_admin and request.page:
        blocks.append(post_numbers(texts, lang, request.page, request.security, request.measured_at))
    return rich.message([*blocks, rich.divider(), rich.footer()])


def _section_title(texts: Texts, lang: Lang, verdict: BlockVerdict) -> str:
    return f"{texts.get(lang, f'block_{verdict.block}')} — {texts.get(lang, f'grade_{verdict.grade}')}"


def summary_text(texts: Texts, lang: Lang, verdict: Verdict) -> str:
    kind = verdict.summary
    if kind is SummaryKind.CERT_BLOCKS:
        return texts.get(lang, "summary_cert_blocks", reason=_cert_reason(texts, lang, verdict.troubles[0]))
    if kind is SummaryKind.GOOD_WITH_UNKNOWN:
        return texts.get(lang, "summary_good_with_unknown", blocks=_unknown_names(texts, lang, verdict))
    if kind is SummaryKind.ALL_GOOD:
        return texts.get(lang, "summary_all_good")
    key = "summary_has_bad" if kind is SummaryKind.HAS_BAD else "summary_only_fix"
    phrases = [_trouble_phrase(texts, lang, item) for item in verdict.troubles]
    return texts.get(lang, key, troubles=texts.get(lang, "and_join").join(phrases))


def _trouble_phrase(texts: Texts, lang: Lang, item: FindingItem) -> str:
    if item.finding is Finding.HEAVY_PAGE:
        return texts.get(lang, f"trouble_heavy_page_{item.grade}")
    return texts.get(lang, f"trouble_{item.finding}")


def _cert_reason(texts: Texts, lang: Lang, item: FindingItem) -> str:
    if item.finding is Finding.CERT_INVALID:
        return texts.get(lang, f"cert_reason_{item.cert_problem}")
    return texts.get(lang, "cert_reason_untrusted")


def _unknown_names(texts: Texts, lang: Lang, verdict: Verdict) -> str:
    names = [texts.get(lang, f"unknown_name_{block}") for block in REPORT_ORDER
             if verdict.blocks[block].grade is Grade.UNKNOWN]
    if len(names) == 1:
        return names[0]
    return ITEMS_JOIN.join(names[:-1]) + texts.get(lang, "and_last") + names[-1]


def block_text(texts: Texts, lang: Lang, request: ReportRequest, verdict: BlockVerdict) -> str:
    if verdict.grade is Grade.UNKNOWN:
        return texts.get(lang, f"unknown_{verdict.unknown_reason}")
    writers = {Block.SPEED: _speed_text, Block.MOBILE: _mobile_text, Block.SECURITY: _security_text,
               Block.IMAGES: _images_text}
    return writers[verdict.block](texts, lang, request, verdict)


def _speed_text(texts: Texts, lang: Lang, request: ReportRequest, verdict: BlockVerdict) -> str:
    sentences = [texts.get(lang, "speed_lcp", seconds=texts.seconds(lang, request.page.speed.lcp_ms))]
    if verdict.findings:
        sentences += _cause_sentence(texts, lang, verdict.findings[0])
    return SENTENCE_GAP.join(sentences)


def _cause_sentence(texts: Texts, lang: Lang, item: FindingItem) -> list[str]:
    if item.cause is Cause.UNKNOWN:
        return []
    if item.cause is Cause.SERVER:
        # C24: server_ms может быть None (лидирует document-latency-insight, а server-response-time пропал) —
        # тогда без цифры, а не «0 секунд».
        if item.server_ms is None:
            return [texts.get(lang, "cause_server_plain")]
        return [texts.get(lang, "cause_server", seconds=texts.seconds(lang, item.server_ms))]
    return [texts.get(lang, f"cause_{item.cause}")]


def _mobile_text(texts: Texts, lang: Lang, request: ReportRequest, verdict: BlockVerdict) -> str:
    if verdict.grade is Grade.GOOD:
        return texts.get(lang, "mobile_good")
    main = [item for item in verdict.findings if item.grade is Grade.BAD]
    tails = _tails(texts, lang, [item for item in verdict.findings if item.grade is Grade.FIX])
    if not main:
        return texts.get(lang, "mobile_but", problems=tails)
    sentences = [texts.get(lang, f"mobile_{main[0].finding}")]
    if tails:
        sentences.append(texts.get(lang, "also", problems=tails))
    return SENTENCE_GAP.join(sentences)


def _tails(texts: Texts, lang: Lang, items: list[FindingItem]) -> str:
    return texts.get(lang, "tails_join").join(
        texts.get(lang, f"tail_{item.finding}", days=texts.count(lang, max(item.days_left or 0, 0), "day"))
        for item in items)


def _security_text(texts: Texts, lang: Lang, request: ReportRequest, verdict: BlockVerdict) -> str:
    main = verdict.findings[0] if verdict.findings else None
    if main and main.grade is Grade.BAD:
        return _security_bad_sentence(texts, lang, main)
    tails = [item for item in verdict.findings if item.finding is not Finding.INCOMPLETE_CHAIN]
    opening = _security_opening(texts, lang, request, verdict)
    if not tails:
        return opening
    return SENTENCE_GAP.join([opening, texts.get(lang, "security_but", problems=_tails(texts, lang, tails))])


def _security_bad_sentence(texts: Texts, lang: Lang, item: FindingItem) -> str:
    if item.finding is Finding.CERT_INVALID:
        until = texts.date(lang, item.until) if item.until else ""
        return texts.get(lang, f"security_cert_{item.cert_problem}", date=until)
    return texts.get(lang, f"security_{item.finding}")


def _security_opening(texts: Texts, lang: Lang, request: ReportRequest, verdict: BlockVerdict) -> str:
    if any(item.finding is Finding.INCOMPLETE_CHAIN for item in verdict.findings):
        return texts.get(lang, "security_incomplete_chain")
    until = _cert_until(request.security)
    sentences = [texts.get(lang, "security_ok", date=texts.date(lang, until)) if until
                 else texts.get(lang, "security_ok_no_date")]
    if verdict.grade is Grade.GOOD and RedirectState.REDIRECTS in request.security.redirects:
        sentences.append(texts.get(lang, "security_redirect_ok"))
    return SENTENCE_GAP.join(sentences)


def _cert_until(security: SecurityFacts) -> date | None:
    for facts in reversed(security.tls):  # итоговый хост — последним
        if facts.outcome is TlsOutcome.OK and facts.cert:
            return facts.cert.not_after.date()
    return None


def _images_text(texts: Texts, lang: Lang, request: ReportRequest, verdict: BlockVerdict) -> str:
    images = request.page.images
    if verdict.grade is not Grade.GOOD:
        return SENTENCE_GAP.join(_heavy_sentences(texts, lang, images))
    sentences = [texts.get(lang, "images_weight", size=texts.size(lang, images.page_bytes))]
    if images.heaviest:
        sentences.append(_heaviest_one(texts, lang, images))
    return SENTENCE_GAP.join(sentences)


def _heaviest_one(texts: Texts, lang: Lang, images: ImageFacts) -> str:
    top = images.heaviest[0]
    return texts.get(lang, "images_heaviest_one", name=top.name, size=texts.size(lang, top.bytes))


def _heavy_sentences(texts: Texts, lang: Lang, images: ImageFacts) -> list[str]:
    size = texts.size(lang, images.page_bytes)
    sentences = [texts.get(lang, "images_weight_split", size=size, images=texts.size(lang, images.image_bytes))
                 if images.image_bytes else texts.get(lang, "images_weight", size=size)]
    if len(images.heaviest) == 1:
        sentences.append(_heaviest_one(texts, lang, images))
    elif images.heaviest:
        listed = ITEMS_JOIN.join(texts.get(lang, "image_item", name=item.name, size=texts.size(lang, item.bytes))
                                 for item in images.heaviest)
        sentences.append(texts.get(lang, "images_heaviest_many", items=listed))
    if images.compress_ratio and images.compress_ratio >= COMPRESS_MIN_RATIO:
        sentences.append(texts.get(lang, "images_ratio", times=texts.count(lang, images.compress_ratio, "times")))
    return sentences


def _fixes_section(texts: Texts, lang: Lang, verdict: Verdict) -> list[dict]:
    title = rich.heading(texts.get(lang, "fixes_title"), SECTION_SIZE)
    if not verdict.fixes:
        return [title, rich.paragraph(texts.get(lang, "fixes_none"))]
    return [title, *(rich.paragraph(FIX_MARKER + fix_text(texts, lang, fix)) for fix in verdict.fixes)]


def fix_text(texts: Texts, lang: Lang, fix: FixItem) -> str:
    item = fix.source
    if fix.key is FixKey.FIX_SERVER and item.server_ms is None:
        # C24: то же самое, что в _cause_sentence — без выдуманного «0 секунд».
        return texts.get(lang, "fix_server_plain")
    until = texts.date(lang, item.until) if item.until else ""
    return texts.get(lang, fix.key, date=until, seconds=texts.seconds(lang, item.server_ms or 0))


def _buttons(texts: Texts, lang: Lang, brand: Brand, domain: str) -> dict:
    dm = rich.dm_link(brand.dm_username, texts.get(lang, "discuss_prefill", domain=domain))
    return rich.pills(rich.pill_url(texts.get(lang, "discuss_button"), dm, rich.STYLE_PRIMARY),
                      rich.pill_callback(texts.get(lang, "another_button"), AGAIN_CALLBACK),
                      rich.pill_url(texts.get(lang, "channel_button"), brand.channel_url))
