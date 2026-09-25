import json
from pathlib import Path

import pytest

from bot.site_check.lighthouse import AuditState, audit_state, file_name, parse_lighthouse, strip_params
from tests.builders import audit, lighthouse

FIXTURES = sorted((Path(__file__).parents[1] / "fixtures" / "pagespeed").glob("*.json"))
FAILURE_FIXTURES = {"not_found", "cert", "no_domain"}
PAGE_FIXTURES = [path for path in FIXTURES if path.stem not in FAILURE_FIXTURES]


@pytest.mark.parametrize(("raw", "state"), [
    (None, AuditState.UNKNOWN),
    ({"score": 1, "scoreDisplayMode": "binary"}, AuditState.PASSED),
    ({"score": 0, "scoreDisplayMode": "binary"}, AuditState.FAILED),
    ({"score": 0.95, "scoreDisplayMode": "numeric"}, AuditState.PASSED),
    ({"score": 0.5, "scoreDisplayMode": "metricSavings"}, AuditState.FAILED),
    ({"score": None, "scoreDisplayMode": "notApplicable"}, AuditState.NOT_APPLICABLE),
    ({"score": None, "scoreDisplayMode": "error"}, AuditState.UNKNOWN),
    ({"score": None, "scoreDisplayMode": "informative"}, AuditState.UNKNOWN),
])
def test_audit_state(raw, state):
    assert audit_state(raw) is state


def test_speed_numbers_and_savings_by_bucket():
    facts = parse_lighthouse(lighthouse(
        largest_contentful_paint=audit(value=7000), total_blocking_time=audit(value=450),
        server_response_time=audit(value=1800), image_delivery_insight=audit(score=0, lcp_savings=2500),
        lcp_discovery_insight=audit(score=0, lcp_savings=300), render_blocking_insight=audit(score=0, lcp_savings=400),
        document_latency_insight=audit(score=0, lcp_savings=900)))
    speed = facts.speed
    assert (speed.lcp_ms, speed.tbt_ms, speed.server_ms) == (7000, 450, 1800)
    assert (speed.image_savings_ms, speed.script_savings_ms, speed.server_savings_ms) == (2800, 400, 900)


def test_viewport_snippet_is_kept():
    items = [{"node": {"snippet": '<meta name="viewport" content="width=1024">'}}]
    facts = parse_lighthouse(lighthouse(viewport_insight=audit(score=0, items=items)))
    assert facts.mobile.viewport is AuditState.FAILED
    assert "width=1024" in facts.mobile.viewport_snippet


def test_missing_audits_are_listed_and_unknown():
    result = lighthouse()
    del result["audits"]["target-size"]
    facts = parse_lighthouse(result)
    assert facts.missing_audits == ("target-size",)
    assert facts.mobile.target_size is AuditState.UNKNOWN


def test_images_heaviest_names_savings_and_ratio():
    requests = [
        {"url": "https://site.test/_astro/00-oblozhka.CU0uyZTT_vHsa5.webp?w=800", "resourceType": "Image",
         "transferSize": 112_654},
        {"url": "https://site.test/app.js", "resourceType": "Script", "transferSize": 500_000},
        {"url": "https://site.test/img/team.jpg", "resourceType": "Image", "transferSize": 50_000},
    ]
    delivery = [{"url": "https://site.test/_astro/00-oblozhka.CU0uyZTT_vHsa5.webp", "totalBytes": 112_220,
                 "wastedBytes": 98_266},
                {"url": "https://site.test/img/team.jpg", "totalBytes": 50_000, "wastedBytes": 10_000}]
    summary = [{"resourceType": "total", "requestCount": 14, "transferSize": 265_789},
               {"resourceType": "image", "requestCount": 6, "transferSize": 176_996}]
    facts = parse_lighthouse(lighthouse(
        network_requests=audit(mode="informative", items=requests),
        image_delivery_insight=audit(score=0, items=delivery),
        resource_summary=audit(mode="informative", items=summary), total_byte_weight=audit(value=265_789)))
    assert [image.name for image in facts.images.heaviest] == ["00-oblozhka.webp", "team.jpg"]
    assert facts.images.heaviest[0].savings_bytes == 98_266
    assert (facts.images.page_bytes, facts.images.image_bytes) == (265_789, 176_996)
    assert facts.images.compress_ratio == 3  # 162 220 / 53 954 = 3,006 → вниз
    assert facts.post.requests == 14
    assert facts.post.heaviest_files[0].name == "app.js"


def test_mixed_content_urls_lose_parameters():
    items = [{"url": "http://cdn.test/pic.jpg?token=abc"}]
    facts = parse_lighthouse(lighthouse(is_on_https=audit(score=0, mode="binary", items=items)))
    assert facts.insecure_urls == ("http://cdn.test/pic.jpg",)


def test_third_parties_names():
    items = [{"entity": {"type": "link", "text": "Google Tag Manager"}, "transferSize": 90_000},
             {"entity": "Yandex Metrica", "transferSize": 50_000}]
    facts = parse_lighthouse(lighthouse(third_parties_insight=audit(items=items)))
    assert facts.post.third_parties == (("Google Tag Manager", 90_000), ("Yandex Metrica", 50_000))


@pytest.mark.parametrize(("url", "name"), [
    ("https://s.test/a/b/photo.jpg", "photo.jpg"),
    ("https://s.test/%D1%84%D0%BE%D1%82%D0%BE.png", "фото.png"),
    ("https://s.test/app.1a2b3c4d.js", "app.js"),
    ("https://s.test/logo.v2.png", "logo.v2.png"),
    ("https://s.test/" + "x" * 60 + ".jpg", "x" * 39 + "…"),
    ("https://s.test/", "s.test"),
])
def test_file_name(url, name):
    assert file_name(url) == name


def test_strip_params():
    assert strip_params("https://a.test/p?x=1#f") == "https://a.test/p"


def test_requested_url_is_parsed_and_stripped():
    # C7: ТЗ 7.5 просит «цепочку переадресаций» — бот сам переадресации не проходит, поэтому
    # владельцу показываем то, что просили измерить, и то, где Lighthouse оказался (final_url).
    result = lighthouse()
    result["requestedUrl"] = "https://site.test/?utm_source=x"
    facts = parse_lighthouse(result)
    assert facts.requested_url == "https://site.test/"


def test_requested_url_missing_gives_empty_string():
    facts = parse_lighthouse(lighthouse())
    assert facts.requested_url == ""


@pytest.mark.skipif(not PAGE_FIXTURES, reason="нет записанных ответов PageSpeed (задача 2)")
@pytest.mark.parametrize("path", PAGE_FIXTURES, ids=lambda path: path.stem)
def test_recorded_answers_parse_completely(path):
    recorded = json.loads(path.read_text(encoding="utf-8"))
    facts = parse_lighthouse(recorded["response"]["lighthouseResult"])
    assert facts.lighthouse_version.startswith("13")
    assert facts.speed.lcp_ms is not None
    assert not facts.missing_audits
