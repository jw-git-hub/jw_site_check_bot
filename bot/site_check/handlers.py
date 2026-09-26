"""Приём ссылок и выполнение проверок (ТЗ, разделы 3, 7.6, 9). Обработчики aiogram — тонкие: работа — в Intake
и CheckRunner, которые знают Telegram только через Messenger."""
import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, MessageEntity
from loguru import logger

from bot.core.clock import Clock
from bot.core.commands import Brand
from bot.core.i18n import Texts
from bot.core.messenger import DeliveryFailed, Messenger, edit_or_send
from bot.core.stats import best_effort
from bot.core.users import User, Users
from bot.settings import Settings
from bot.site_check import replies
from bot.site_check.checks import FAILED, QUEUED, ChecksRepo, NewCheck
from bot.site_check.limits import LIMIT_GLOBAL, LIMIT_USER, Limits
from bot.site_check.notifier import DAY, ONCE, SIX_HOURS, Notifier
from bot.site_check.pagespeed import MEASURE_FAILED
from bot.site_check.pipeline import CheckFailed, CheckResult, Pipeline
from bot.site_check.probe import SERVICE_DOWN
from bot.site_check.queue import CheckJob, CheckQueue
from bot.site_check.report import AGAIN_CALLBACK, ReportRequest, build_report
from bot.site_check.url_input import SOCIAL, Rejection, Target, parse_input

BUSY = "busy"
QUEUE_FULL = "queue_full"
NOT_TEXT = "not_text"
COMMAND_PREFIX = "/"
SERVICE_NOTICES = {"quota": "notify_pagespeed_quota", "key": "notify_pagespeed_key"}
OTHER_SERVICE_NOTICE = "notify_pagespeed_other"
AUDITS_JOIN = ", "
RETRY_DELAY_SECONDS = 2  # поправка 4 к задаче 17: 1–3 с — доставка итога повторяется один раз
DELIVERY_ATTEMPTS = 2  # первая попытка плюс одна повторная

router = Router(name="site_check")


@dataclass(frozen=True)
class IncomingText:
    user_id: int
    chat_id: int
    language_code: str | None
    text: str
    entity_urls: list[str]


def entity_urls(text: str, entities: list[MessageEntity]) -> list[str]:
    urls = []
    for entity in entities:
        if entity.type == "text_link" and entity.url:
            urls.append(entity.url)
        elif entity.type == "url":
            urls.append(entity.extract_from(text))
    return urls


def incoming_from(message: Message) -> IncomingText:
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    return IncomingText(message.from_user.id, message.chat.id, message.from_user.language_code, text,
                        entity_urls(text, entities))


class Intake:
    def __init__(self, users: Users, repo: ChecksRepo, limits: Limits, queue: CheckQueue, messenger: Messenger,
                 texts: Texts, brand: Brand, settings: Settings, notifier: Notifier):
        self._users = users
        self._repo = repo
        self._limits = limits
        self._queue = queue
        self._messenger = messenger
        self._texts = texts
        self._brand = brand
        self._settings = settings
        self._notifier = notifier

    async def handle_text(self, incoming: IncomingText) -> None:
        # Поправка 7: человек отмечен визитом раньше любой записи проверки — до разбора и до отказов.
        user = await self._users.touch(incoming.user_id, incoming.language_code)
        parsed = parse_input(incoming.text, incoming.entity_urls)
        if isinstance(parsed, Rejection):
            await self._refuse(incoming.chat_id, user, None, parsed.code, self._rejection_message(user, parsed))
            return
        refusal = await self._refusal(user, parsed)
        if refusal:
            await self._refuse(incoming.chat_id, user, parsed, *refusal)
            return
        await self._enqueue(incoming.chat_id, user, parsed)

    async def handle_not_text(self, user_id: int, chat_id: int, language_code: str | None) -> None:
        user = await self._users.touch(user_id, language_code)
        message = replies.failure(self._texts, user.lang, self._brand, NOT_TEXT)
        await self._refuse(chat_id, user, None, NOT_TEXT, message)

    def _rejection_message(self, user: User, rejection: Rejection) -> dict:
        if rejection.code == SOCIAL:
            return replies.social(self._texts, user.lang, self._brand, rejection.platform)
        return replies.failure(self._texts, user.lang, self._brand, rejection.code)

    async def _refusal(self, user: User, target: Target) -> tuple[str, dict] | None:
        busy_with = self._queue.busy_display(user.user_id)
        if busy_with:
            return BUSY, replies.busy(self._texts, user.lang, self._brand, busy_with)
        decision = await self._limits.decide(user.user_id)
        if decision.code == LIMIT_USER:
            limit = self._settings.user_daily_limit
            return LIMIT_USER, replies.limit_user(self._texts, user.lang, self._brand, limit, decision.hours_left)
        if decision.code == LIMIT_GLOBAL:
            checks = self._texts.count("ru", self._settings.global_daily_limit, "check")
            await self._notifier.notify(LIMIT_GLOBAL, "notify_global_limit", DAY, checks=checks)
            return LIMIT_GLOBAL, replies.failure(self._texts, user.lang, self._brand, LIMIT_GLOBAL)
        if self._queue.is_full():
            return QUEUE_FULL, replies.failure(self._texts, user.lang, self._brand, QUEUE_FULL)
        return None

    async def _refuse(self, chat_id: int, user: User, target: Target | None, code: str, message: dict) -> None:
        try:
            await self._messenger.send(chat_id, message)
        except DeliveryFailed as error:
            logger.warning("отказ не доставлен: {}", error)
        new = NewCheck(user.user_id, user.last_source, target, FAILED, code)
        await best_effort(self._repo.create(new), "отказ", None)

    async def _enqueue(self, chat_id: int, user: User, target: Target) -> None:
        # Раунд ревью 1, находка 1 (поправка 9): ровно одно освобождение места на любом пути — не встала
        # работа в очередь по любой причине (включая переполнение) — release здесь и только здесь.
        self._queue.reserve(user.user_id, target.display)
        submitted = False
        try:
            submitted = await self._start(chat_id, user, target)
        except DeliveryFailed as error:
            logger.warning("статус проверки не доставлен: {}", error)
        finally:
            if not submitted:
                self._queue.release(user.user_id)

    async def _start(self, chat_id: int, user: User, target: Target) -> bool:
        """True — работа встала в очередь; место освободит воркер сам, когда проверка закончится."""
        ahead = self._queue.ahead_now()
        message_id = await self._messenger.send(chat_id, self._status(user, target, ahead))
        new = NewCheck(user.user_id, user.last_source, target, QUEUED, None, chat_id, message_id)
        check_id = await best_effort(self._repo.create(new), "новая проверка", None)
        job = CheckJob(check_id, user.user_id, chat_id, message_id, user.lang, user.last_source, target,
                       user.user_id == self._settings.admin_id, was_queued=ahead > 0)
        try:
            self._queue.submit(job)
        except asyncio.QueueFull:
            await self._queue_overflow(job)
            return False
        return True

    def _status(self, user: User, target: Target, ahead: int) -> dict:
        if ahead == 0:
            return replies.checking(self._texts, user.lang, self._brand, target.display)
        return replies.queued(self._texts, user.lang, self._brand, ahead, self._queue.estimate_minutes(ahead))

    async def _queue_overflow(self, job: CheckJob) -> None:
        """Очередь заполнилась между reserve и submit: запись закрывается первой, доставка отказа — best
        effort (раунд ревью 1, находка 1) — release места делает единственный вызывающий, `_enqueue`."""
        if job.check_id:
            await best_effort(self._repo.finish_failed(job.check_id, QUEUE_FULL, charged=False), "очередь полна", None)
        message = replies.failure(self._texts, job.lang, self._brand, QUEUE_FULL)
        try:
            await edit_or_send(self._messenger, job.chat_id, job.message_id, message)
        except DeliveryFailed as error:
            logger.warning("отказ «очередь полна» не доставлен: {}", error)


class CheckRunner:
    def __init__(self, pipeline: Pipeline, repo: ChecksRepo, limits: Limits, messenger: Messenger, texts: Texts,
                 brand: Brand, notifier: Notifier, clock: Clock):
        self._pipeline = pipeline
        self._repo = repo
        self._limits = limits
        self._messenger = messenger
        self._texts = texts
        self._brand = brand
        self._notifier = notifier
        self._clock = clock

    async def run(self, job: CheckJob) -> None:
        await self._mark_running(job)
        try:
            result = await self._pipeline.run(job.target)
        except CheckFailed as failure:
            await self._finish_failed(job, failure)
        except Exception:  # noqa: BLE001 — одна проверка не должна ронять воркер
            logger.exception("проверка {} упала", job.check_id)
            await self._finish_failed(job, CheckFailed(MEASURE_FAILED, reached_measurement=False))
        else:
            await self._finish_done(job, result)

    async def _mark_running(self, job: CheckJob) -> None:
        if job.check_id:
            await best_effort(self._repo.mark_running(job.check_id), "начало проверки", None)
        if job.was_queued:
            await self._show(job, replies.checking(self._texts, job.lang, self._brand, job.target.display))

    async def _show(self, job: CheckJob, message: dict) -> None:
        """Статус «Проверяю…»: без повторной попытки — свежий статус важнее старого (поправка 4)."""
        try:
            job.message_id = await edit_or_send(self._messenger, job.chat_id, job.message_id, message)
        except DeliveryFailed as error:
            logger.warning("сообщение проверки {} не доставлено: {}", job.check_id, error)

    async def _deliver(self, job: CheckJob, message: dict) -> None:
        """Итог (отчёт или отказ) — до двух попыток с паузой между ними; не прошли обе — один раз в журнал
        (поправка 4; раунд ревью 1, находка 4: одна функция вместо пары почти одинаковых)."""
        for attempt in range(1, DELIVERY_ATTEMPTS + 1):
            try:
                job.message_id = await edit_or_send(self._messenger, job.chat_id, job.message_id, message)
                return
            except DeliveryFailed as error:
                if attempt == DELIVERY_ATTEMPTS:
                    logger.warning("итог проверки {} не доставлен: {}", job.check_id, error)
                else:
                    await asyncio.sleep(RETRY_DELAY_SECONDS)

    def _safe_message(self, build: Callable[[], dict]) -> dict | None:
        """Сборка сообщения — тоже под защитой: неожиданное сочетание находок не должно ронять воркер
        и оставлять человека со статусом «Проверяю…» навсегда (поправка 3)."""
        try:
            return build()
        except Exception:  # noqa: BLE001 — падение сборки текста не должно ронять воркер
            logger.exception("сборка сообщения не удалась")
            return None

    async def _finish_done(self, job: CheckJob, result: CheckResult) -> None:
        request = ReportRequest(job.target.display, job.target.display_host, result.verdict, result.page,
                                result.security, job.is_admin, self._clock.now())
        message = self._safe_message(lambda: build_report(self._texts, job.lang, self._brand, request))
        if message is None:
            # Раунд ревью 1, находка 3 (ТЗ Л9): сбой сборки отчёта — наша сторона, а не сайта, замер не в счёт.
            await self._finish_failed(job, CheckFailed(MEASURE_FAILED, reached_measurement=False))
            return
        await self._deliver(job, message)
        self._limits.remember_charge(job.user_id)
        if job.check_id:
            await best_effort(self._repo.finish_done(job.check_id, result), "итог проверки", None)
        await self._notify_missing_audits(result)

    async def _finish_failed(self, job: CheckJob, failure: CheckFailed) -> None:
        message = self._safe_message(lambda: replies.failure(self._texts, job.lang, self._brand, failure.code,
                                                              status=failure.page_status, site=job.target.display))
        if message is None:
            message = replies.failure(self._texts, job.lang, self._brand, MEASURE_FAILED)
        await self._deliver(job, message)
        if failure.charged:
            self._limits.remember_charge(job.user_id)
        if job.check_id:
            await best_effort(self._repo.finish_failed(job.check_id, failure.code, failure.charged), "итог", None)
        if failure.code == SERVICE_DOWN:
            await self._notify_service_down(failure)

    async def _notify_service_down(self, failure: CheckFailed) -> None:
        # Поправка 1: владельцу — только о сбое PageSpeed, после того как проверка дошла до замера. Сбой на
        # своей стороне до замера (например, свой DNS) — в журнал, без уведомления.
        if not failure.reached_measurement:
            logger.warning("свой сбой до замера: {}", failure.reason or "")
            return
        await self._notify_service(failure.reason or "")

    async def _notify_service(self, reason: str) -> None:
        key = SERVICE_NOTICES.get(reason, OTHER_SERVICE_NOTICE)
        await self._notifier.notify(f"pagespeed:{key}", key, SIX_HOURS, reason=reason)

    async def _notify_missing_audits(self, result: CheckResult) -> None:
        page = result.page
        if page and page.missing_audits:
            await self._notifier.notify(f"audits:{page.lighthouse_version}", "notify_audits_missing", ONCE,
                                        version=page.lighthouse_version, audits=AUDITS_JOIN.join(page.missing_audits))


@router.message(F.text.startswith(COMMAND_PREFIX))
async def on_unknown_command(message: Message, users: Users, messenger: Messenger, texts: Texts,
                             brand: Brand) -> None:
    user = await users.touch(message.from_user.id, message.from_user.language_code)
    await messenger.send(message.chat.id, replies.again(texts, user.lang, brand))


@router.message(F.text | F.caption)
async def on_text(message: Message, intake: Intake) -> None:
    await intake.handle_text(incoming_from(message))


@router.message()
async def on_other(message: Message, intake: Intake) -> None:
    await intake.handle_not_text(message.from_user.id, message.chat.id, message.from_user.language_code)


@router.callback_query(F.data == AGAIN_CALLBACK)
async def on_again(callback: CallbackQuery, users: Users, messenger: Messenger, texts: Texts, brand: Brand) -> None:
    # Поправка 5: устаревшее нажатие («Проверить другой сайт» на старом отчёте) не должно ронять обработчик.
    with contextlib.suppress(TelegramAPIError):
        await callback.answer()
    user = await users.touch(callback.from_user.id, callback.from_user.language_code)
    await messenger.send(callback.message.chat.id, replies.again(texts, user.lang, brand))
