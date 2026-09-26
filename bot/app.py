"""Сборка и запуск бота (ТЗ, разделы 9 и 13): база, aiogram, очередь, фоновые задачи, сторож зависания."""
import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, TelegramObject
from sqlalchemy.ext.asyncio import AsyncEngine

from bot.brand import BRAND
from bot.core import commands
from bot.core.access import OpenGate
from bot.core.clock import Clock, SystemClock
from bot.core.db import BACKUP_DIR_NAME, backup_daily, create_engine, daily_backup_path, migrate
from bot.core.i18n import detect_lang
from bot.core.messenger import AiogramMessenger, DeliveryFailed, Messenger, edit_or_send
from bot.core.stats import best_effort
from bot.core.throttle import ThrottleMiddleware
from bot.core.users import Users
from bot.core.watchdog import EVENT_LOOP, POLLING, Heartbeat, PollingPulse, loop_pulse, start_watchdog
from bot.locales import TEXTS
from bot.schema import MIGRATIONS
from bot.settings import Settings
from bot.site_check import admin, handlers, replies
from bot.site_check.checks import INTERRUPTED, ChecksRepo
from bot.site_check.handlers import CheckRunner, Intake
from bot.site_check.limits import Limits
from bot.site_check.net_guard import AddressGuard, fetch_home_ip, system_resolver
from bot.site_check.notifier import DAY, Notifier
from bot.site_check.pagespeed import PageSpeedClient
from bot.site_check.pipeline import Pipeline
from bot.site_check.probe import GuardedProbes
from bot.site_check.queue import CheckQueue

ALLOWED_UPDATES = ["message", "callback_query"]
MESSAGE_INTERVAL_SECONDS = 2.0
BUTTON_INTERVAL_SECONDS = 0.7
ADMIN_COMMANDS = ("stats", "site")
HOME_IP_REFRESH_SECONDS = 3600
BACKUP_CHECK_SECONDS = 6 * 3600
STAMP_FORMAT = "%Y%m%d-%H%M%S"


@dataclass
class Parts:
    bot: Bot
    dispatcher: Dispatcher
    engine: AsyncEngine
    http: aiohttp.ClientSession
    repo: ChecksRepo
    messenger: Messenger
    guard: AddressGuard
    notifier: Notifier
    queue: CheckQueue
    heartbeat: Heartbeat


async def build(settings: Settings, clock: Clock) -> Parts:
    engine = create_engine(settings.data_dir)
    await migrate(engine, MIGRATIONS, settings.data_dir / BACKUP_DIR_NAME, clock.now().strftime(STAMP_FORMAT))
    heartbeat = Heartbeat()
    bot = Bot(token=settings.bot_token.get_secret_value())
    bot.session.middleware(PollingPulse(heartbeat))
    messenger = AiogramMessenger(bot)
    http = aiohttp.ClientSession(trust_env=False)
    users, repo = Users(engine, clock), ChecksRepo(engine, clock)
    notifier = Notifier(messenger, settings.admin_id, clock, TEXTS, BRAND)
    guard = AddressGuard(system_resolver)
    queue, intake = _checking(settings, clock, users, repo, messenger, notifier, guard, http)
    dispatcher = build_dispatcher(settings, users, repo, messenger, intake, clock)
    return Parts(bot, dispatcher, engine, http, repo, messenger, guard, notifier, queue, heartbeat)


def _checking(settings: Settings, clock: Clock, users: Users, repo: ChecksRepo, messenger: Messenger,
              notifier: Notifier, guard: AddressGuard, http: aiohttp.ClientSession) -> tuple[CheckQueue, Intake]:
    limits = Limits(repo, clock, settings.user_daily_limit, settings.global_daily_limit, settings.admin_id)
    pagespeed = PageSpeedClient(http, settings.pagespeed_api_key.get_secret_value(), clock)
    pipeline = Pipeline(GuardedProbes(guard), pagespeed, clock)
    runner = CheckRunner(pipeline, repo, limits, messenger, TEXTS, BRAND, notifier, clock)
    queue = CheckQueue(settings.check_workers, settings.queue_max, runner.run, clock)
    return queue, Intake(users, repo, limits, queue, messenger, TEXTS, BRAND, settings, notifier)


def build_dispatcher(settings: Settings, users: Users, repo: ChecksRepo, messenger: Messenger, intake: Intake,
                     clock: Clock) -> Dispatcher:
    """Данные диспетчера — зависимости обработчиков по имени параметра (задачи 6, 17, 18)."""
    dispatcher = Dispatcher()
    dispatcher.workflow_data.update(settings=settings, texts=TEXTS, brand=BRAND, users=users, repo=repo,
                                    messenger=messenger, intake=intake, clock=clock)
    dispatcher.message.filter(F.chat.type == ChatType.PRIVATE)  # только личка (ТЗ, Р6)
    dispatcher.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    dispatcher.include_router(private_chats(settings, users, messenger))
    return dispatcher


def private_chats(settings: Settings, users: Users, messenger: Messenger) -> Router:
    """Частота и режим до запуска — внешние слои этого роутера. На диспетчере они сработали бы раньше фильтра лички."""
    router = Router(name="private_chats")
    gate = OpenGate(settings, closed_answer(users, messenger))
    send_notice = _throttle_notice_sender(messenger)
    for observer, interval in ((router.message, MESSAGE_INTERVAL_SECONDS),
                               (router.callback_query, BUTTON_INTERVAL_SECONDS)):
        observer.outer_middleware(ThrottleMiddleware(interval, throttle_notice, send_notice))
        observer.outer_middleware(gate)
    router.include_routers(commands.router, admin.router, handlers.router)
    return router


def throttle_notice(language_code: str | None) -> str:
    # Поправка 2 к задаче 19: язык выбранный через /lang (Р11) здесь недоступен без запроса к базе на каждое
    # частое нажатие — тот самый случай, где брифовый вариант (по language_code Telegram) осознанно оставлен;
    # подробности и обоснование — в отчёте задачи 19.
    return TEXTS.get(detect_lang(language_code), "throttled")


def _throttle_notice_sender(messenger: Messenger) -> Callable[[int, str], Awaitable[None]]:
    """send_notice для ThrottleMiddleware (поправка 1 к задаче 19): то же rich-сообщение с шапкой (ТЗ, 7.1),
    доставка best-effort — предупреждение о частоте не должно ронять обработку из-за DeliveryFailed."""
    async def send(chat_id: int, text: str) -> None:
        with contextlib.suppress(DeliveryFailed):
            await messenger.send(chat_id, commands.simple_message(BRAND, text))
    return send


def closed_answer(users: Users, messenger: Messenger) -> Callable[[TelegramObject], Awaitable[None]]:
    """Ответ до запуска (ТЗ, Р4): человека записываем и говорим, что бот скоро откроется."""
    async def answer(event: TelegramObject) -> None:
        user = await users.touch(event.from_user.id, event.from_user.language_code)
        text = TEXTS.get(user.lang, "not_open_yet")
        with contextlib.suppress(TelegramAPIError, DeliveryFailed):
            if isinstance(event, CallbackQuery):
                await event.answer(text)
            else:
                await messenger.send(event.chat.id, commands.simple_message(BRAND, text))
    return answer


async def close_interrupted(repo: ChecksRepo, messenger: Messenger) -> None:
    """После перезапуска незаконченные проверки не повторяются: одна уронила бота — повтор уронит снова (ТЗ, Л5)."""
    for item in await best_effort(repo.interrupt_unfinished(), "незаконченные проверки", []):
        message = replies.failure(TEXTS, item.lang, BRAND, INTERRUPTED)
        with contextlib.suppress(DeliveryFailed):
            await edit_or_send(messenger, item.chat_id, item.message_id, message)


async def update_home_ip(guard: AddressGuard, http: aiohttp.ClientSession, notifier: Notifier) -> None:
    address = await fetch_home_ip(http)
    if address:
        guard.home_ip = address
    elif guard.home_ip is None:
        await notifier.notify("home_ip", "notify_home_ip", DAY)


async def refresh_home_ip(guard: AddressGuard, http: aiohttp.ClientSession, notifier: Notifier) -> None:
    while True:
        await asyncio.sleep(HOME_IP_REFRESH_SECONDS)
        await update_home_ip(guard, http, notifier)


async def daily_backups(engine: AsyncEngine, backup_dir: Path, clock: Clock) -> None:
    """Копия за день — одна: если бот падает и поднимается, хорошая утренняя копия не затирается."""
    while True:
        day = clock.now().date()
        if not daily_backup_path(backup_dir, day).exists():
            await best_effort(backup_daily(engine, backup_dir, day), "суточная копия базы", None)
        await asyncio.sleep(BACKUP_CHECK_SECONDS)


async def run(settings: Settings) -> None:
    clock = SystemClock()
    parts = await build(settings, clock)
    await close_interrupted(parts.repo, parts.messenger)
    await update_home_ip(parts.guard, parts.http, parts.notifier)  # адрес дома — до первой проверки
    await commands.setup_commands(parts.bot, TEXTS, settings.admin_id, ADMIN_COMMANDS)
    await parts.bot.delete_webhook(drop_pending_updates=False)  # присланное, пока бот лежал, — ответить (ТЗ, Л6)
    background = _start_background(parts, settings, clock)
    try:
        await parts.dispatcher.start_polling(parts.bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        await _shutdown(parts, background)


def _start_background(parts: Parts, settings: Settings, clock: Clock) -> list[asyncio.Task]:
    parts.heartbeat.beat(EVENT_LOOP)
    parts.heartbeat.beat(POLLING)
    start_watchdog(parts.heartbeat)
    parts.queue.start()
    backup_dir = settings.data_dir / BACKUP_DIR_NAME
    return [asyncio.create_task(loop_pulse(parts.heartbeat), name="пульс"),
            asyncio.create_task(refresh_home_ip(parts.guard, parts.http, parts.notifier), name="адрес дома"),
            asyncio.create_task(daily_backups(parts.engine, backup_dir, clock), name="копии базы")]


async def _shutdown(parts: Parts, background: list[asyncio.Task]) -> None:
    for task in background:
        task.cancel()
    await asyncio.gather(*background, return_exceptions=True)
    await parts.queue.stop()
    await parts.http.close()
    await parts.engine.dispose()
