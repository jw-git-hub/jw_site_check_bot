"""Уведомления владельцу (ТЗ, раздел 8): у каждого вида — свой период, чтобы не засыпать личку."""
import math

from loguru import logger

from bot.core.clock import Clock
from bot.core.commands import simple_message
from bot.core.i18n import Lang, Texts
from bot.core.messenger import DeliveryFailed, Messenger

OWNER_LANG: Lang = "ru"
DAY = 24 * 3600
SIX_HOURS = 6 * 3600
ONCE = math.inf  # один раз за жизнь процесса: после перезапуска — ещё раз


class Notifier:
    def __init__(self, messenger: Messenger, admin_id: int, clock: Clock, texts: Texts):
        self._messenger = messenger
        self._admin_id = admin_id
        self._clock = clock
        self._texts = texts
        self._last: dict[str, float] = {}

    async def notify(self, kind: str, key: str, period: float, **params: object) -> None:
        now = self._clock.monotonic()
        last = self._last.get(kind)
        if last is not None and now - last < period:
            return
        self._last[kind] = now
        # То же служебное сообщение, что и у остальных команд, — не своя копия сборки.
        message = simple_message(self._texts, OWNER_LANG, self._texts.get(OWNER_LANG, key, **params))
        try:
            await self._messenger.send(self._admin_id, message)
        except DeliveryFailed as error:
            logger.warning("уведомление владельцу не ушло: {}", error)
