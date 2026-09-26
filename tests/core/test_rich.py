from urllib.parse import unquote

from bot.core import rich


def test_header_is_monospace_console_line():
    assert rich.header("site-check") == {"type": "paragraph", "text": [{"type": "code", "text": ">jw ~/site-check_"}]}


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


def test_dm_link_encodes_prefilled_text():
    text = "Пришёл из проверки сайта: пример.рф"
    url = rich.dm_link("jw_dev_pro", text)
    assert url.startswith("https://t.me/jw_dev_pro?text=")
    assert unquote(url.split("=", 1)[1]) == text
