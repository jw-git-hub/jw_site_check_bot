"""Сборщики входных данных для тестов: ответ Lighthouse, замеры, факты защиты."""
from bot.site_check.pagespeed import AUDIT_IDS


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
