"""Сборщики входных данных для тестов: ответ Lighthouse, замеры, факты защиты."""
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from bot.site_check.head_tags import HeadTags
from bot.site_check.lighthouse import AuditState, FileWeight, ImageFacts, MobileFacts, PageFacts, PostNumbers, SpeedFacts
from bot.site_check.page_contacts import ContactFacts
from bot.site_check.page_fetch import ImageCheck, ImageState, PagePreview
from bot.site_check.pagespeed import AUDIT_IDS
from bot.site_check.readability_block import ReadabilityFacts
from bot.site_check.search_block import BlockSource, SearchFacts
from bot.site_check.tls_check import CertInfo, RedirectState, TlsFacts, TlsOutcome
from bot.site_check.verdict import SecurityFacts

TODAY = date(2026, 9, 25)
MB = 1024 * 1024
TINY_JPEG = b"\xff\xd8\xff\xe0" + bytes(16) + b"\xff\xd9"
VIEWPORT_OK = '<meta name="viewport" content="width=device-width,initial-scale=1">'
NOINDEX_META = '<meta name="robots" content="noindex" />'
JW_DEV_PRO_HEAVIEST = (("00-oblozhka.webp", 112_654),)
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "pagespeed"
# Записанные ответы PageSpeed (tests/fixtures/pagespeed), где страницу не измерить, и ожидаемый исход.
# Просроченный сертификат и несуществующий домен PageSpeed называет одинаково — FAILED_DOCUMENT_REQUEST:
# различают их свои проверки бота до замера.
RECORDED_FAILURES = {"not_found": "not_found", "blocked": "blocked", "cert": "unreachable", "no_domain": "unreachable"}


def recorded_lighthouse(name: str) -> dict:
    """Записанный ответ PageSpeed (задача 25) — lighthouseResult."""
    recorded = json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return recorded["response"]["lighthouseResult"]


def readability(contrast=AuditState.PASSED, examples=(), alt=AuditState.PASSED, alt_names=(), alt_count=None,
                lang=AuditState.PASSED) -> ReadabilityFacts:
    count = len(alt_names) if alt_count is None else alt_count
    return ReadabilityFacts(contrast, tuple(examples), len(examples), alt, tuple(alt_names), count, 0, lang, 0, 0)


def search(crawlable=AuditState.PASSED, source=None, robots=AuditState.PASSED, robots_status=None, robots_errors=(),
           title=AuditState.PASSED, description=AuditState.PASSED) -> SearchFacts:
    snippet = NOINDEX_META if source is BlockSource.META else ""
    return SearchFacts(crawlable, source, snippet, robots, robots_status, tuple(robots_errors), title, description)


def head(title="Сайт", description="Описание сайта", og_title=None, og_image="https://site.test/og.jpg",
         canonical=None, lang="ru", complete=True) -> HeadTags:
    return HeadTags(title=title, description=description, og_title=og_title, og_image=og_image, canonical=canonical,
                    lang=lang, complete=complete)


def contacts(call_links=1, short=(), text_phones=0, tap_blocked=False, whatsapp=False, telegram=False, email=False,
             forms=0, policy=True, markers=frozenset({"metrika"}), complete=True) -> ContactFacts:
    return ContactFacts(call_links, tuple(short), text_phones, tap_blocked, whatsapp, telegram, False, email, forms,
                        policy, frozenset(markers), complete)


def preview(page_head=None, image_state=ImageState.OK, image_status=200, failure=None, status=200,
            url="https://site.test/", contact_facts=None) -> PagePreview:
    """Своя загрузка страницы: по умолчанию удалась, head с заголовком, описанием и картинкой 70 КБ."""
    if failure is not None:
        return PagePreview(url, None, failure, status, 0, 0.0, None)
    tags = page_head or head()
    image = ImageCheck("og.jpg", image_state, image_status, "image/jpeg", 70_415) if tags.preview_image else None
    return PagePreview(url, tags, None, status, 6_102, 400.0, image, True, contact_facts)


def audit(score=1, mode="numeric", value=None, lcp_savings=None, items=None) -> dict:
    result = {"score": score, "scoreDisplayMode": mode}
    if value is not None:
        result["numericValue"] = value
    if lcp_savings is not None:
        result["metricSavings"] = {"LCP": lcp_savings}
    if items is not None:
        result["details"] = {"type": "table", "items": items}
    return result


def lighthouse(final_url: str = "https://site.test/", **overrides) -> dict:
    """Ответ, где все проверки пройдены; overrides — по именам проверок с _ вместо -."""
    audits = {name: audit() for name in AUDIT_IDS}
    audits.update({name.replace("_", "-"): value for name, value in overrides.items()})
    return {"lighthouseVersion": "13.5.0", "finalDisplayedUrl": final_url, "audits": audits}


def speed(lcp=1400.0, image=0.0, script=0.0, server=0.0, tbt=0.0, server_ms=50.0) -> SpeedFacts:
    return SpeedFacts(lcp_ms=lcp, fcp_ms=1100.0, tbt_ms=tbt, cls=0.0, speed_index_ms=2400.0, server_ms=server_ms,
                      image_savings_ms=image, script_savings_ms=script, server_savings_ms=server)


def mobile(viewport=AuditState.PASSED, snippet=VIEWPORT_OK, target=AuditState.PASSED,
           zoom=AuditState.PASSED) -> MobileFacts:
    return MobileFacts(viewport, snippet, target, zoom)


def images(page_bytes=265_789, image_bytes=176_996, heaviest=JW_DEV_PRO_HEAVIEST, ratio=None, stretched=(),
          blurry=()) -> ImageFacts:
    files = tuple(FileWeight(name, "Image", size, 0) for name, size in heaviest)
    return ImageFacts(page_bytes, image_bytes, files, ratio, tuple(stretched), tuple(blurry), 0)


def page(speed_facts=None, mobile_facts=None, image_facts=None, final_url="https://site.test/",
         readability_facts=None, search_facts=None, screenshot=None) -> PageFacts:
    post = PostNumbers(14, (("total", 265_789),), (), ())
    return PageFacts("13.5.0", final_url, speed_facts or speed(), mobile_facts or mobile(), image_facts or images(),
                     (), post, (), search=search_facts, readability=readability_facts, screenshot=screenshot)


def cert(days_left=74, lifetime=90) -> CertInfo:
    """days_left — от TODAY: 74 дня — это 8 декабря 2026, как у jw-dev.pro."""
    ends = datetime(2026, 9, 25, 12, tzinfo=UTC) + timedelta(days=days_left)
    return CertInfo(ends - timedelta(days=lifetime), ends, "Let's Encrypt", ("site.test",))


def security(outcome=TlsOutcome.OK, days_left=74, lifetime=90, redirects=(RedirectState.REDIRECTS,), insecure=(),
             cert_blocks=False, domain=None) -> SecurityFacts:
    return SecurityFacts((TlsFacts("site.test", outcome, cert(days_left, lifetime)),), redirects, insecure,
                         cert_blocks, domain)
