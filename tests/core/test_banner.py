from aiogram.types import Animation, Chat, FSInputFile, Message, RichBlockAnimation, RichMessage

from bot.core.banner import BANNER_FILES, BannerCache
from tests.fakes import FAKE_NOW

RU_BANNER = {"blocks": [{"type": "animation", "animation": {"type": "animation", "media": "banner:ru"}},
                        {"type": "paragraph", "text": ["привет"]}]}
NO_BANNER = {"blocks": [{"type": "paragraph", "text": ["привет"]}]}
MP4_SIGNATURE = b"ftyp"
MP4_SIGNATURE_OFFSET = 4  # подпись начинается после 4 байт длины первого чанка (задача 33d)
MAX_BANNER_BYTES = 100 * 1024


def animation_message(file_id: str) -> Message:
    clip = Animation(file_id=file_id, file_unique_id=file_id, width=1600, height=400, duration=1)
    rich_message = RichMessage(blocks=[RichBlockAnimation(animation=clip)])
    return Message(message_id=7, date=FAKE_NOW, chat=Chat(id=1, type="private"), rich_message=rich_message)


def test_message_without_banner_block_is_returned_unchanged():
    cache = BannerCache()
    assert cache.resolve(NO_BANNER) == NO_BANNER


def test_first_resolve_substitutes_the_banner_file_for_the_message_language():
    cache = BannerCache()
    resolved = cache.resolve(RU_BANNER)
    media = resolved["blocks"][0]["animation"]["media"]
    assert isinstance(media, FSInputFile)
    assert BANNER_FILES["ru"].samefile(media.path)


def test_resolve_does_not_mutate_the_caller_dict():
    cache = BannerCache()
    original = {"blocks": [dict(RU_BANNER["blocks"][0]), dict(RU_BANNER["blocks"][1])]}
    cache.resolve(original)
    assert original["blocks"][0]["animation"]["media"] == "banner:ru"


def test_remember_then_resolve_reuses_the_file_id_instead_of_the_file():
    cache = BannerCache()
    cache.remember(RU_BANNER, animation_message("file123"))
    resolved = cache.resolve(RU_BANNER)
    assert resolved["blocks"][0]["animation"]["media"] == "file123"


def test_remember_ignores_a_response_without_rich_message():
    cache = BannerCache()
    cache.remember(RU_BANNER, Message(message_id=1, date=FAKE_NOW, chat=Chat(id=1, type="private")))
    assert isinstance(cache.resolve(RU_BANNER)["blocks"][0]["animation"]["media"], FSInputFile)


def test_remember_ignores_a_boolean_response():
    """editMessageText может ответить True вместо Message (инлайновые сообщения) — не должно падать."""
    cache = BannerCache()
    cache.remember(RU_BANNER, True)
    assert isinstance(cache.resolve(RU_BANNER)["blocks"][0]["animation"]["media"], FSInputFile)


def test_remember_ignores_messages_without_a_banner_label():
    cache = BannerCache()
    cache.remember(NO_BANNER, animation_message("large"))
    assert cache.resolve(RU_BANNER)["blocks"][0]["animation"]["media"] != "large"


def test_banner_files_exist_are_mp4_and_stay_small():
    """Сигнатура MP4 — `ftyp` после длины первого чанка; сама полоса — единственный источник размера (задача 33d)."""
    for path in BANNER_FILES.values():
        assert path.is_file()
        with open(path, "rb") as file:
            header = file.read(MP4_SIGNATURE_OFFSET + len(MP4_SIGNATURE))
        assert header[MP4_SIGNATURE_OFFSET:] == MP4_SIGNATURE
        assert path.stat().st_size < MAX_BANNER_BYTES
