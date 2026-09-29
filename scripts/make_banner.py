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

Шапка всех сообщений — анимация полосы с мигающим «_» на конце имени раздела, как курсор в конце консоли
(решение владельца 29.09.2026, задача 33d): два кадра тем же кодом рисования — 0,5 с с «_» (тем же цветом,
что имя), 0,5 с без «_» (кадр без «_» байт в байт равен обычной полосе); первый кадр — с «_», чтобы при
выключенном автозапуске GIF человек видел полосу с курсором, как в консоли. Кадры собираются в MP4 через
ffmpeg (подпроцесс, только для этой разовой генерации — в образ бота ffmpeg не нужен).

    scripts/test.sh не нужен, Pillow в requirements не добавлен, нужен ffmpeg (на Маке — `brew install ffmpeg`);
    запуск разовый, из папки проекта, кэши — вне её:
    PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pillow==12.3.0 python scripts/make_banner.py \
        --name проверка-сайта --blink --out bot/assets/banner-ru.mp4
    PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pillow==12.3.0 python scripts/make_banner.py \
        --name site-check --blink --out bot/assets/banner-en.mp4
"""
import argparse
import math
import subprocess
import tempfile
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
BLINK_CURSOR = "_"           # мигающий курсор в конце имени раздела (задача 33d)
BLINK_INPUT_FPS = 2          # каждый кадр виден 0,5 с
BLINK_OUTPUT_FPS = 10        # проба 29.09.2026 (ТЗ, раздел 20)
BLINK_FRAME_PATTERN = "f%d.png"
CURSOR_FRAME_NAME = "f0.png"    # с «_» — показывается первым (ТЗ, 7.1)
PLAIN_FRAME_NAME = "f1.png"     # без «_» — байт в байт как обычная полоса


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


def _save_blink_frames(prefix: str, name: str, directory: Path) -> None:
    """Два кадра тем же кодом рисования, что и обычная полоса — код рисования не меняется (задача 33d)."""
    render(prefix, name + BLINK_CURSOR).save(directory / CURSOR_FRAME_NAME, "PNG", optimize=True)
    render(prefix, name).save(directory / PLAIN_FRAME_NAME, "PNG", optimize=True)


def _encode_blink_video(directory: Path, out: Path) -> None:
    """Собирает MP4 из двух кадров через ffmpeg — команда пробы 29.09.2026 (ТЗ, раздел 20)."""
    command = ["ffmpeg", "-y", "-framerate", str(BLINK_INPUT_FPS), "-i", BLINK_FRAME_PATTERN,
              "-vf", f"fps={BLINK_OUTPUT_FPS},format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "23",
              "-tune", "stillimage", "-movflags", "+faststart", "-an", str(out.resolve())]
    subprocess.run(command, cwd=directory, check=True, capture_output=True)


def render_blink_animation(prefix: str, name: str, out: Path) -> None:
    """Полоса с мигающим «_» — MP4 H.264, 1 с, первый кадр — с курсором (ТЗ, 7.1, задача 33d)."""
    with tempfile.TemporaryDirectory() as directory:
        _save_blink_frames(prefix, name, Path(directory))
        _encode_blink_video(Path(directory), out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Полоса-обложка рубрики")
    parser.add_argument("--prefix", default="~/", help="начало пути — бирюзовым")
    parser.add_argument("--name", default="проверка-сайта", help="имя раздела — светлым")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--blink", action="store_true", help="анимация полосы с мигающим «_» — MP4 через ffmpeg")
    args = parser.parse_args()
    if args.blink:
        render_blink_animation(args.prefix, args.name, args.out)
        print(f"анимация полосы: {args.out}")
        return
    render(args.prefix, args.name).save(args.out, "PNG", optimize=True)
    print(f"полоса: {args.out}")


if __name__ == "__main__":
    main()
