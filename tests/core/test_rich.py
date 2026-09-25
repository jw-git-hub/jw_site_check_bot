from urllib.parse import unquote

from bot.core import rich


def test_header_is_monospace_console_line():
    assert rich.header("site-check") == {"type": "paragraph", "text": [{"type": "code", "text": ">jw_ ~/site-check"}]}


def test_footer_links_site_and_username_in_monospace():
    parts = rich.footer()["text"]
    assert parts[0] == {"type": "url", "url": "https://jw-dev.pro", "text": {"type": "code", "text": "jw-dev.pro"}}
    assert parts[2]["url"] == "https://t.me/jw_dev_pro"


def test_pills_are_separated_and_keep_style():
    block = rich.pills(rich.pill_url("A", "https://a.example"), rich.pill_callback("B", "again", "primary"))
    assert block["text"][1] == rich.PILL_GAP
    assert block["text"][2]["button"] == {"text": "B", "callback_data": "again", "style": "primary"}
    assert "style" not in block["text"][0]["button"]


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
