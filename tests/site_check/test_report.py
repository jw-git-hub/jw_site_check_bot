from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import unquote

from bot.brand import BRAND
from bot.locales import TEXTS
from bot.site_check.lighthouse import AuditState
from bot.site_check.post_numbers import post_numbers
from bot.site_check.report import ReportRequest, build_report
from bot.site_check.tls_check import CertInfo, RedirectState, TlsFacts, TlsOutcome
from bot.site_check.verdict import SecurityFacts, judge
from tests.builders import MB, TODAY, cert, images, mobile, page, security, speed
from tests.fakes import rich_text

MEASURED_AT = datetime(2026, 9, 25, 5, 30, tzinfo=UTC)
EXAMPLE_HEAVIEST = (("slider-1.jpg", 3_355_443), ("about.png", 2_202_010), ("team.jpg", 1_887_437))

# Утверждённый владельцем вид (задача 23a, живая приёмка): заголовки блоков + короткие строки «>».
JW_DEV_PRO_RU = """>jw ~/проверка-сайта_
jw-dev.pro
Сайт в порядке: открывается быстро, на телефоне удобен, защита работает.
Скорость — хорошо
> главное на экране — через 1,4 секунды
Телефон — хорошо
> мобильная версия есть
> кнопки стоят свободно
Защита — хорошо
> сертификат действует до 8 декабря 2026
> адрес без https переводит на защищённый
Картинки — хорошо
> страница весит 260 КБ
> самая тяжёлая — 00-oblozhka.webp, 110 КБ
Что поправить в первую очередь
Срочного нет.
────
jw-dev.pro · @jw_dev_pro"""

EXAMPLE_RU = """>jw ~/проверка-сайта_
example.com
Есть что чинить: с телефона открывается медленно, и страница слишком тяжёлая.
Скорость — плохо
> главное на экране — через 7 секунд
> больше всего времени уходит на тяжёлые картинки
Телефон — стоит поправить
> мобильная версия есть
> кнопки и ссылки стоят тесно: пальцем легко попасть не туда
Защита — стоит поправить
> сертификат действует до 14 марта 2027
> по ссылке без https часть браузеров откроет сайт без защиты, с пометкой «Не защищено»
Картинки — плохо
> страница весит 12 МБ, из них 10 МБ — картинки
> самые тяжёлые: slider-1.jpg — 3,2 МБ, about.png — 2,1 МБ, team.jpg — 1,8 МБ
> картинки можно ужать примерно в 6 раз почти без потери качества
Что поправить в первую очередь
> Ужать картинки — страница станет легче и быстрее откроется с телефона.
> Включить переадресацию на https — чтобы по любой ссылке сайт открывался защищённым.
> Раздвинуть кнопки и ссылки в мобильной версии.
────
jw-dev.pro · @jw_dev_pro"""

EXAMPLE_EN = """>jw ~/site-check_
example.com
Needs fixing: it loads slowly on phones, and the page is too heavy.
Speed — poor
> the main content appears after 7 seconds
> most of that time goes to heavy images
Phone — worth fixing
> there is a mobile version
> buttons and links sit too close together: it's easy to tap the wrong one
Security — worth fixing
> the certificate is valid until 14 March 2027
> links without https open the site unprotected in some browsers, marked "Not secure"
Images — poor
> the page weighs 12 MB, 10 MB of it images
> heaviest: slider-1.jpg — 3.2 MB, about.png — 2.1 MB, team.jpg — 1.8 MB
> the images can be compressed about 6 times with almost no loss in quality
Fix first
> Compress the images — the page gets lighter and opens faster on phones.
> Turn on the redirect to https — so the site opens secure from any link.
> Give buttons and links more room in the mobile version.
────
jw-dev.pro · @jw_dev_pro"""

# Отчёт при сертификате, который блокирует браузер (задача 23a): блоки без данных из-за той же причины
# собираются под один заголовок, идущий после оценённых блоков.
EXPIRED_BADSSL_RU = """>jw ~/проверка-сайта_
expired.badssl.com
Браузер не пускает на сайт: сертификат истёк.
Защита — плохо
> сертификат истёк 12 апреля 2015
> браузер показывает предупреждение во весь экран, сайт открывается только через «Дополнительно»
Скорость, телефон, картинки — не удалось проверить
> браузер не открывает сайт из-за сертификата
Что поправить в первую очередь
> Заменить сертификат — сейчас браузер пугает посетителей предупреждением.
────
jw-dev.pro · @jw_dev_pro"""


def example_page():
    return page(speed(lcp=7000, image=3000), mobile(target=AuditState.FAILED),
                images(page_bytes=12 * MB, image_bytes=10 * MB, heaviest=EXAMPLE_HEAVIEST, ratio=6))


def example_security():
    return security(days_left=170, redirects=(RedirectState.NO_REDIRECT,))


def build(lang, request_page, request_security, display="example.com", is_admin=False) -> tuple[dict, dict]:
    verdict = judge(request_page, request_security, TODAY)
    request = ReportRequest(display, display.split("/")[0], verdict, request_page, request_security, is_admin,
                            MEASURED_AT)
    return build_report(TEXTS, lang, BRAND, request)


def report(lang, request_page, request_security, display="example.com", is_admin=False) -> dict:
    return build(lang, request_page, request_security, display, is_admin)[0]


def test_jw_dev_pro_report_matches_spec():
    assert rich_text(report("ru", page(), security(), display="jw-dev.pro")) == JW_DEV_PRO_RU


def test_example_report_matches_spec_in_russian():
    assert rich_text(report("ru", example_page(), example_security())) == EXAMPLE_RU


def test_example_report_matches_spec_in_english():
    assert rich_text(report("en", example_page(), example_security())) == EXAMPLE_EN


def test_certificate_blocking_report_matches_spec():
    expired_2015 = CertInfo(datetime(2013, 4, 12, tzinfo=UTC), datetime(2015, 4, 12, tzinfo=UTC), "Let's Encrypt",
                            ("expired.badssl.com",))
    facts = SecurityFacts((TlsFacts("expired.badssl.com", TlsOutcome.EXPIRED, expired_2015),),
                          (RedirectState.REDIRECTS,), (), cert_blocks=True)
    text = rich_text(report("ru", None, facts, display="expired.badssl.com"))
    assert text == EXPIRED_BADSSL_RU


def test_discuss_button_opens_dm_with_domain_and_another_is_callback():
    _, keyboard = build("ru", page(), security(), display="пример.рф/uslugi")
    buttons = keyboard["inline_keyboard"]
    discuss = buttons[0][0]
    assert unquote(discuss["url"].split("=", 1)[1]) == "Пришёл из проверки сайта: пример.рф"
    assert discuss["style"] == "primary"
    assert buttons[1][0]["callback_data"] == "again"
    assert buttons[2][0]["text"] == "Канал"


def test_owner_gets_numbers_for_post_and_others_do_not():
    owner = rich_text(report("ru", page(), security(), is_admin=True))
    assert "Цифры для поста" in owner
    assert "Главное на экране (LCP) | 1,4 секунды" in owner
    assert "Замер (UTC+7) | 25.09.2026 12:30" in owner
    assert "Цифры для поста" not in rich_text(report("ru", page(), security()))


def test_unknown_security_is_named_in_summary():
    failed = SecurityFacts((TlsFacts("site.test", TlsOutcome.CONNECT_FAILED),), (RedirectState.CLOSED,), ())
    text = rich_text(report("ru", page(), failed))
    assert "Всё, что удалось проверить, в порядке. Не удалось проверить защиту." in text
    assert "Защита — не удалось проверить\n> сайт не ответил на мои запросы" in text


def test_expiring_certificate_sentence_and_fix():
    text = rich_text(report("ru", page(), security(days_left=6)))
    assert ("> сертификат действует до 1 октября 2026\n"
            "> сертификат закончится через 6 дней — если он не продлится сам, браузер начнёт показывать "
            "предупреждение") in text
    assert "> Проверить автопродление сертификата до 1 октября 2026." in text


def test_missing_server_response_time_uses_plain_texts():
    """server_ms может быть None (лидирует document-latency-insight, а server-response-time пропал)."""
    facts = page(speed(lcp=7000.0, server=2000.0, server_ms=None))
    text = rich_text(report("ru", facts, security()))
    assert "> больше всего времени уходит на ответ сервера" in text
    assert "> Разобраться с сервером или хостингом — он долго думает, прежде чем отдать страницу." in text
    assert "0 секунд" not in text


def test_fast_server_response_uses_plain_texts_even_when_named_the_cause():
    """server_savings_ms (document-latency-insight) включает переадресации и сжатие, поэтому причина «сервер»
    может выбраться и когда сам server_ms маленький — тогда тоже без выдуманных секунд."""
    facts = page(speed(lcp=7000.0, server=2000.0, server_ms=40.0))
    text = rich_text(report("ru", facts, security()))
    assert "> больше всего времени уходит на ответ сервера" in text
    assert "> Разобраться с сервером или хостингом — он долго думает, прежде чем отдать страницу." in text
    assert "0 секунд" not in text


def test_expired_certificate_with_a_future_leaf_date_uses_no_date_text():
    """Код ошибки 10 (истёк) может относиться к промежуточному сертификату цепочки — read_cert_unverified читает
    лист, и его срок ещё не кончился. Дата листа тут ни при чём: с ней текст выглядел бы как «истёк, 25 октября
    2026», хотя эта дата ещё не наступила."""
    facts = SecurityFacts((TlsFacts("site.test", TlsOutcome.EXPIRED, cert(days_left=30)),),
                          (RedirectState.REDIRECTS,), ())
    text = rich_text(report("ru", page(), facts))
    assert "> сертификат истёк\n> браузер показывает предупреждение во весь экран" in text
    assert "октября" not in text


def test_expired_certificate_without_parseable_data_uses_no_date_text():
    """read_cert_unverified может не разобрать сертификат и отдать cert=None — тогда без пустой даты в строке."""
    failed_cert = SecurityFacts((TlsFacts("site.test", TlsOutcome.EXPIRED, None),), (RedirectState.REDIRECTS,), ())
    text_ru = rich_text(report("ru", page(), failed_cert))
    assert "> сертификат истёк\n> браузер показывает предупреждение во весь экран" in text_ru
    text_en = rich_text(report("en", page(), failed_cert))
    assert "> the certificate has expired\n> the browser shows a full-screen warning" in text_en


def test_security_bad_certificate_still_lists_other_consequences():
    """Сертификат «плохо» не должен молча прятать остальные находки блока (общее правило — у каждой находки
    должно быть последствие для посетителя), каждая — своей строкой «>»."""
    facts = SecurityFacts((TlsFacts("site.test", TlsOutcome.WRONG_HOST, cert()),), (RedirectState.REDIRECTS,),
                          ("http://x/a.js",))
    text = rich_text(report("ru", page(), facts))
    assert "> сертификат выдан на другой адрес" in text
    assert "> часть файлов страницы грузится без защиты" in text


def two_host_security(first_outcome, second_outcome, first_cert=True, second_cert=True) -> SecurityFacts:
    """Присланный хост и итоговый (после переадресации) проверяются отдельно — у обоих может найтись «плохо»."""
    return SecurityFacts((TlsFacts("site.test", first_outcome, cert() if first_cert else None),
                          TlsFacts("www.site.test", second_outcome, cert() if second_cert else None)),
                         (RedirectState.REDIRECTS, RedirectState.REDIRECTS), ())


def security_fact_lines(text: str, lang: str) -> list[str]:
    """Строки «>» блока «Защита» — между его заголовком и следующим («Картинки»/«Images»)."""
    security_title, next_title = ("Защита", "Картинки") if lang == "ru" else ("Security", "Images")
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(security_title))
    end = next(i for i, line in enumerate(lines) if i > start and line.startswith(next_title))
    return [line for line in lines[start:end] if line.startswith("> ")]


def test_second_bad_security_finding_does_not_crash_or_get_a_tail():
    """tail_* заведён не для каждой находки «плохо» (нет tail_cert_untrusted, tail_cert_invalid, tail_no_https) —
    вторая такая находка (второй хост) должна молчать, а не ронять сборку отчёта KeyError'ом: у блока «Защита»
    остаются ровно две строки «>» (находка и её последствие), без третьей от второго хоста."""
    wrong_host_then_untrusted = two_host_security(TlsOutcome.WRONG_HOST, TlsOutcome.OTHER)
    no_https_then_expired = two_host_security(TlsOutcome.NO_HTTPS, TlsOutcome.EXPIRED, first_cert=False)
    for lang in ("ru", "en"):
        for facts in (wrong_host_then_untrusted, no_https_then_expired):
            text = rich_text(report(lang, page(), facts))  # сборка не должна упасть KeyError'ом
            assert len(security_fact_lines(text, lang)) == 2


def test_wrong_host_and_incomplete_chain_state_both_consequences():
    """INCOMPLETE_CHAIN не должен теряться в ветке «плохо» — у него, в отличие от других находок «стоит
    поправить» в этом сценарии, есть свой tail_incomplete_chain."""
    facts = two_host_security(TlsOutcome.WRONG_HOST, TlsOutcome.INCOMPLETE_CHAIN)
    text_ru = rich_text(report("ru", page(), facts))
    assert "> сертификат выдан на другой адрес" in text_ru
    assert "> сервер отдаёт сертификат не полностью, и в части браузеров и приложений сайт покажется небезопасным" \
        in text_ru
    text_en = rich_text(report("en", page(), facts))
    assert "> the certificate is issued for another address" in text_en
    assert "> the server sends the certificate incompletely, so some browsers and apps will show the site as " \
        "unsafe" in text_en


def test_no_https_with_secure_redirect_elsewhere_report():
    """Голый домен без https, переадресация на защищённую версию на другом хосте — «стоит поправить», не «плохо»;
    дата сертификата берётся у итогового хоста."""
    facts = two_host_security(TlsOutcome.NO_HTTPS, TlsOutcome.OK, first_cert=False)
    result_page = page(final_url="https://www.site.test/")
    text_ru = rich_text(report("ru", result_page, facts))
    assert "Защита — стоит поправить" in text_ru
    assert "> сертификат действует до 8 декабря 2026" in text_ru
    assert "> ссылка с https на присланный адрес не откроется — браузер покажет ошибку" in text_ru
    assert "> Подключить сертификат и на этот адрес — тогда сайт откроется по любой ссылке." in text_ru
    assert "работает без защиты" not in text_ru
    text_en = rich_text(report("en", result_page, facts))
    assert "Security — worth fixing" in text_en
    assert "> the certificate is valid until 8 December 2026" in text_en
    assert "> a https link to this exact address won't open — the browser shows an error" in text_en
    assert "> Add a certificate for this address too — then any link to the site will open." in text_en
    assert "works without a secure connection" not in text_en


def test_post_numbers_shows_redirect_chain_when_addresses_differ():
    """requested_url и итоговый final_url отличаются — показываем цепочку переадресаций (ТЗ, 7.5)."""
    facts = replace(page(), requested_url="http://site.test/", final_url="https://site.test/")
    text = rich_text(post_numbers(TEXTS, "ru", facts, security(), MEASURED_AT))
    assert "Переадресация | http://site.test/ → https://site.test/" in text


def test_post_numbers_hides_redirect_row_when_no_real_redirect():
    """Без переадресации (адреса совпадают или requested_url пуст) строки быть не должно."""
    same = replace(page(), requested_url="https://site.test/", final_url="https://site.test/")
    assert "Переадресация" not in rich_text(post_numbers(TEXTS, "ru", same, security(), MEASURED_AT))
    empty = replace(page(), requested_url="", final_url="https://site.test/")
    assert "Переадресация" not in rich_text(post_numbers(TEXTS, "ru", empty, security(), MEASURED_AT))
