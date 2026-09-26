from bot.brand import BRAND
from bot.core.commands import simple_message
from bot.locales import TEXTS
from bot.site_check.notifier import DAY, ONCE, Notifier
from tests.fakes import ADMIN_ID, FakeClock, FakeMessenger, rich_text


async def test_first_notification_reuses_the_shared_service_message():
    """Поправка 2: то же сообщение, что и у остальных служебных команд — не своя копия сборки."""
    messenger, clock = FakeMessenger(), FakeClock()
    notifier = Notifier(messenger, ADMIN_ID, clock, TEXTS, BRAND)
    await notifier.notify("pagespeed", "notify_pagespeed_key", DAY)
    expected = simple_message(BRAND, TEXTS.get("ru", "notify_pagespeed_key"))
    assert messenger.sent == [(ADMIN_ID, expected)]


async def test_second_notification_within_the_period_is_skipped():
    messenger, clock = FakeMessenger(), FakeClock()
    notifier = Notifier(messenger, ADMIN_ID, clock, TEXTS, BRAND)
    await notifier.notify("limit", "notify_global_limit", DAY, checks="10 проверок")
    await notifier.notify("limit", "notify_global_limit", DAY, checks="10 проверок")
    assert len(messenger.sent) == 1


async def test_notification_repeats_once_the_period_has_passed():
    messenger, clock = FakeMessenger(), FakeClock()
    notifier = Notifier(messenger, ADMIN_ID, clock, TEXTS, BRAND)
    await notifier.notify("limit", "notify_global_limit", DAY, checks="10 проверок")
    clock.advance(DAY + 1)
    await notifier.notify("limit", "notify_global_limit", DAY, checks="10 проверок")
    assert len(messenger.sent) == 2


async def test_once_period_never_repeats_within_the_same_process():
    messenger, clock = FakeMessenger(), FakeClock()
    notifier = Notifier(messenger, ADMIN_ID, clock, TEXTS, BRAND)
    await notifier.notify("audits", "notify_home_ip", ONCE)
    clock.advance(365 * DAY)
    await notifier.notify("audits", "notify_home_ip", ONCE)
    assert len(messenger.sent) == 1


async def test_delivery_failure_is_logged_and_swallowed():
    messenger, clock = FakeMessenger(), FakeClock()
    messenger.blocked.add(ADMIN_ID)
    notifier = Notifier(messenger, ADMIN_ID, clock, TEXTS, BRAND)
    await notifier.notify("pagespeed", "notify_pagespeed_key", DAY)  # не должно бросить исключение


def test_notification_message_has_the_brand_header():
    assert rich_text(simple_message(BRAND, TEXTS.get("ru", "notify_pagespeed_key"))).startswith(">jw_ ~/site-check\n")
