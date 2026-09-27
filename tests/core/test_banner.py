import struct

from aiogram.types import Chat, FSInputFile, Message, PhotoSize, RichBlockPhoto, RichMessage

from bot.core.banner import BANNER_FILES, BannerCache
from tests.fakes import FAKE_NOW

RU_BANNER = {"blocks": [{"type": "photo", "photo": {"type": "photo", "media": "banner:ru"}},
                        {"type": "paragraph", "text": ["привет"]}]}
NO_BANNER = {"blocks": [{"type": "paragraph", "text": ["привет"]}]}
BANNER_WIDTH, BANNER_HEIGHT = 1600, 400
PNG_IHDR_SIZE_OFFSET = 16  # подпись (8) + длина чанка (4) + имя "IHDR" (4) — дальше ширина и высота, по 4 байта


def photo_message(sizes: list[PhotoSize]) -> Message:
    rich_message = RichMessage(blocks=[RichBlockPhoto(photo=sizes)])
    return Message(message_id=7, date=FAKE_NOW, chat=Chat(id=1, type="private"), rich_message=rich_message)


def photo_size(width: int, height: int, file_id: str) -> PhotoSize:
    return PhotoSize(file_id=file_id, file_unique_id=file_id, width=width, height=height)


def test_message_without_banner_block_is_returned_unchanged():
    cache = BannerCache()
    assert cache.resolve(NO_BANNER) == NO_BANNER


def test_first_resolve_substitutes_the_banner_file_for_the_message_language():
    cache = BannerCache()
    resolved = cache.resolve(RU_BANNER)
    media = resolved["blocks"][0]["photo"]["media"]
    assert isinstance(media, FSInputFile)
    assert BANNER_FILES["ru"].samefile(media.path)


def test_resolve_does_not_mutate_the_caller_dict():
    cache = BannerCache()
    original = {"blocks": [dict(RU_BANNER["blocks"][0]), dict(RU_BANNER["blocks"][1])]}
    cache.resolve(original)
    assert original["blocks"][0]["photo"]["media"] == "banner:ru"


def test_remember_then_resolve_reuses_the_file_id_instead_of_the_file():
    cache = BannerCache()
    cache.remember(RU_BANNER, photo_message([photo_size(320, 80, "small"), photo_size(1600, 400, "large")]))
    resolved = cache.resolve(RU_BANNER)
    assert resolved["blocks"][0]["photo"]["media"] == "large"


def test_remember_picks_the_largest_photo_size_by_area():
    cache = BannerCache()
    cache.remember(RU_BANNER, photo_message([photo_size(1600, 400, "big"), photo_size(90, 90, "square-ish")]))
    assert cache.resolve(RU_BANNER)["blocks"][0]["photo"]["media"] == "big"


def test_remember_ignores_a_response_without_rich_message():
    cache = BannerCache()
    cache.remember(RU_BANNER, Message(message_id=1, date=FAKE_NOW, chat=Chat(id=1, type="private")))
    assert isinstance(cache.resolve(RU_BANNER)["blocks"][0]["photo"]["media"], FSInputFile)


def test_remember_ignores_a_boolean_response():
    """editMessageText может ответить True вместо Message (инлайновые сообщения) — не должно падать."""
    cache = BannerCache()
    cache.remember(RU_BANNER, True)
    assert isinstance(cache.resolve(RU_BANNER)["blocks"][0]["photo"]["media"], FSInputFile)


def test_remember_ignores_messages_without_a_banner_label():
    cache = BannerCache()
    cache.remember(NO_BANNER, photo_message([photo_size(1600, 400, "large")]))
    assert cache.resolve(RU_BANNER)["blocks"][0]["photo"]["media"] != "large"


def png_size(path) -> tuple[int, int]:
    """Ширина и высота — из заголовка IHDR (первый чанк любого PNG), без Pillow (задача 23b)."""
    with open(path, "rb") as file:
        header = file.read(PNG_IHDR_SIZE_OFFSET + 8)
    width, height = struct.unpack(">II", header[PNG_IHDR_SIZE_OFFSET:PNG_IHDR_SIZE_OFFSET + 8])
    return width, height


def test_banner_files_exist_and_are_the_right_size():
    for path in BANNER_FILES.values():
        assert path.is_file()
        assert png_size(path) == (BANNER_WIDTH, BANNER_HEIGHT)
