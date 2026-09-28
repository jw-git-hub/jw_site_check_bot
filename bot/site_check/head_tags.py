"""<head> страницы → заголовок, описание, og-теги, canonical, язык (ТЗ, 5.6–5.7). Чистый разбор, без сети.

Мессенджер не выполняет JavaScript: превью строится по статическому HTML — ровно по тому, что читает этот модуль.
Разбор кончается на </head> или <body>: <title> внутри svg в теле страницы — не заголовок.
"""
from dataclasses import dataclass
from html.parser import HTMLParser

from bot.site_check.audits import WHITESPACE

OG_IMAGE_KEYS = ("og:image", "og:image:url", "og:image:secure_url")
TWITTER_IMAGE_KEYS = ("twitter:image", "twitter:image:src")
CANONICAL_REL = "canonical"


@dataclass(frozen=True)
class HeadTags:
    title: str | None = None
    description: str | None = None
    og_title: str | None = None
    og_description: str | None = None
    og_image: str | None = None      # как в HTML, без преобразования в полный адрес (ТЗ, 5.7)
    twitter_title: str | None = None
    twitter_image: str | None = None
    canonical: str | None = None
    lang: str | None = None
    complete: bool = False           # дошли до </head> или <body> — отсутствие тега можно утверждать

    @property
    def preview_title(self) -> str | None:
        """Как берёт мессенджер (ТЗ, 5.7): og:title, иначе twitter:title, иначе <title>."""
        return self.og_title or self.twitter_title or self.title

    @property
    def preview_description(self) -> str | None:
        return self.og_description or self.description

    @property
    def preview_image(self) -> str | None:
        return self.og_image or self.twitter_image


def parse_head(html: str) -> HeadTags:
    parser = _HeadParser()
    parser.feed(html)
    parser.close()
    return parser.tags()


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    return WHITESPACE.sub(" ", value).strip() or None


def _first(meta: dict[str, str], keys: tuple[str, ...]) -> str | None:
    return next((meta[key] for key in keys if key in meta), None)


class _HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._meta: dict[str, str] = {}
        self._title_parts: list[str] | None = None
        self._title: str | None = None
        self._canonical: str | None = None
        self._lang: str | None = None
        self._complete = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._complete:
            return
        values = {name.lower(): value or "" for name, value in attrs}
        if tag == "body":
            self._complete = True
        elif tag == "html":
            self._lang = self._lang or _clean(values.get("lang"))
        elif tag == "title" and self._title is None:
            self._title_parts = []
        elif tag == "meta":
            self._remember_meta(values)
        elif tag == "link":
            self._remember_canonical(values)

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self._complete = True
        elif tag == "title" and self._title_parts is not None:
            self._title = _clean("".join(self._title_parts))
            self._title_parts = None

    def handle_data(self, data: str) -> None:
        if self._title_parts is not None:
            self._title_parts.append(data)

    def _remember_meta(self, values: dict[str, str]) -> None:
        key = (values.get("property") or values.get("name") or "").strip().lower()
        content = _clean(values.get("content"))
        if key and content and key not in self._meta:
            self._meta[key] = content

    def _remember_canonical(self, values: dict[str, str]) -> None:
        if self._canonical is None and CANONICAL_REL in values.get("rel", "").lower().split():
            self._canonical = _clean(values.get("href"))

    def tags(self) -> HeadTags:
        title = self._title if self._title_parts is None else _clean("".join(self._title_parts))
        meta = self._meta
        return HeadTags(title=title, description=meta.get("description"), og_title=meta.get("og:title"),
                        og_description=meta.get("og:description"), og_image=_first(meta, OG_IMAGE_KEYS),
                        twitter_title=meta.get("twitter:title"), twitter_image=_first(meta, TWITTER_IMAGE_KEYS),
                        canonical=self._canonical, lang=self._lang, complete=self._complete)
