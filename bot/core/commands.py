"""Общие команды экосистемы (ТЗ, раздел 8): /start <метка>, /lang, /about, /order и меню команд.

Имя, описания, аватар и обложку бота код не трогает — только меню команд (setMyCommands).
"""
import contextlib
from dataclasses import dataclass

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import BotCommand, BotCommandScopeChat, CallbackQuery, Message

from bot.core import rich
from bot.core.access import is_open_for
from bot.core.config import CoreSettings
from bot.core.i18n import RUSSIAN_CODES, Lang, Texts
from bot.core.messenger import Messenger, edit_or_send
from bot.core.stats import best_effort
from bot.core.users import Users, parse_label

LANG_CALLBACK_PREFIX = "lang:"
LANGUAGES: dict[str, str] = {"ru": "Русский", "en": "English"}
PUBLIC_COMMANDS = ("start", "lang", "about", "order")
DEFAULT_MENU_LANG: Lang = "en"
RUSSIAN_MENU_LANG: Lang = "ru"


@dataclass(frozen=True)
class Brand:
    dm_username: str  # личка разработчика
    channel_url: str
    site_url: str


router = Router(name="core_commands")


def simple_message(texts: Texts, lang: Lang, text: str) -> dict:
    return rich.message([rich.header(texts.get(lang, "header_section")), rich.paragraph(text)])


def lang_choice(texts: Texts, lang: Lang) -> tuple[dict, dict]:
    buttons = [rich.button_callback(name, LANG_CALLBACK_PREFIX + code) for code, name in LANGUAGES.items()]
    message = rich.message([rich.header(texts.get(lang, "header_section")),
                            rich.paragraph(texts.get(lang, "lang_choose"))])
    return message, rich.keyboard(*buttons)


def about_message(brand: Brand, texts: Texts, lang: Lang) -> tuple[dict, dict]:
    message = rich.message([rich.header(texts.get(lang, "header_section")), rich.paragraph(texts.get(lang, "about")),
                            rich.divider(), rich.footer()])
    keyboard = rich.keyboard(rich.button_url(texts.get(lang, "about_site_button"), brand.site_url),
                             rich.button_url(texts.get(lang, "channel_button"), brand.channel_url))
    return message, keyboard


def order_message(brand: Brand, texts: Texts, lang: Lang) -> tuple[dict, dict]:
    link = rich.dm_link(brand.dm_username, texts.get(lang, "order_prefill"))
    message = rich.message([rich.header(texts.get(lang, "header_section")), rich.paragraph(texts.get(lang, "order")),
                            rich.divider(), rich.footer()])
    keyboard = rich.keyboard(rich.button_url(texts.get(lang, "order_button"), link, rich.STYLE_PRIMARY))
    return message, keyboard


@router.message(CommandStart())
async def on_start(message: Message, command: CommandObject, users: Users, messenger: Messenger, texts: Texts,
                   settings: CoreSettings) -> None:
    user = await users.touch(message.from_user.id, message.from_user.language_code, parse_label(command.args))
    key = "welcome" if is_open_for(settings, user.user_id) else "not_open_yet"
    await messenger.send(message.chat.id, simple_message(texts, user.lang, texts.get(user.lang, key)))


@router.message(Command("lang"))
async def on_lang(message: Message, users: Users, messenger: Messenger, texts: Texts) -> None:
    user = await users.touch(message.from_user.id, message.from_user.language_code)
    await messenger.send(message.chat.id, *lang_choice(texts, user.lang))


@router.callback_query(F.data.startswith(LANG_CALLBACK_PREFIX))
async def on_lang_chosen(callback: CallbackQuery, users: Users, messenger: Messenger, texts: Texts) -> None:
    lang = callback.data.removeprefix(LANG_CALLBACK_PREFIX)
    # Устаревшее нажатие («query is too old») не должно ронять обработчик выбора языка.
    with contextlib.suppress(TelegramAPIError):
        await callback.answer()
    if lang not in LANGUAGES:
        return
    await best_effort(users.set_lang(callback.from_user.id, lang), "выбор языка", None)
    chat_id, message_id = callback.message.chat.id, callback.message.message_id
    await edit_or_send(messenger, chat_id, message_id, simple_message(texts, lang, texts.get(lang, "lang_done")))


@router.message(Command("about"))
async def on_about(message: Message, users: Users, messenger: Messenger, texts: Texts, brand: Brand) -> None:
    user = await users.touch(message.from_user.id, message.from_user.language_code)
    await messenger.send(message.chat.id, *about_message(brand, texts, user.lang))


@router.message(Command("order"))
async def on_order(message: Message, users: Users, messenger: Messenger, texts: Texts, brand: Brand) -> None:
    user = await users.touch(message.from_user.id, message.from_user.language_code)
    await messenger.send(message.chat.id, *order_message(brand, texts, user.lang))


async def setup_commands(bot: Bot, texts: Texts, admin_id: int, admin_commands: tuple[str, ...]) -> None:
    """Меню на en для всех, на ru — для ru, uk, be, kk; владельцу — ещё свои команды."""
    await bot.set_my_commands(_menu(texts, DEFAULT_MENU_LANG, PUBLIC_COMMANDS))
    for code in sorted(RUSSIAN_CODES):
        await bot.set_my_commands(_menu(texts, RUSSIAN_MENU_LANG, PUBLIC_COMMANDS), language_code=code)
    owner_menu = _menu(texts, RUSSIAN_MENU_LANG, PUBLIC_COMMANDS + admin_commands)
    await bot.set_my_commands(owner_menu, scope=BotCommandScopeChat(chat_id=admin_id))


def _menu(texts: Texts, lang: Lang, names: tuple[str, ...]) -> list[BotCommand]:
    return [BotCommand(command=name, description=texts.get(lang, f"command_{name}")) for name in names]
