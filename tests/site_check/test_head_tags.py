from bot.site_check.head_tags import parse_head

JW_DEV_PRO_TITLE = "Сайты, боты и автоматизация для малого бизнеса — jw-dev.pro"
JW_DEV_PRO_HEAD = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>{JW_DEV_PRO_TITLE}</title>
<meta name="description" content="Собираю сайты, Telegram-ботов и автоматизацию для небольшого бизнеса.">
<meta property="og:title" content="{JW_DEV_PRO_TITLE}">
<meta property="og:image" content="https://jw-dev.pro/og/jw-dev-pro.jpg">
<meta name="robots" content="noindex">
<link rel="canonical" href="https://jw-dev.pro/">
<script>var s = "<meta property='og:image' content='wrong.png'>";</script>
</head><body><svg><title>Иконка</title></svg>"""


def test_jw_dev_pro_head():
    tags = parse_head(JW_DEV_PRO_HEAD)
    assert (tags.title, tags.og_title, tags.preview_title) == (JW_DEV_PRO_TITLE,) * 3
    assert tags.og_image == tags.preview_image == "https://jw-dev.pro/og/jw-dev-pro.jpg"
    assert tags.description.startswith("Собираю сайты")
    assert (tags.canonical, tags.lang, tags.complete) == ("https://jw-dev.pro/", "ru", True)


def test_wix_head_without_image_or_description_falls_back_to_title():
    tags = parse_head('<html lang="ru"><head><title>Главная | Mysite</title></head><body>')
    assert (tags.preview_title, tags.preview_image, tags.preview_description) == ("Главная | Mysite", None, None)


def test_title_whitespace_and_entities_are_cleaned():
    tags = parse_head("<head><title>\n  Кафе &laquo;Парижская&raquo;\n  | Вологда </title></head>")
    assert tags.title == "Кафе «Парижская» | Вологда"


def test_first_non_empty_value_wins():
    html = ('<head><meta property="og:image" content=" "><meta property="og:image" content="/a.png">'
            '<meta property="og:image" content="/b.png"></head>')
    assert parse_head(html).og_image == "/a.png"


def test_image_variants_and_twitter_fallbacks():
    secure = parse_head('<head><meta property="og:image:secure_url" content="https://cdn.example/s.jpg"></head>')
    twitter = parse_head('<head><meta name="twitter:image:src" content="https://cdn.example/t.jpg">'
                         '<meta name="twitter:title" content="Твиттер"></head>')
    assert secure.preview_image == "https://cdn.example/s.jpg"
    assert (twitter.preview_image, twitter.preview_title) == ("https://cdn.example/t.jpg", "Твиттер")


def test_head_cut_before_its_end_is_incomplete_but_keeps_what_was_read():
    tags = parse_head("<html><head><title>Большой сайт</title><style>" + "a{}" * 1000)
    assert (tags.title, tags.complete) == ("Большой сайт", False)


def test_body_start_ends_the_head():
    tags = parse_head("<head><title>A</title><body><meta property='og:image' content='x.png'>")
    assert (tags.og_image, tags.complete) == (None, True)


def test_canonical_among_rel_values_and_uppercase_names():
    tags = parse_head('<HEAD><LINK REL="alternate canonical" HREF="https://a.example/">'
                      '<META NAME="Description" CONTENT="Описание"></HEAD>')
    assert (tags.canonical, tags.description) == ("https://a.example/", "Описание")


def test_ampersand_in_attribute_is_unescaped():
    tags = parse_head('<head><meta property="og:image" content="https://cdn.example/og.jpg?w=1&amp;h=2"></head>')
    assert tags.og_image == "https://cdn.example/og.jpg?w=1&h=2"


def test_garbage_gives_empty_tags_without_error():
    tags = parse_head("<<<>>><head <title")
    assert (tags.title, tags.preview_image, tags.complete) == (None, None, False)
