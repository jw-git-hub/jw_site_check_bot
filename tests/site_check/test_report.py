from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import unquote

from bot.brand import BRAND
from bot.locales import TEXTS
from bot.site_check.lighthouse import AuditState
from bot.site_check.post_numbers import post_numbers
from bot.site_check.report import ReportRequest, build_report
from bot.site_check.tls_check import RedirectState, TlsFacts, TlsOutcome
from bot.site_check.verdict import SecurityFacts, judge
from tests.builders import MB, TODAY, cert, images, mobile, page, security, speed
from tests.fakes import rich_text

MEASURED_AT = datetime(2026, 9, 25, 5, 30, tzinfo=UTC)
EXAMPLE_HEAVIEST = (("slider-1.jpg", 3_355_443), ("about.png", 2_202_010), ("team.jpg", 1_887_437))

JW_DEV_PRO_RU = """>jw_ ~/site-check
jw-dev.pro
Сайт в порядке: открывается быстро, на телефоне удобен, защита работает.
Скорость — хорошо
С телефона главное на странице появляется через 1,4 секунды.
Телефон — хорошо
Мобильная версия есть: страница подстраивается под экран, кнопки стоят свободно.
Защита — хорошо
Соединение защищено, сертификат действует до 8 декабря 2026. Адрес без https сам переводит на защищённый.
Картинки — хорошо
Страница весит 260 КБ. Самая тяжёлая картинка — 00-oblozhka.webp, 110 КБ.
Что поправить в первую очередь
Срочного нет.
[Обсудить с разработчиком]  [Проверить другой сайт]  [Канал]
────
jw-dev.pro · @jw_dev_pro"""

EXAMPLE_RU = """>jw_ ~/site-check
example.com
Есть что чинить: с телефона открывается медленно, и страница слишком тяжёлая.
Скорость — плохо
С телефона главное на странице появляется через 7 секунд. Больше всего времени уходит на тяжёлые картинки.
Телефон — стоит поправить
Мобильная версия есть, но кнопки и ссылки стоят тесно: пальцем легко попасть не туда.
Защита — стоит поправить
Соединение защищено, сертификат действует до 14 марта 2027. Но по ссылке без https часть браузеров откроет сайт \
без защиты, с пометкой «Не защищено».
Картинки — плохо
Страница весит 12 МБ, из них 10 МБ — картинки. Самые тяжёлые: slider-1.jpg — 3,2 МБ, about.png — 2,1 МБ, \
team.jpg — 1,8 МБ. Картинки можно ужать примерно в 6 раз почти без потери качества.
Что поправить в первую очередь
> Ужать картинки — страница станет легче и быстрее откроется с телефона.
> Включить переадресацию на https — чтобы по любой ссылке сайт открывался защищённым.
> Раздвинуть кнопки и ссылки в мобильной версии.
[Обсудить с разработчиком]  [Проверить другой сайт]  [Канал]
────
jw-dev.pro · @jw_dev_pro"""

EXAMPLE_EN = """>jw_ ~/site-check
example.com
Needs fixing: it loads slowly on phones, and the page is too heavy.
Speed — poor
On a phone, the main content appears after 7 seconds. Most of that time goes to heavy images.
Phone — worth fixing
There is a mobile version, but buttons and links sit too close together: it's easy to tap the wrong one.
Security — worth fixing
The connection is secure, and the certificate is valid until 14 March 2027. But links without https open the site \
unprotected in some browsers, marked "Not secure".
Images — poor
The page weighs 12 MB, 10 MB of it images. Heaviest: slider-1.jpg — 3.2 MB, about.png — 2.1 MB, team.jpg — 1.8 MB. \
The images can be compressed about 6 times with almost no loss in quality.
Fix first
> Compress the images — the page gets lighter and opens faster on phones.
> Turn on the redirect to https — so the site opens secure from any link.
> Give buttons and links more room in the mobile version.
[Talk to the developer]  [Check another site]  [Channel]
────
jw-dev.pro · @jw_dev_pro"""


def example_page():
    return page(speed(lcp=7000, image=3000), mobile(target=AuditState.FAILED),
                images(page_bytes=12 * MB, image_bytes=10 * MB, heaviest=EXAMPLE_HEAVIEST, ratio=6))


def example_security():
    return security(days_left=170, redirects=(RedirectState.NO_REDIRECT,))


def report(lang, request_page, request_security, display="example.com", is_admin=False) -> dict:
    verdict = judge(request_page, request_security, TODAY)
    request = ReportRequest(display, display.split("/")[0], verdict, request_page, request_security, is_admin,
                            MEASURED_AT)
    return build_report(TEXTS, lang, BRAND, request)


def test_jw_dev_pro_report_matches_spec():
    assert rich_text(report("ru", page(), security(), display="jw-dev.pro")) == JW_DEV_PRO_RU


def test_example_report_matches_spec_in_russian():
    assert rich_text(report("ru", example_page(), example_security())) == EXAMPLE_RU


def test_example_report_matches_spec_in_english():
    assert rich_text(report("en", example_page(), example_security())) == EXAMPLE_EN


def test_discuss_button_opens_dm_with_domain_and_another_is_callback():
    pills = report("ru", page(), security(), display="пример.рф/uslugi")["blocks"][-3]["text"]
    discuss = pills[0]["button"]
    assert unquote(discuss["url"].split("=", 1)[1]) == "Пришёл из проверки сайта: пример.рф"
    assert discuss["style"] == "primary"
    assert pills[2]["button"]["callback_data"] == "again"


def test_owner_gets_numbers_for_post_and_others_do_not():
    owner = rich_text(report("ru", page(), security(), is_admin=True))
    assert "Цифры для поста" in owner
    assert "Главное на экране (LCP) | 1,4 секунды" in owner
    assert "Замер (UTC+7) | 25.09.2026 12:30" in owner
    assert "Цифры для поста" not in rich_text(report("ru", page(), security()))


def test_certificate_blocking_report():
    text = rich_text(report("ru", None, security(outcome=TlsOutcome.EXPIRED, days_left=-3, cert_blocks=True)))
    assert "Браузер не пускает на сайт: сертификат истёк." in text
    assert "Скорость — не удалось проверить\nНе удалось проверить: браузер не открывает сайт из-за сертификата." in text
    assert "Сертификат истёк 22 сентября 2026: браузер показывает предупреждение во весь экран" in text
    assert "> Заменить сертификат — сейчас браузер пугает посетителей предупреждением." in text


def test_unknown_security_is_named_in_summary():
    failed = SecurityFacts((TlsFacts("site.test", TlsOutcome.CONNECT_FAILED),), (RedirectState.CLOSED,), ())
    text = rich_text(report("ru", page(), failed))
    assert "Всё, что удалось проверить, в порядке. Не удалось проверить защиту." in text
    assert "Защита — не удалось проверить\nНе удалось проверить: сайт не ответил на мои запросы." in text


def test_expiring_certificate_sentence_and_fix():
    text = rich_text(report("ru", page(), security(days_left=6)))
    assert ("Соединение защищено, сертификат действует до 1 октября 2026. Но сертификат закончится через 6 дней"
            in text)
    assert "> Проверить автопродление сертификата до 1 октября 2026." in text


def test_missing_server_response_time_uses_plain_texts():
    """C24: server_ms может быть None (лидирует document-latency-insight, а server-response-time пропал)."""
    facts = page(speed(lcp=7000.0, server=2000.0, server_ms=None))
    text = rich_text(report("ru", facts, security()))
    assert "Больше всего времени уходит на ответ сервера." in text
    assert "> Разобраться с сервером или хостингом — он долго думает, прежде чем отдать страницу." in text
    assert "0 секунд" not in text


def test_fast_server_response_uses_plain_texts_even_when_named_the_cause():
    """Ревью, находка 1: server_savings_ms (document-latency-insight) включает переадресации и сжатие, поэтому
    причина «сервер» может выбраться и когда сам server_ms маленький — тогда тоже без выдуманных секунд."""
    facts = page(speed(lcp=7000.0, server=2000.0, server_ms=40.0))
    text = rich_text(report("ru", facts, security()))
    assert "Больше всего времени уходит на ответ сервера." in text
    assert "> Разобраться с сервером или хостингом — он долго думает, прежде чем отдать страницу." in text
    assert "0 секунд" not in text


def test_expired_certificate_without_parseable_data_uses_no_date_text():
    """Ревью, находка 2: read_cert_unverified может не разобрать сертификат и отдать cert=None — тогда без
    пустой даты в предложении."""
    failed_cert = SecurityFacts((TlsFacts("site.test", TlsOutcome.EXPIRED, None),), (RedirectState.REDIRECTS,), ())
    text_ru = rich_text(report("ru", page(), failed_cert))
    assert "Сертификат истёк: браузер показывает предупреждение во весь экран" in text_ru
    assert "Сертификат истёк :" not in text_ru
    text_en = rich_text(report("en", page(), failed_cert))
    assert "The certificate has expired: the browser shows a full-screen warning" in text_en
    assert "expired on :" not in text_en


def test_security_bad_certificate_still_lists_other_consequences():
    """Ревью, находка 3: сертификат «плохо» не должен молча прятать остальные находки блока (общее правило —
    у каждой находки должно быть последствие для посетителя)."""
    facts = SecurityFacts((TlsFacts("site.test", TlsOutcome.WRONG_HOST, cert()),), (RedirectState.REDIRECTS,),
                          ("http://x/a.js",))
    text = rich_text(report("ru", page(), facts))
    assert "Сертификат выдан на другой адрес" in text
    assert "Кроме того, часть файлов страницы грузится без защиты" in text


def two_host_security(first_outcome, second_outcome, first_cert=True, second_cert=True) -> SecurityFacts:
    """Присланный хост и итоговый (после переадресации) проверяются отдельно — у обоих может найтись «плохо»."""
    return SecurityFacts((TlsFacts("site.test", first_outcome, cert() if first_cert else None),
                          TlsFacts("www.site.test", second_outcome, cert() if second_cert else None)),
                         (RedirectState.REDIRECTS, RedirectState.REDIRECTS), ())


def test_second_bad_security_finding_does_not_crash_or_get_a_tail():
    """Ревью раунд 2, находка 1: tail_* заведён не для каждой находки «плохо» (нет tail_cert_untrusted,
    tail_cert_invalid, tail_no_https) — вторая такая находка (второй хост) должна молчать в хвосте
    «Кроме того, …», а не ронять сборку отчёта KeyError'ом."""
    wrong_host_then_untrusted = two_host_security(TlsOutcome.WRONG_HOST, TlsOutcome.OTHER)
    no_https_then_expired = two_host_security(TlsOutcome.NO_HTTPS, TlsOutcome.EXPIRED, first_cert=False)
    for lang in ("ru", "en"):
        for facts in (wrong_host_then_untrusted, no_https_then_expired):
            text = rich_text(report(lang, page(), facts))
            assert "Кроме того" not in text
            assert "Also," not in text


def test_wrong_host_and_incomplete_chain_state_both_consequences():
    """Ревью раунд 2, находка 2: INCOMPLETE_CHAIN не должен теряться в ветке «плохо» — у него, в отличие от
    других находок «стоит поправить» в этом сценарии, есть свой tail_incomplete_chain."""
    facts = two_host_security(TlsOutcome.WRONG_HOST, TlsOutcome.INCOMPLETE_CHAIN)
    text_ru = rich_text(report("ru", page(), facts))
    assert "Сертификат выдан на другой адрес" in text_ru
    assert "Кроме того, сервер отдаёт сертификат не полностью" in text_ru
    text_en = rich_text(report("en", page(), facts))
    assert "The certificate is issued for another address" in text_en
    assert "Also, the server sends the certificate incompletely" in text_en


def test_post_numbers_shows_redirect_chain_when_addresses_differ():
    """C7: requested_url и итоговый final_url отличаются — показываем цепочку переадресаций."""
    facts = replace(page(), requested_url="http://site.test/", final_url="https://site.test/")
    text = rich_text(post_numbers(TEXTS, "ru", facts, security(), MEASURED_AT))
    assert "Переадресация | http://site.test/ → https://site.test/" in text


def test_post_numbers_hides_redirect_row_when_no_real_redirect():
    """C7: без переадресации (адреса совпадают или requested_url пуст) строки быть не должно."""
    same = replace(page(), requested_url="https://site.test/", final_url="https://site.test/")
    assert "Переадресация" not in rich_text(post_numbers(TEXTS, "ru", same, security(), MEASURED_AT))
    empty = replace(page(), requested_url="", final_url="https://site.test/")
    assert "Переадресация" not in rich_text(post_numbers(TEXTS, "ru", empty, security(), MEASURED_AT))
