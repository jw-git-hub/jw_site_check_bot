#!/usr/bin/env python3
"""Полоса-обложка шапки — как в боте канала (ТЗ, 7.1; задача 23b): взята без изменений в рисовании из
`../Телеграм бот - jw_dev_pro_channel_bot/scripts/make_banner.py` (тот бот — только чтение, копия здесь своя).
Бренд экосистемы одинаков во всех ботах >jw_, поэтому код полосы не меняется — только докстринг и имя по умолчанию.

Шапка сайта (`.p-bar` в `src/styles/header.css`) — стекло: подложка `--bar-rgb` плотностью .85, сквозь неё размыто
просвечивает фон «Плетение» (`.bg` в `src/styles/base.css`), насыщенность ×1.5, снизу тонкая линия `--line`.
Штриховка и дизер «Плетения» под размытием шапки сливаются в ровный тон, поэтому под стеклом здесь только бархат
и три цветных пятна — те же цвета, прозрачность и место, что в CSS. Пятна стоят в долях полосы: так сайт выглядит
в окне её пропорций. Пиксель сайта — 3 пикселя полосы: Telegram показывает её шириной около 530 точек.

Путь раздела, как первая строка закрепа канала: `~/` бирюзовым #4DB6AC, имя раздела светлым, JetBrains Mono.

    scripts/test.sh не нужен, Pillow в requirements не добавлен — запуск разовый:
    uv run --no-project --with pillow==12.3.0 python scripts/make_banner.py \
        --name проверка-сайта --out bot/assets/banner-ru.png
    uv run --no-project --with pillow==12.3.0 python scripts/make_banner.py \
        --name site-check --out bot/assets/banner-en.png
"""
import argparse
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

FONTS = Path(__file__).resolve().parents[1] / "assets" / "fonts"
WIDTH, HEIGHT = 1600, 400
SITE_PX = 3                          # пикселей полосы на пиксель сайта
VELVET = (15, 11, 19)                # --velvet
GLASS_COLOR = (14, 10, 18)           # --bar-rgb
GLASS_ALPHA = 0.85
GLASS_SATURATE = 1.5                 # backdrop-filter: saturate(1.5)
EDGE_LINE = (255, 255, 255)          # --line: нижняя граница шапки
EDGE_ALPHA = 0.07
ACCENT = (77, 182, 172)              # #4DB6AC, --teal
TEXT_LIGHT = (214, 222, 228)
FONT_SIZE = 104
TEXT_LEFT = 110
TEXT_BASELINE = 238


@dataclass(frozen=True)
class Spot:
    """Пятно radial-gradient сайта: центр и радиусы в долях полосы, прозрачность в центре и где пятно гаснет."""
    color: tuple[int, int, int]
    center: tuple[float, float]
    radii: tuple[float, float]
    alpha: float
    fade_end: float


SPOTS = (  # снизу вверх, как слои CSS
    Spot((240, 98, 146), (0.46, 0.94), (0.60, 0.44), 0.10, 0.74),   # --pink
    Spot((77, 182, 172), (0.82, 0.44), (0.56, 0.44), 0.13, 0.72),   # --teal
    Spot((149, 117, 205), (0.24, 0.16), (0.64, 0.48), 0.17, 0.72),  # --violet
)
SPOT_GRID = 256                      # пятно считается на этой сетке и растягивается до своего эллипса


def _spot_mask(spot: Spot, size: tuple[int, int]) -> Image.Image:
    half = SPOT_GRID / 2
    values = bytearray(SPOT_GRID * SPOT_GRID)
    for y in range(SPOT_GRID):
        for x in range(SPOT_GRID):
            distance = math.hypot(x + 0.5 - half, y + 0.5 - half) / half
            values[y * SPOT_GRID + x] = round(255 * spot.alpha * max(0.0, 1 - distance / spot.fade_end))
    return Image.frombytes("L", (SPOT_GRID, SPOT_GRID), bytes(values)).resize(size, Image.BICUBIC)


def _spot(spot: Spot) -> Image.Image:
    radius_x, radius_y = round(spot.radii[0] * WIDTH), round(spot.radii[1] * HEIGHT)
    left, top = round(spot.center[0] * WIDTH) - radius_x, round(spot.center[1] * HEIGHT) - radius_y
    mask = Image.new("L", (WIDTH, HEIGHT), 0)
    mask.paste(_spot_mask(spot, (2 * radius_x, 2 * radius_y)), (left, top))
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (*spot.color, 0))
    layer.putalpha(mask)
    return layer


def _backdrop() -> Image.Image:
    """Что просвечивает сквозь стекло шапки: бархат и пятна «Плетения», насыщенность как у фильтра шапки."""
    canvas = Image.new("RGBA", (WIDTH, HEIGHT), (*VELVET, 255))
    for spot in SPOTS:
        canvas = Image.alpha_composite(canvas, _spot(spot))
    return ImageEnhance.Color(canvas.convert("RGB")).enhance(GLASS_SATURATE).convert("RGBA")


def _glass() -> Image.Image:
    glass = Image.new("RGBA", (WIDTH, HEIGHT), (*GLASS_COLOR, round(255 * GLASS_ALPHA)))
    canvas = Image.alpha_composite(_backdrop(), glass)
    edge = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    ImageDraw.Draw(edge).rectangle((0, HEIGHT - SITE_PX, WIDTH, HEIGHT), fill=(*EDGE_LINE, round(255 * EDGE_ALPHA)))
    return Image.alpha_composite(canvas, edge)


def _text(draw: ImageDraw.ImageDraw, prefix: str, name: str) -> None:
    bold = ImageFont.truetype(str(FONTS / "JetBrainsMono-Bold.ttf"), FONT_SIZE)
    draw.text((TEXT_LEFT, TEXT_BASELINE), prefix, font=bold, fill=ACCENT, anchor="ls")
    name_left = TEXT_LEFT + draw.textlength(prefix, font=bold)
    draw.text((name_left, TEXT_BASELINE), name, font=bold, fill=TEXT_LIGHT, anchor="ls")


def render(prefix: str, name: str) -> Image.Image:
    canvas = _glass()
    _text(ImageDraw.Draw(canvas), prefix, name)
    return canvas.convert("RGB")


def main() -> None:
    parser = argparse.ArgumentParser(description="Полоса-обложка рубрики")
    parser.add_argument("--prefix", default="~/", help="начало пути — бирюзовым")
    parser.add_argument("--name", default="проверка-сайта", help="имя раздела — светлым")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    render(args.prefix, args.name).save(args.out, "PNG", optimize=True)
    print(f"полоса: {args.out}")


if __name__ == "__main__":
    main()
