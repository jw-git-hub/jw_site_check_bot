from urllib.parse import unquote

from bot.core import rich


def test_header_is_a_banner_animation_block_labelled_by_language():
    """Шапка — блок-анимация полосы с мигающим «_» (задача 33d): файл и file_id подставляет мессенджер, здесь
    только метка языка — rich.py ничего не знает про файлы (ТЗ, 7.1)."""
    assert rich.header("ru") == {"type": "animation", "animation": {"type": "animation", "media": "banner:ru"}}
    assert rich.header("en") == {"type": "animation", "animation": {"type": "animation", "media": "banner:en"}}


def test_photo_is_a_plain_picture_block():
    """Снимок первого экрана — по-прежнему картинкой, не анимацией (ТЗ, 5.9)."""
    assert rich.photo("file123") == {"type": "photo", "photo": {"type": "photo", "media": "file123"}}


def test_footer_links_site_and_username_in_monospace():
    parts = rich.footer()["text"]
    assert parts[0] == {"type": "url", "url": "https://jw-dev.pro", "text": {"type": "code", "text": "jw-dev.pro"}}
    assert parts[2]["url"] == "https://t.me/jw_dev_pro"


def test_button_url_and_callback_keep_style():
    assert rich.button_url("A", "https://a.example") == {"text": "A", "url": "https://a.example"}
    assert rich.button_callback("B", "again", "primary") == {"text": "B", "callback_data": "again",
                                                             "style": "primary"}


def test_keyboard_puts_one_button_per_row():
    a, b = rich.button_url("A", "https://a.example"), rich.button_callback("B", "again")
    assert rich.keyboard(a, b) == {"inline_keyboard": [[a], [b]]}


def test_table_marks_first_row_and_aligns_every_cell():
    cells = rich.table([["Что", "Сколько"], ["LCP", "1,4 с"]])["cells"]
    assert cells[0][0]["is_header"] is True
    assert "is_header" not in cells[1][0]
    assert all(cell["align"] == "left" and cell["valign"] == "top" for row in cells for cell in row)


def test_blockquote_wraps_blocks_without_a_marker():
    """Факты цитатой (задача 33c) — маркер «> » уходит в «Что поправить», здесь его нет."""
    fact = rich.paragraph("главное на экране — через 1,4 секунды")
    assert rich.blockquote([fact]) == {"type": "blockquote", "blocks": [fact]}


def test_pill_is_a_button_rich_text_with_callback_data_and_style():
    assert rich.pill("хорошо", "grade:good", rich.STYLE_SUCCESS) == {
        "type": "button", "button": {"text": "хорошо", "callback_data": "grade:good", "style": "success"}}


def test_pill_without_style_omits_the_field():
    """«Не удалось проверить» — без style (задача 33c, ТЗ 7.1)."""
    assert rich.pill("не удалось проверить", "grade:unknown") == {
        "type": "button", "button": {"text": "не удалось проверить", "callback_data": "grade:unknown"}}


def test_dm_link_encodes_prefilled_text():
    text = "Пришёл из проверки сайта: пример.рф"
    url = rich.dm_link("jw_dev_pro", text)
    assert url.startswith("https://t.me/jw_dev_pro?text=")
    assert unquote(url.split("=", 1)[1]) == text
