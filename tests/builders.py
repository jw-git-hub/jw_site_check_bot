"""Сборщики входных данных для тестов: ответ Lighthouse, замеры, факты защиты."""
from datetime import UTC, date, datetime, timedelta

from bot.site_check.lighthouse import AuditState, FileWeight, ImageFacts, MobileFacts, PageFacts, PostNumbers, SpeedFacts
from bot.site_check.pagespeed import AUDIT_IDS
from bot.site_check.tls_check import CertInfo, RedirectState, TlsFacts, TlsOutcome
from bot.site_check.verdict import SecurityFacts

TODAY = date(2026, 9, 25)
MB = 1024 * 1024
VIEWPORT_OK = '<meta name="viewport" content="width=device-width,initial-scale=1">'
JW_DEV_PRO_HEAVIEST = (("00-oblozhka.webp", 112_654),)


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


def images(page_bytes=265_789, image_bytes=176_996, heaviest=JW_DEV_PRO_HEAVIEST, ratio=None) -> ImageFacts:
    files = tuple(FileWeight(name, "Image", size, 0) for name, size in heaviest)
    return ImageFacts(page_bytes, image_bytes, files, ratio)


def page(speed_facts=None, mobile_facts=None, image_facts=None, final_url="https://site.test/") -> PageFacts:
    post = PostNumbers(14, (("total", 265_789),), (), ())
    return PageFacts("13.5.0", final_url, speed_facts or speed(), mobile_facts or mobile(), image_facts or images(),
                     (), post, ())


def cert(days_left=74, lifetime=90) -> CertInfo:
    """days_left — от TODAY: 74 дня — это 8 декабря 2026, как у jw-dev.pro."""
    ends = datetime(2026, 9, 25, 12, tzinfo=UTC) + timedelta(days=days_left)
    return CertInfo(ends - timedelta(days=lifetime), ends, "Let's Encrypt", ("site.test",))


def security(outcome=TlsOutcome.OK, days_left=74, lifetime=90, redirects=(RedirectState.REDIRECTS,), insecure=(),
             cert_blocks=False) -> SecurityFacts:
    return SecurityFacts((TlsFacts("site.test", outcome, cert(days_left, lifetime)),), redirects, insecure, cert_blocks)
