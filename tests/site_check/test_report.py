from dataclasses import replace
from datetime import UTC, date, datetime
from urllib.parse import unquote

import pytest

from bot.brand import BRAND
from bot.locales import TEXTS
from bot.site_check.comparison import Comparison
from bot.site_check.domain_expiry import RDAP, DomainPaid
from bot.site_check.findings import Block, BlockVerdict, Finding, FindingItem, FixItem, FixKey, Grade
from bot.site_check.lighthouse import AuditState
from bot.site_check.page_fetch import FetchFailure, ImageState
from bot.site_check.post_numbers import post_numbers
from bot.site_check.report import ReportRequest, build_report, fix_text, summary_text
from bot.site_check.search_block import BlockSource
from bot.site_check.tls_check import CertInfo, RedirectState, TlsFacts, TlsOutcome
from bot.site_check.verdict import SecurityFacts, SummaryKind, Verdict, judge
from tests.builders import (MB, TINY_JPEG, TODAY, cert, contacts, head, images, mobile, page, preview, readability,
                            security, search, speed)
from tests.fakes import rich_text

MEASURED_AT = datetime(2026, 9, 25, 5, 30, tzinfo=UTC)
EXAMPLE_HEAVIEST = (("slider-1.jpg", 3_355_443), ("about.png", 2_202_010), ("team.jpg", 1_887_437))

# Утверждённый владельцем вид (задача 23a, живая приёмка): заголовки блоков + короткие строки «>».
JW_DEV_PRO_TITLE = "Сайты, боты и автоматизация для малого бизнеса — jw-dev.pro"

# Пример ТЗ 7.3, версия 1.2: восемь блоков, снимок и срок домена, на главной jw-dev.pro стоит noindex
# и нет счётчика посещений (разведка 27.09.2026).
JW_DEV_PRO_RU = f"""[полоса ~/проверка-сайта_ (мигает _)]
jw-dev.pro
[снимок первого экрана]
Есть что чинить: страница закрыта от поисковиков, и нет счётчика посещений.
────
Скорость [хорошо·success]
┃ главное на экране — через 1,4 секунды
────
Телефон [хорошо·success]
┃ мобильная версия есть
┃ кнопки стоят свободно
────
Защита [хорошо·success]
┃ сертификат действует до 8 декабря 2026
┃ адрес без https переводит на защищённый
┃ домен оплачен до 25 августа 2027
────
Картинки [хорошо·success]
┃ страница весит 260 КБ
┃ самая тяжёлая — 00-oblozhka.webp, 110 КБ
────
Заявки и контакты [стоит поправить·primary]
┃ связаться можно: в Telegram, по почте, через форму заявки
┃ счётчика посещений нет — не видно, сколько людей заходит и откуда они пришли
────
Поиск в Google [плохо·danger]
┃ страница закрыта от поисковиков: в коде стоит запрет noindex
┃ Google и Яндекс не покажут её в поиске
────
Ссылка в мессенджерах [хорошо·success]
┃ для превью заданы картинка и название «{JW_DEV_PRO_TITLE}»
────
Удобство чтения [хорошо·success]
┃ текст хорошо виден на фоне
┃ у картинок есть подписи
────
Что поправить в первую очередь
> Снять запрет noindex — иначе страницу не найти в Google и Яндексе.
> Поставить счётчик посещений — станет видно, сколько людей заходит и откуда.
────
jw-dev.pro · @jw_dev_pro"""

AVTOSPASATEL_TITLE = ('Шиномонтаж в Кирове - "АвтоСпасатель". Услуги шиномонтажа, автопомощи в Кирове. '
                      "Ремонт шин и дисков")
AVTOSPASATEL_OG = ("Шиномонтаж в Кирове - АвтоСпасатель. Услуги шиномонтажа, автопомощи в Кирове. Ремонт шин и дисков. "
                   "Автомойка")
AVTOSPASATEL_HEAVIEST = (("road1.png", 116_155), ("gruzovoy-shinomontazh-1.jpg", 94_509), ("datchiki1.jpg", 76_554))

# Пример ТЗ 7.3, версия 1.2: живые данные avtospasatel43.ru, восемь блоков.
AVTOSPASATEL_RU = """[полоса ~/проверка-сайта_ (мигает _)]
avtospasatel43.ru
[снимок первого экрана]
Есть что чинить: с телефона открывается медленно, и страница тяжеловата.
────
Скорость [плохо·danger]
┃ главное на экране — через 14 секунд
┃ больше всего времени уходит на тяжёлые картинки
────
Телефон [хорошо·success]
┃ мобильная версия есть
┃ кнопки стоят свободно
────
Защита [хорошо·success]
┃ сертификат действует до 8 декабря 2026
┃ адрес без https переводит на защищённый
┃ домен оплачен до 16 июня 2027
────
Картинки [стоит поправить·primary]
┃ страница весит 4,7 МБ, из них 1,5 МБ — картинки
┃ самые тяжёлые: road1.png — 113 КБ, gruzovoy-shinomontazh-1.jpg — 92 КБ, datchiki1.jpg — 75 КБ
┃ картинки можно ужать примерно в 2 раза почти без потери качества
┃ 3 картинки на телефоне выглядят нечётко — например, 20let.png
────
Заявки и контакты [стоит поправить·primary]
┃ связаться можно: звонком в одно касание
┃ по кнопке звонка набирается 43-43-48 — с мобильного без кода города не дозвониться
┃ посещения считают Яндекс Метрика и Top.Mail.ru
────
Поиск в Google [хорошо·success]
┃ страница открыта для поисковиков
┃ заголовок для поиска: «Шиномонтаж в Кирове - "АвтоСпасатель". Услуги шиномонтажа, автопомощи…»
────
Ссылка в мессенджерах [хорошо·success]
┃ для превью заданы картинка и название «Шиномонтаж в Кирове - АвтоСпасатель. Услуги шиномонтажа, автопомощи в…»
────
Удобство чтения [стоит поправить·primary]
┃ местами текст плохо виден на фоне — например, «Политикой обработки персональных данных и файлов Cookie»
┃ на солнце и людям со слабым зрением его трудно прочитать
┃ у 9 картинок нет подписи — например, 20let.png, avtomoyka-open-340x210.jpg
┃ Google хуже понимает, что на них, а незрячим посетителям программа не скажет, что там
────
Что поправить в первую очередь
> Ужать картинки — страница станет легче и быстрее откроется с телефона.
> Загрузить картинки крупнее — на телефоне они станут чёткими.
> Указать в кнопке звонка номер с кодом города — тогда с мобильного дозвонятся.
────
jw-dev.pro · @jw_dev_pro"""

WIX_COFFEE_FRAGMENT_RU = """Поиск в Google [стоит поправить·primary]
┃ заголовок для поиска: «Главная | Mysite»
┃ описания для поиска нет — Google сам выберет кусок текста со страницы
────
Ссылка в мессенджерах [стоит поправить·primary]
┃ для превью не задана картинка — в Telegram ссылка придёт без картинки
┃ название в превью: «Главная | Mysite»
────
Удобство чтения [стоит поправить·primary]
┃ местами текст плохо виден на фоне — например, «© 2023 «Бариста». Сайт создан на Wix.com»
┃ на солнце и людям со слабым зрением его трудно прочитать"""

EXAMPLE_RU = """[полоса ~/проверка-сайта_ (мигает _)]
example.com
Есть что чинить: с телефона открывается медленно, и страница слишком тяжёлая.
────
Скорость [плохо·danger]
┃ главное на экране — через 7 секунд
┃ больше всего времени уходит на тяжёлые картинки
────
Телефон [стоит поправить·primary]
┃ мобильная версия есть
┃ кнопки и ссылки стоят тесно: пальцем легко попасть не туда
────
Защита [стоит поправить·primary]
┃ сертификат действует до 14 марта 2027
┃ по ссылке без https часть браузеров откроет сайт без защиты, с пометкой «Не защищено»
────
Картинки [плохо·danger]
┃ страница весит 12 МБ, из них 10 МБ — картинки
┃ самые тяжёлые: slider-1.jpg — 3,2 МБ, about.png — 2,1 МБ, team.jpg — 1,8 МБ
┃ картинки можно ужать примерно в 6 раз почти без потери качества
────
Что поправить в первую очередь
> Ужать картинки — страница станет легче и быстрее откроется с телефона.
> Включить переадресацию на https — чтобы по любой ссылке сайт открывался защищённым.
> Раздвинуть кнопки и ссылки в мобильной версии.
────
jw-dev.pro · @jw_dev_pro"""

EXAMPLE_EN = """[banner ~/site-check_ (blinks _)]
example.com
Needs fixing: it loads slowly on phones, and the page is too heavy.
────
Speed [poor·danger]
┃ the main content appears after 7 seconds
┃ most of that time goes to heavy images
────
Phone [worth fixing·primary]
┃ there is a mobile version
┃ buttons and links sit too close together: it's easy to tap the wrong one
────
Security [worth fixing·primary]
┃ the certificate is valid until 14 March 2027
┃ links without https open the site unprotected in some browsers, marked "Not secure"
────
Images [poor·danger]
┃ the page weighs 12 MB, 10 MB of it images
┃ heaviest: slider-1.jpg — 3.2 MB, about.png — 2.1 MB, team.jpg — 1.8 MB
┃ the images can be compressed about 6 times with almost no loss in quality
────
Fix first
> Compress the images — the page gets lighter and opens faster on phones.
> Turn on the redirect to https — so the site opens secure from any link.
> Give buttons and links more room in the mobile version.
────
jw-dev.pro · @jw_dev_pro"""

# Отчёт при сертификате, который блокирует браузер (задача 23a): блоки без данных из-за той же причины
# собираются под один заголовок, идущий после оценённых блоков.
EXPIRED_BADSSL_RU = """[полоса ~/проверка-сайта_ (мигает _)]
expired.badssl.com
Браузер не пускает на сайт: сертификат истёк.
────
Защита [плохо·danger]
┃ сертификат истёк 12 апреля 2015
┃ браузер показывает предупреждение во весь экран, сайт открывается только через «Дополнительно»
────
Скорость, телефон, картинки [не удалось проверить]
┃ браузер не открывает сайт из-за сертификата
────
Что поправить в первую очередь
> Заменить сертификат — сейчас браузер пугает посетителей предупреждением.
────
jw-dev.pro · @jw_dev_pro"""


def example_page():
    return page(speed(lcp=7000, image=3000), mobile(target=AuditState.FAILED),
                images(page_bytes=12 * MB, image_bytes=10 * MB, heaviest=EXAMPLE_HEAVIEST, ratio=6))


def example_security():
    return security(days_left=170, redirects=(RedirectState.NO_REDIRECT,))


def build(lang, request_page, request_security, display="example.com", is_admin=False,
          preview_facts=None) -> tuple[dict, dict]:
    verdict = judge(request_page, request_security, TODAY, preview_facts)
    request = ReportRequest(display, display.split("/")[0], verdict, request_page, request_security, is_admin,
                            MEASURED_AT, preview_facts)
    return build_report(TEXTS, lang, BRAND, request)


def report(lang, request_page, request_security, display="example.com", is_admin=False, preview_facts=None) -> dict:
    return build(lang, request_page, request_security, display, is_admin, preview_facts)[0]


def test_comparison_paragraph_goes_right_after_the_summary():
    found = Comparison(date(2026, 9, 12), (4200, 2100), (12 * MB, 3_250_586),
                       ((Block.SPEED, Grade.BAD, Grade.GOOD),))
    verdict = judge(page(), security(), TODAY)
    request = ReportRequest("site.test", "site.test", verdict, page(), security(), False, MEASURED_AT, None, found)
    lines = rich_text(build_report(TEXTS, "ru", BRAND, request)[0]).splitlines()
    assert lines[3] == ("С прошлой проверки (12 сентября 2026): главное на экране — 4,2 секунды → 2,1 секунды, "
                        "страница — 12 МБ → 3,1 МБ; скорость — плохо → хорошо.")


def jw_dev_pro_request_parts():
    facts = page(search_facts=search(crawlable=AuditState.FAILED, source=BlockSource.META),
                readability_facts=readability(), screenshot=TINY_JPEG)
    shown = preview(head(title=JW_DEV_PRO_TITLE, og_title=JW_DEV_PRO_TITLE,
                         og_image="https://jw-dev.pro/og/jw-dev-pro.jpg"),
                    contact_facts=contacts(call_links=0, telegram=True, email=True, forms=1, markers=frozenset()))
    return facts, security(domain=DomainPaid("jw-dev.pro", date(2027, 8, 25), RDAP)), shown


def test_jw_dev_pro_report_matches_spec():
    facts, security_facts, shown = jw_dev_pro_request_parts()
    text = rich_text(report("ru", facts, security_facts, display="jw-dev.pro", preview_facts=shown))
    assert text == JW_DEV_PRO_RU


def test_avtospasatel_report_matches_spec():
    heavy = images(page_bytes=4_961_882, image_bytes=1_590_607, heaviest=AVTOSPASATEL_HEAVIEST, ratio=2,
                   blurry=("20let.png", "15-podpiska-340x210.jpg", "avtomoyka-open-340x210.jpg"))
    facts = page(speed(lcp=13_631, image=2050, script=684, tbt=534), image_facts=heavy, search_facts=search(),
                readability_facts=readability(contrast=AuditState.FAILED,
                                              examples=("Политикой обработки персональных данных и файлов Cookie",),
                                              alt=AuditState.FAILED,
                                              alt_names=("20let.png", "avtomoyka-open-340x210.jpg"), alt_count=9),
                screenshot=TINY_JPEG)
    shown = preview(head(title=AVTOSPASATEL_TITLE, og_title=AVTOSPASATEL_OG,
                         og_image="https://avtospasatel43.ru/images/500x500-blue-logo.jpg"),
                    contact_facts=contacts(short=("43-43-48",), markers=frozenset({"metrika", "top_mail"})))
    paid = security(domain=DomainPaid("avtospasatel43.ru", date(2027, 6, 16), "whois"))
    text = rich_text(report("ru", facts, paid, display="avtospasatel43.ru", preview_facts=shown))
    assert text == AVTOSPASATEL_RU


def test_wix_coffee_fragment_matches_spec():
    facts = page(search_facts=search(description=AuditState.FAILED),
                readability_facts=readability(contrast=AuditState.FAILED,
                                              examples=("© 2023 «Бариста». Сайт создан на Wix.com",)))
    shown = preview(head(title="Главная | Mysite", og_title="Главная | Mysite", description=None, og_image=None))
    assert WIX_COFFEE_FRAGMENT_RU in rich_text(report("ru", facts, security(), preview_facts=shown))


def test_bot_protection_stub_hides_preview_and_keeps_search_from_lighthouse():
    facts = page(search_facts=search(description=AuditState.FAILED))
    text = rich_text(report("ru", facts, security(), preview_facts=preview(failure=FetchFailure.STATUS, status=403)))
    assert "Ссылка в мессенджерах" not in text and "заголовок для поиска" not in text
    assert ("Поиск в Google [стоит поправить·primary]\n"
            "┃ описания для поиска нет — Google сам выберет кусок текста со страницы") in text


@pytest.mark.parametrize(("state", "fact"), [
    (ImageState.BROKEN, "картинка для превью не открывается (ошибка 404) — в Telegram ссылка придёт без картинки"),
    (ImageState.NOT_IMAGE, "картинка для превью не открывается — в Telegram ссылка придёт без картинки"),
    (ImageState.SVG, "картинка для превью — в формате SVG, Telegram её не показывает"),
    (ImageState.RELATIVE, "адрес картинки для превью указан не полностью — Telegram её не показывает"),
])
def test_preview_image_lines_with_the_title_after_them(state, fact):
    shown = preview(head(og_title="Кафе"), image_state=state, image_status=404 if state is ImageState.BROKEN else 200)
    text = rich_text(report("ru", page(), security(), preview_facts=shown))
    assert f"Ссылка в мессенджерах [стоит поправить·primary]\n┃ {fact}\n┃ название в превью: «Кафе»" in text


def test_preview_good_without_description_and_without_title():
    no_description = preview(head(og_title="Кафе", description=None))
    assert "┃ для превью заданы картинка и название «Кафе», без описания" in \
        rich_text(report("ru", page(), security(), preview_facts=no_description))
    untitled = rich_text(report("ru", page(), security(), preview_facts=preview(head(title=None))))
    assert "┃ название для превью не задано — вместо него покажется адрес сайта" in untitled


def test_preview_lines_in_english():
    text = rich_text(report("en", page(), security(), preview_facts=preview(head(og_image=None))))
    assert "Link in messengers [worth fixing·primary]\n┃ no preview image is set — in Telegram the link will arrive" in text


def test_example_report_matches_spec_in_russian():
    assert rich_text(report("ru", example_page(), example_security())) == EXAMPLE_RU


def test_example_report_matches_spec_in_english():
    assert rich_text(report("en", example_page(), example_security())) == EXAMPLE_EN


def test_picture_lines_one_and_many():
    many = rich_text(report("ru", page(image_facts=images(blurry=("20let.png", "a.jpg", "b.jpg"))), security()))
    one = rich_text(report("ru", page(image_facts=images(stretched=("team.jpg",))), security()))
    assert "┃ 3 картинки на телефоне выглядят нечётко — например, 20let.png" in many
    assert "┃ картинка растянута или сплющена — team.jpg" in one
    assert "Картинки [стоит поправить·primary]\n┃ страница весит 260 КБ\n┃ самая тяжёлая — 00-oblozhka.webp, 110 КБ" in one


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
    assert buttons[2][0]["text"] == "📣 Канал"


def test_owner_gets_numbers_for_post_and_others_do_not():
    owner = rich_text(report("ru", page(), security(), is_admin=True))
    assert "Подробные замеры — видите только вы" in owner
    assert "Главное на экране (LCP) | 1,4 секунды" in owner
    assert "Замер (UTC+7) | 25.09.2026 12:30" in owner
    assert "Подробные замеры — видите только вы" not in rich_text(report("ru", page(), security()))


def test_screenshot_goes_right_under_the_domain():
    message = report("ru", page(screenshot=TINY_JPEG), security(), display="jw-dev.pro")
    assert rich_text(message).splitlines()[:3] == ["[полоса ~/проверка-сайта_ (мигает _)]", "jw-dev.pro",
                                                    "[снимок первого экрана]"]
    assert message["blocks"][2]["photo"]["media"].filename == "screen.jpg"


def test_no_screenshot_no_block():
    assert "[снимок первого экрана]" not in rich_text(report("ru", page(), security()))


def test_owner_sees_the_screenshot_size():
    owner = rich_text(report("ru", page(screenshot=TINY_JPEG), security(), is_admin=True))
    assert "Снимок первого экрана | 1 КБ" in owner
    assert "Снимок первого экрана | нет в ответе" in rich_text(report("ru", page(), security(), is_admin=True))


def test_unknown_security_is_named_in_summary():
    failed = SecurityFacts((TlsFacts("site.test", TlsOutcome.CONNECT_FAILED),), (RedirectState.CLOSED,), ())
    text = rich_text(report("ru", page(), failed))
    assert "Всё, что удалось проверить, в порядке. Не удалось проверить защиту." in text
    assert "Защита [не удалось проверить]\n┃ сайт не ответил на мои запросы" in text


def test_expiring_certificate_sentence_and_fix():
    text = rich_text(report("ru", page(), security(days_left=6)))
    assert ("┃ сертификат действует до 1 октября 2026\n"
            "┃ сертификат закончится через 6 дней — если он не продлится сам, браузер начнёт показывать "
            "предупреждение") in text
    assert "> Проверить автопродление сертификата до 1 октября 2026." in text


def test_security_names_the_paid_domain_date():
    facts = security(domain=DomainPaid("jw-dev.pro", date(2027, 8, 25), RDAP))
    assert ("Защита [хорошо·success]\n┃ сертификат действует до 8 декабря 2026\n┃ адрес без https переводит на "
            "защищённый\n┃ домен оплачен до 25 августа 2027") in rich_text(report("ru", page(), facts))


def test_domain_ending_soon_is_worth_fixing_with_its_date():
    facts = security(domain=DomainPaid("site.ru", date(2026, 10, 14), "whois"))
    text = rich_text(report("ru", page(), facts))
    assert "В целом в порядке, но скоро кончается оплата домена." in text
    assert "┃ домен оплачен до 14 октября 2026 — если не продлить, сайт и почта перестанут открываться" in text
    assert "> Продлить домен до 14 октября 2026 или включить автопродление." in text


def test_missing_server_response_time_uses_plain_texts():
    """server_ms может быть None (лидирует document-latency-insight, а server-response-time пропал)."""
    facts = page(speed(lcp=7000.0, server=2000.0, server_ms=None))
    text = rich_text(report("ru", facts, security()))
    assert "┃ больше всего времени уходит на ответ сервера" in text
    assert "> Разобраться с сервером или хостингом — он долго думает, прежде чем отдать страницу." in text
    assert "0 секунд" not in text


def test_fast_server_response_uses_plain_texts_even_when_named_the_cause():
    """server_savings_ms (document-latency-insight) включает переадресации и сжатие, поэтому причина «сервер»
    может выбраться и когда сам server_ms маленький — тогда тоже без выдуманных секунд."""
    facts = page(speed(lcp=7000.0, server=2000.0, server_ms=40.0))
    text = rich_text(report("ru", facts, security()))
    assert "┃ больше всего времени уходит на ответ сервера" in text
    assert "> Разобраться с сервером или хостингом — он долго думает, прежде чем отдать страницу." in text
    assert "0 секунд" not in text


def test_expired_certificate_with_a_future_leaf_date_uses_no_date_text():
    """Код ошибки 10 (истёк) может относиться к промежуточному сертификату цепочки — read_cert_unverified читает
    лист, и его срок ещё не кончился. Дата листа тут ни при чём: с ней текст выглядел бы как «истёк, 25 октября
    2026», хотя эта дата ещё не наступила."""
    facts = SecurityFacts((TlsFacts("site.test", TlsOutcome.EXPIRED, cert(days_left=30)),),
                          (RedirectState.REDIRECTS,), ())
    text = rich_text(report("ru", page(), facts))
    assert "┃ сертификат истёк\n┃ браузер показывает предупреждение во весь экран" in text
    assert "октября" not in text


def test_expired_certificate_without_parseable_data_uses_no_date_text():
    """read_cert_unverified может не разобрать сертификат и отдать cert=None — тогда без пустой даты в строке."""
    failed_cert = SecurityFacts((TlsFacts("site.test", TlsOutcome.EXPIRED, None),), (RedirectState.REDIRECTS,), ())
    text_ru = rich_text(report("ru", page(), failed_cert))
    assert "┃ сертификат истёк\n┃ браузер показывает предупреждение во весь экран" in text_ru
    text_en = rich_text(report("en", page(), failed_cert))
    assert "┃ the certificate has expired\n┃ the browser shows a full-screen warning" in text_en


def test_security_bad_certificate_still_lists_other_consequences():
    """Сертификат «плохо» не должен молча прятать остальные находки блока (общее правило — у каждой находки
    должно быть последствие для посетителя), каждая — своей строкой «>»."""
    facts = SecurityFacts((TlsFacts("site.test", TlsOutcome.WRONG_HOST, cert()),), (RedirectState.REDIRECTS,),
                          ("http://x/a.js",))
    text = rich_text(report("ru", page(), facts))
    assert "┃ сертификат выдан на другой адрес" in text
    assert "┃ часть файлов страницы грузится без защиты" in text


def two_host_security(first_outcome, second_outcome, first_cert=True, second_cert=True) -> SecurityFacts:
    """Присланный хост и итоговый (после переадресации) проверяются отдельно — у обоих может найтись «плохо»."""
    return SecurityFacts((TlsFacts("site.test", first_outcome, cert() if first_cert else None),
                          TlsFacts("www.site.test", second_outcome, cert() if second_cert else None)),
                         (RedirectState.REDIRECTS, RedirectState.REDIRECTS), ())


def security_fact_lines(text: str, lang: str) -> list[str]:
    """Строки цитаты блока «Защита» — между его заголовком и следующим («Картинки»/«Images»)."""
    security_title, next_title = ("Защита", "Картинки") if lang == "ru" else ("Security", "Images")
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(security_title))
    end = next(i for i, line in enumerate(lines) if i > start and line.startswith(next_title))
    return [line for line in lines[start:end] if line.startswith("┃ ")]


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
    assert "┃ сертификат выдан на другой адрес" in text_ru
    assert "┃ сервер отдаёт сертификат не полностью, и в части браузеров и приложений сайт покажется небезопасным" \
        in text_ru
    text_en = rich_text(report("en", page(), facts))
    assert "┃ the certificate is issued for another address" in text_en
    assert "┃ the server sends the certificate incompletely, so some browsers and apps will show the site as " \
        "unsafe" in text_en


def test_no_https_with_secure_redirect_elsewhere_report():
    """Голый домен без https, переадресация на защищённую версию на другом хосте — «стоит поправить», не «плохо»;
    дата сертификата берётся у итогового хоста."""
    facts = two_host_security(TlsOutcome.NO_HTTPS, TlsOutcome.OK, first_cert=False)
    result_page = page(final_url="https://www.site.test/")
    text_ru = rich_text(report("ru", result_page, facts))
    assert "Защита [стоит поправить·primary]" in text_ru
    assert "┃ сертификат действует до 8 декабря 2026" in text_ru
    assert "┃ ссылка с https на присланный адрес не откроется — браузер покажет ошибку" in text_ru
    assert "> Подключить сертификат и на этот адрес — тогда сайт откроется по любой ссылке." in text_ru
    assert "работает без защиты" not in text_ru
    text_en = rich_text(report("en", result_page, facts))
    assert "Security [worth fixing·primary]" in text_en
    assert "┃ the certificate is valid until 8 December 2026" in text_en
    assert "┃ a https link to this exact address won't open — the browser shows an error" in text_en
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


def test_all_good_mentions_search_only_when_search_was_checked():
    verdict = judge(page(), security(), TODAY)
    assert summary_text(TEXTS, "ru", verdict) == ("Сайт в порядке: открывается быстро, на телефоне удобен, "
                                                  "защита работает.")
    searched = replace(verdict, blocks={**verdict.blocks, Block.SEARCH: BlockVerdict(Block.SEARCH, Grade.GOOD)})
    assert summary_text(TEXTS, "ru", searched) == ("Сайт в порядке: открывается быстро, на телефоне удобен, "
                                                   "защита работает, поисковикам открыт.")


def test_new_blocks_without_data_are_neither_printed_nor_named():
    failed = SecurityFacts((TlsFacts("site.test", TlsOutcome.CONNECT_FAILED),), (RedirectState.CLOSED,), ())
    text = rich_text(report("ru", page(), failed))
    assert "Всё, что удалось проверить, в порядке. Не удалось проверить защиту." in text
    assert not any(name in text for name in ("Поиск в Google", "Ссылка в мессенджерах", "Удобство чтения"))


def test_new_troubles_and_fixes_have_their_words():
    closed = FindingItem(Finding.CLOSED_META, Grade.BAD)
    verdict = Verdict({block: BlockVerdict(block, Grade.GOOD) for block in Block}, SummaryKind.HAS_BAD, (closed,),
                      (FixItem(FixKey.UNBLOCK_META, closed),))
    assert summary_text(TEXTS, "ru", verdict) == "Есть что чинить: страница закрыта от поисковиков."
    assert fix_text(TEXTS, "ru", verdict.fixes[0]) == ("Снять запрет noindex — иначе страницу не найти в Google "
                                                       "и Яндексе.")


def test_readability_lines_name_the_pale_text_and_the_pictures():
    facts = page(readability_facts=readability(contrast=AuditState.FAILED, examples=("НАШИ ЦЕНЫ",),
                                               alt=AuditState.FAILED, alt_names=("20let.png", "diplom-tm-2025-m.jpg"),
                                               alt_count=9, lang=AuditState.FAILED))
    text = rich_text(report("ru", facts, security()))
    assert ("Удобство чтения [стоит поправить·primary]\n"
            "┃ местами текст плохо виден на фоне — например, «НАШИ ЦЕНЫ»\n"
            "┃ на солнце и людям со слабым зрением его трудно прочитать\n"
            "┃ у 9 картинок нет подписи — например, 20let.png, diplom-tm-2025-m.jpg\n"
            "┃ Google хуже понимает, что на них, а незрячим посетителям программа не скажет, что там\n"
            "┃ в коде не указан язык страницы — программа чтения вслух может читать текст с чужим произношением"
            ) in text
    assert "В целом в порядке, но местами текст плохо виден." in text


def test_readability_good_lines_and_silence_without_facts():
    good = rich_text(report("ru", page(readability_facts=readability()), security()))
    assert "Удобство чтения [хорошо·success]\n┃ текст хорошо виден на фоне\n┃ у картинок есть подписи" in good
    no_images = rich_text(report("ru", page(readability_facts=readability(alt=AuditState.NOT_APPLICABLE)), security()))
    assert "Удобство чтения [хорошо·success]\n┃ текст хорошо виден на фоне\n────\nЧто поправить" in no_images
    silent = readability(contrast=AuditState.UNKNOWN, alt=AuditState.NOT_APPLICABLE)
    assert "Удобство чтения" not in rich_text(report("ru", page(readability_facts=silent), security()))


def test_readability_lines_in_english():
    facts = page(readability_facts=readability(alt=AuditState.FAILED, alt_names=("team.jpg",)))
    text = rich_text(report("en", facts, security()))
    assert "Readability [worth fixing·primary]\n┃ no alt text on 1 image — for example, team.jpg" in text


def test_closed_page_report_lines_and_fix():
    facts = page(search_facts=search(crawlable=AuditState.FAILED, source=BlockSource.META))
    text = rich_text(report("ru", facts, security()))
    assert "Есть что чинить: страница закрыта от поисковиков." in text
    assert ("Поиск в Google [плохо·danger]\n┃ страница закрыта от поисковиков: в коде стоит запрет noindex\n"
            "┃ Google и Яндекс не покажут её в поиске") in text
    assert "> Снять запрет noindex — иначе страницу не найти в Google и Яндексе." in text


def test_open_page_quotes_the_real_title_or_speaks_generally():
    quoted = rich_text(report("ru", page(search_facts=search()), security(),
                              preview_facts=preview(head(title="Кофейня Vitru | Нижний Тагил | Главная"))))
    assert ("Поиск в Google [хорошо·success]\n┃ страница открыта для поисковиков\n"
            "┃ заголовок для поиска: «Кофейня Vitru | Нижний Тагил | Главная»") in quoted
    general = rich_text(report("ru", page(search_facts=search()), security()))
    assert "┃ заголовок и описание для поиска есть" in general
    assert "защита работает, поисковикам открыт." in general


def test_long_title_is_cut_with_an_ellipsis():
    text = rich_text(report("ru", page(search_facts=search()), security(), preview_facts=preview(head(title="А" * 100))))
    assert "┃ заголовок для поиска: «" + "А" * 69 + "…»" in text


def test_worth_fixing_search_quotes_the_title_first():
    facts = page(search_facts=search(description=AuditState.FAILED, robots=AuditState.FAILED, robots_status=503))
    text = rich_text(report("ru", facts, security(), preview_facts=preview(head(title="Главная | Mysite",
                                                                                canonical="https://other.example/"))))
    assert ("Поиск в Google [стоит поправить·primary]\n┃ заголовок для поиска: «Главная | Mysite»\n"
            "┃ файл robots.txt не открывается (ошибка 503) — Google в таком случае может перестать заходить на сайт\n"
            "┃ описания для поиска нет — Google сам выберет кусок текста со страницы\n"
            "┃ основным адресом в коде указан другой сайт — other.example\n"
            "┃ Google может показывать в поиске его, а не эту страницу") in text


def test_search_lines_in_english():
    facts = page(search_facts=search(crawlable=AuditState.FAILED, source=BlockSource.ROBOTS_TXT))
    text = rich_text(report("en", facts, security()))
    assert ("Google search [poor·danger]\n┃ search engines are forbidden to read the page — the robots.txt file says so\n"
            "┃ it's missing from search or shown without a description") in text


def test_owner_sees_search_preview_and_readability_rows():
    facts = page(search_facts=search(crawlable=AuditState.FAILED, source=BlockSource.META),
                readability_facts=readability(contrast=AuditState.FAILED, examples=("НАШИ ЦЕНЫ",)))
    facts = replace(facts, post=replace(facts.post, category_scores=(("seo", 69), ("accessibility", 100))))
    text = rich_text(post_numbers(TEXTS, "ru", facts, security(), MEASURED_AT, preview()))
    assert 'Индексация | закрыта: <meta name="robots" content="noindex" />' in text
    assert "robots.txt | в порядке" in text
    assert "Заголовок (4 знака) | Сайт" in text
    assert "Своя загрузка страницы | 200, head 6 КБ, 0,4 секунды" in text
    assert "Превью: картинка | og.jpg, image/jpeg, 69 КБ" in text
    assert "Бледный текст, мест | 1" in text
    assert "Язык страницы | ru" in text
    assert "Lighthouse: SEO / доступность | 69 / 100" in text


def test_owner_sees_why_the_new_blocks_are_missing():
    stub = rich_text(post_numbers(TEXTS, "ru", page(), security(), MEASURED_AT,
                                  preview(failure=FetchFailure.STATUS, status=403)))
    assert "Своя загрузка страницы | ответ 403 — не страница" in stub
    assert "Своя загрузка страницы | не запускалась или упала" in rich_text(
        post_numbers(TEXTS, "ru", page(), security(), MEASURED_AT))


def test_owner_sees_why_a_parsed_page_was_hidden():
    """Задача 33: разбор страницы в отдельном процессе не удался — своя загрузка была, разбор — нет."""
    stub = rich_text(post_numbers(TEXTS, "ru", page(), security(), MEASURED_AT,
                                  preview(failure=FetchFailure.PARSE, status=200)))
    assert "Своя загрузка страницы | ответ 200 получен, разобрать страницу не удалось" in stub


def test_contacts_lines_ways_findings_and_counters():
    facts = contacts(short=("43-43-48",), markers=frozenset({"metrika", "top_mail"}))
    text = rich_text(report("ru", page(), security(), preview_facts=preview(contact_facts=facts)))
    assert ("Заявки и контакты [стоит поправить·primary]\n┃ связаться можно: звонком в одно касание\n"
            "┃ по кнопке звонка набирается 43-43-48 — с мобильного без кода города не дозвониться\n"
            "┃ посещения считают Яндекс Метрика и Top.Mail.ru") in text


def test_contacts_ways_in_order_and_missing_counter():
    facts = contacts(call_links=0, telegram=True, email=True, forms=1, markers=frozenset())
    text = rich_text(report("ru", page(), security(), preview_facts=preview(contact_facts=facts)))
    assert ("┃ связаться можно: в Telegram, по почте, через форму заявки\n"
            "┃ счётчика посещений нет — не видно, сколько людей заходит и откуда они пришли") in text
    assert "> Поставить счётчик посещений — станет видно, сколько людей заходит и откуда." in text


def test_platform_statistics_line_and_phone_text_wording():
    facts = contacts(call_links=0, text_phones=1, tap_blocked=True, markers=frozenset({"tilda_stats"}))
    text = rich_text(report("ru", page(), security(), preview_facts=preview(contact_facts=facts)))
    assert "┃ телефон написан просто текстом — по нему нельзя нажать, чтобы позвонить" in text
    assert "┃ посещения считает статистика Tilda" in text


def test_owner_sees_how_the_page_was_read_and_what_was_found():
    facts = contacts(short=("43-43-48",), forms=2, complete=False, markers=frozenset({"metrika", "jivo"}))
    text = rich_text(post_numbers(TEXTS, "ru", page(), security(), MEASURED_AT, preview(contact_facts=facts)))
    assert "Страница прочитана | обрезана на 2 МБ" in text
    assert "Контакты | tel: 1, без кода: 1, телефонов текстом: 0, форм с личными полями: 2" in text
    assert "Сервисы на странице | jivo, metrika" in text
    assert "Ссылка на политику персональных данных | есть" in text


def test_contacts_lines_in_english():
    facts = contacts(call_links=0, text_phones=1)
    text = rich_text(report("en", page(), security(), preview_facts=preview(contact_facts=facts)))
    assert "Leads and contacts [worth fixing·primary]" in text
    assert "┃ the phone is plain text — on Android it can't be tapped to call" in text
