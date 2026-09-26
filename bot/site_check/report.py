"""Отчёт → rich-сообщение (ТЗ, 7.1–7.3). Какие находки — решает verdict.py; здесь только слова и порядок блоков.

Заголовок блока — оценка, под ним факты по одной строке, каждая начинается с «> » (решение владельца после живой
приёмки, задача 23a). Блоки «не удалось проверить» с общей причиной собираются под один заголовок и идут после
оценённых блоков.
"""
from dataclasses import dataclass
from datetime import date, datetime

from bot.core import rich
from bot.core.commands import Brand
from bot.core.i18n import Lang, Texts
from bot.site_check.lighthouse import ImageFacts, PageFacts
from bot.site_check.post_numbers import post_numbers
from bot.site_check.thresholds import COMPRESS_MIN_RATIO, SERVER_ALLOWANCE_MS
from bot.site_check.tls_check import RedirectState, TlsOutcome
from bot.site_check.verdict import (REPORT_ORDER, Block, BlockVerdict, Cause, Finding, FindingItem, FixItem, Grade,
                                    FixKey, SecurityFacts, SummaryKind, UnknownReason, Verdict)

TITLE_SIZE = 1
SECTION_SIZE = 2
FIX_MARKER = "> "
FACTS_JOIN = "\n"
UNKNOWN_NAMES_JOIN = ", "
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


def build_report(texts: Texts, lang: Lang, brand: Brand, request: ReportRequest) -> tuple[dict, dict]:
    blocks = [rich.header(texts.get(lang, "header_section")), rich.heading(request.display, TITLE_SIZE),
              rich.paragraph(summary_text(texts, lang, request.verdict))]
    blocks += _graded_sections(texts, lang, request)
    blocks += _unknown_sections(texts, lang, request.verdict)
    blocks += _fixes_section(texts, lang, request.verdict)
    if request.is_admin and request.page:
        blocks.append(post_numbers(texts, lang, request.page, request.security, request.measured_at))
    message = rich.message([*blocks, rich.divider(), rich.footer()])
    return message, _report_keyboard(texts, lang, brand, request.domain)


def _report_keyboard(texts: Texts, lang: Lang, brand: Brand, domain: str) -> dict:
    dm = rich.dm_link(brand.dm_username, texts.get(lang, "discuss_prefill", domain=domain))
    return rich.keyboard(rich.button_url(texts.get(lang, "discuss_button"), dm, rich.STYLE_PRIMARY),
                         rich.button_callback(texts.get(lang, "another_button"), AGAIN_CALLBACK),
                         rich.button_url(texts.get(lang, "channel_button"), brand.channel_url))


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


def _graded_sections(texts: Texts, lang: Lang, request: ReportRequest) -> list[dict]:
    blocks = []
    for block in REPORT_ORDER:
        verdict = request.verdict.blocks[block]
        if verdict.grade is not Grade.UNKNOWN:
            blocks += _graded_section(texts, lang, request, verdict)
    return blocks


def _graded_section(texts: Texts, lang: Lang, request: ReportRequest, verdict: BlockVerdict) -> list[dict]:
    title = _section_title(texts, lang, verdict)
    facts = block_facts(texts, lang, request, verdict)
    return [rich.heading(title, SECTION_SIZE), _facts_paragraph(facts)]


def _facts_paragraph(facts: list[str]) -> dict:
    return rich.paragraph(FACTS_JOIN.join(FIX_MARKER + fact for fact in facts))


def _unknown_sections(texts: Texts, lang: Lang, verdict: Verdict) -> list[dict]:
    unknown = [verdict.blocks[block] for block in REPORT_ORDER if verdict.blocks[block].grade is Grade.UNKNOWN]
    blocks = []
    for group in _group_by_reason(unknown):
        blocks += _unknown_section(texts, lang, group)
    return blocks


def _group_by_reason(unknown: list[BlockVerdict]) -> list[list[BlockVerdict]]:
    """Блоки «не удалось проверить» с общей причиной — под один заголовок, в порядке первого появления причины."""
    groups: dict[UnknownReason, list[BlockVerdict]] = {}
    for item in unknown:
        groups.setdefault(item.unknown_reason, []).append(item)
    return list(groups.values())


def _unknown_section(texts: Texts, lang: Lang, group: list[BlockVerdict]) -> list[dict]:
    title = f"{_unknown_group_title(texts, lang, group)} — {texts.get(lang, 'grade_unknown')}"
    fact = texts.get(lang, f"unknown_{group[0].unknown_reason}")
    return [rich.heading(title, SECTION_SIZE), _facts_paragraph([fact])]


def _unknown_group_title(texts: Texts, lang: Lang, group: list[BlockVerdict]) -> str:
    """Имена блоков — со строчной после первого (решение владельца)."""
    names = [texts.get(lang, f"block_{item.block}") for item in group]
    return UNKNOWN_NAMES_JOIN.join([names[0], *(_lowered(name) for name in names[1:])])


def _lowered(name: str) -> str:
    return name[0].lower() + name[1:]


def block_facts(texts: Texts, lang: Lang, request: ReportRequest, verdict: BlockVerdict) -> list[str]:
    writers = {Block.SPEED: _speed_facts, Block.MOBILE: _mobile_facts, Block.SECURITY: _security_facts,
               Block.IMAGES: _images_facts}
    return writers[verdict.block](texts, lang, request, verdict)


def _speed_facts(texts: Texts, lang: Lang, request: ReportRequest, verdict: BlockVerdict) -> list[str]:
    facts = [texts.get(lang, "speed_lcp", seconds=texts.seconds(lang, request.page.speed.lcp_ms))]
    if verdict.findings:
        facts += _cause_fact(texts, lang, verdict.findings[0])
    return facts


def _cause_fact(texts: Texts, lang: Lang, item: FindingItem) -> list[str]:
    if item.cause is Cause.UNKNOWN:
        return []
    if item.cause is Cause.SERVER:
        if _server_response_is_fast(item.server_ms):
            return [texts.get(lang, "cause_server_plain")]
        return [texts.get(lang, "cause_server", seconds=texts.seconds(lang, item.server_ms))]
    return [texts.get(lang, f"cause_{item.cause}")]


def _server_response_is_fast(server_ms: float | None) -> bool:
    """server_savings_ms (document-latency-insight) включает переадресации и сжатие, не только ответ сервера.

    main_cause может выбрать причиной «сервер», даже когда сам ответ (server-response-time) быстрый или проверка
    пропала — тогда и в предложении, и в «что поправить» нужен текст без выдуманной цифры секунд.
    """
    return server_ms is None or server_ms <= SERVER_ALLOWANCE_MS


def _mobile_facts(texts: Texts, lang: Lang, request: ReportRequest, verdict: BlockVerdict) -> list[str]:
    tails = _tail_facts(texts, lang, _fix_grade_findings(verdict.findings))
    bad = [item for item in verdict.findings if item.grade is Grade.BAD]
    if bad:
        return [*_mobile_bad_facts(texts, lang, bad[0]), *tails]
    if verdict.grade is Grade.GOOD:
        return [texts.get(lang, "mobile_good"), texts.get(lang, "mobile_good_tap")]
    return [texts.get(lang, "mobile_good"), *tails]


def _mobile_bad_facts(texts: Texts, lang: Lang, item: FindingItem) -> list[str]:
    return [texts.get(lang, f"mobile_{item.finding}"), texts.get(lang, f"mobile_{item.finding}_effect")]


def _fix_grade_findings(findings: tuple[FindingItem, ...] | list[FindingItem]) -> list[FindingItem]:
    """Хвост «> …» — только для находок «стоит поправить».

    У «плохо» есть своя главная строка, а текстов `tail_*` для оценки «плохо» не заведено (нет
    `tail_cert_untrusted` и похожих) — находка «плохо» со второго хоста в хвост поэтому не попадает, иначе
    `_tail_facts` упала бы KeyError'ом на отсутствующем ключе.
    """
    return [item for item in findings if item.grade is Grade.FIX]


def _tail_facts(texts: Texts, lang: Lang, items: list[FindingItem]) -> list[str]:
    return [texts.get(lang, f"tail_{item.finding}", days=texts.count(lang, max(item.days_left or 0, 0), "day"))
            for item in items]


def _security_facts(texts: Texts, lang: Lang, request: ReportRequest, verdict: BlockVerdict) -> list[str]:
    main = verdict.findings[0] if verdict.findings else None
    if main and main.grade is Grade.BAD:
        return _security_bad_facts(texts, lang, main, verdict.findings[1:], request.measured_at.date())
    tails = [item for item in verdict.findings if item.finding is not Finding.INCOMPLETE_CHAIN]
    return [*_security_opening_facts(texts, lang, request, verdict), *_tail_facts(texts, lang, tails)]


def _security_bad_facts(texts: Texts, lang: Lang, main: FindingItem, rest: tuple[FindingItem, ...],
                        today: date) -> list[str]:
    # Сертификат «плохо» не должен молча прятать остальные находки блока — та же строка `tail_*`, что и у
    # остальных блоков, чтобы у каждой находки было своё последствие. INCOMPLETE_CHAIN здесь не исключаем
    # (в отличие от ветки «хорошо/стоит поправить» — там его накрывает открывающая строка
    # `security_incomplete_chain`, здесь открывающей строки нет): у него есть свой `tail_*`.
    facts = _security_bad_main_facts(texts, lang, main, today)
    tails = _tail_facts(texts, lang, _fix_grade_findings(rest))
    return [*facts, *tails]


def _security_bad_main_facts(texts: Texts, lang: Lang, item: FindingItem, today: date) -> list[str]:
    if item.finding is Finding.CERT_INVALID:
        return _cert_invalid_facts(texts, lang, item, today)
    if item.finding is Finding.NO_HTTPS:
        return [texts.get(lang, "security_no_https"), texts.get(lang, "security_no_https_effect")]
    return [texts.get(lang, f"security_{item.finding}")]


def _cert_invalid_facts(texts: Texts, lang: Lang, item: FindingItem, today: date) -> list[str]:
    # read_cert_unverified возвращает cert=None по замыслу, когда второе соединение не удалось — без даты
    # строка о просрочке без даты. Код ошибки 10 (истёк) может относиться к промежуточному сертификату цепочки,
    # а прочитанный сертификат — всегда лист: если его срок ещё не кончился, дата — не про этот код ошибки и
    # показывать её нельзя (иначе «истёк» с датой в будущем).
    no_reliable_date = item.until is None or item.until > today
    effect = texts.get(lang, "security_cert_warning_effect")
    if item.cert_problem is TlsOutcome.EXPIRED and no_reliable_date:
        return [texts.get(lang, "security_cert_expired_no_date"), effect]
    until = texts.date(lang, item.until) if item.until else ""
    return [texts.get(lang, f"security_cert_{item.cert_problem}", date=until), effect]


def _security_opening_facts(texts: Texts, lang: Lang, request: ReportRequest, verdict: BlockVerdict) -> list[str]:
    if any(item.finding is Finding.INCOMPLETE_CHAIN for item in verdict.findings):
        return [texts.get(lang, "security_incomplete_chain"), texts.get(lang, "security_incomplete_chain_effect")]
    until = _cert_until(request.security)
    facts = [texts.get(lang, "security_ok", date=texts.date(lang, until)) if until
             else texts.get(lang, "security_ok_no_date")]
    if verdict.grade is Grade.GOOD and RedirectState.REDIRECTS in request.security.redirects:
        facts.append(texts.get(lang, "security_redirect_ok"))
    return facts


def _cert_until(security: SecurityFacts) -> date | None:
    for facts in reversed(security.tls):  # итоговый хост — последним
        if facts.outcome is TlsOutcome.OK and facts.cert:
            return facts.cert.not_after.date()
    return None


def _images_facts(texts: Texts, lang: Lang, request: ReportRequest, verdict: BlockVerdict) -> list[str]:
    images = request.page.images
    if verdict.grade is not Grade.GOOD:
        return _heavy_facts(texts, lang, images)
    facts = [texts.get(lang, "images_weight", size=texts.size(lang, images.page_bytes))]
    if images.heaviest:
        facts.append(_heaviest_one_fact(texts, lang, images))
    return facts


def _heaviest_one_fact(texts: Texts, lang: Lang, images: ImageFacts) -> str:
    top = images.heaviest[0]
    return texts.get(lang, "images_heaviest_one", name=top.name, size=texts.size(lang, top.bytes))


def _heavy_facts(texts: Texts, lang: Lang, images: ImageFacts) -> list[str]:
    size = texts.size(lang, images.page_bytes)
    facts = [texts.get(lang, "images_weight_split", size=size, images=texts.size(lang, images.image_bytes))
             if images.image_bytes else texts.get(lang, "images_weight", size=size)]
    if len(images.heaviest) == 1:
        facts.append(_heaviest_one_fact(texts, lang, images))
    elif images.heaviest:
        listed = ITEMS_JOIN.join(texts.get(lang, "image_item", name=item.name, size=texts.size(lang, item.bytes))
                                 for item in images.heaviest)
        facts.append(texts.get(lang, "images_heaviest_many", items=listed))
    if images.compress_ratio and images.compress_ratio >= COMPRESS_MIN_RATIO:
        facts.append(texts.get(lang, "images_ratio", times=texts.count(lang, images.compress_ratio, "times")))
    return facts


def _fixes_section(texts: Texts, lang: Lang, verdict: Verdict) -> list[dict]:
    title = rich.heading(texts.get(lang, "fixes_title"), SECTION_SIZE)
    if not verdict.fixes:
        return [title, rich.paragraph(texts.get(lang, "fixes_none"))]
    return [title, *(rich.paragraph(FIX_MARKER + fix_text(texts, lang, fix)) for fix in verdict.fixes)]


def fix_text(texts: Texts, lang: Lang, fix: FixItem) -> str:
    item = fix.source
    if fix.key is FixKey.FIX_SERVER and _server_response_is_fast(item.server_ms):
        return texts.get(lang, "fix_server_plain")
    until = texts.date(lang, item.until) if item.until else ""
    return texts.get(lang, fix.key, date=until, seconds=texts.seconds(lang, item.server_ms or 0))
