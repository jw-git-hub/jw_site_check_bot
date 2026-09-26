# Живая обложка описания (экран «Что умеет этот бот?») @jw_site_check_bot —
# по системе экосистемы (_avatars/_src/cover.py): 640×360, MP4 H.264 yuv420p без звука,
# ровно 3 оборота кольца (7,8 с) для бесшовного повтора; рядом GIF-запаска и PNG.
# Иконка — из icon.py, подпись — «сайт → отчёт»: связь с проверкой сайта — словом, без логотипа.
# Запуск: python3 cover.py [имя без расширения]
import cv2, numpy as np, subprocess, sys
from PIL import Image, ImageDraw, ImageFont
from icon import ELEMENTS, SW as SW800

W, H = 640, 360; S = 4
big = cv2.imread('bg.png')
bg = cv2.resize(big[175:625, 0:800], (W, H), interpolation=cv2.INTER_AREA).astype(np.float32)
TEAL = np.array([172, 182, 77], np.float32); DIM = np.array([60, 62, 34], np.float32)
ICON = np.array([164, 173, 73], np.float32)
k = 0.42; CX, CY = 320, 152
P = lambda x, y: ((CX + (x - 400) * k) * S, (CY + (y - 400) * k) * S)   # 800-пространство → обложка
SW = SW800 * k; R = 250 * k


def layer(fn):
    im = Image.new('L', (W * S, H * S), 0); d = ImageDraw.Draw(im); fn(d)
    return np.asarray(im.resize((W, H), Image.LANCZOS), np.float32) / 255.


ring = layer(lambda d: d.ellipse([(CX - R - SW / 2) * S, (CY - R - SW / 2) * S, (CX + R + SW / 2) * S,
                                  (CY + R + SW / 2) * S], outline=255, width=int(SW * S)))
elems = [layer(lambda d, f=f: f(d, P, SW * S)) for f in ELEMENTS]

# подпись: «что на входе → что на выходе», моноширинный серый, стрелка — акцентом
CAPTION = ('сайт', '→', 'отчёт')
f = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', int(20 * S))
capA = Image.new('L', (W * S, H * S), 0); capB = Image.new('L', (W * S, H * S), 0)
da = ImageDraw.Draw(capA); db = ImageDraw.Draw(capB)
parts = [CAPTION[0] + ' ', CAPTION[1], ' ' + CAPTION[2]]
tw = sum(da.textlength(p, font=f) for p in parts); x = (W * S - tw) / 2; y = 300 * S
for i, p in enumerate(parts):
    (db if i == 1 else da).text((x, y), p, font=f, fill=255); x += da.textlength(p, font=f)
cap = np.asarray(capA.resize((W, H), Image.LANCZOS), np.float32) / 255.
arr = np.asarray(capB.resize((W, H), Image.LANCZOS), np.float32) / 255.
CAPC = np.array([128, 120, 112], np.float32)

yy, xx = np.mgrid[0:H, 0:W]; ang = np.degrees(np.arctan2(xx - CX, -(yy - CY))) % 360
CYC = 2.6; centers = np.linspace(0.45, 1.6, len(elems))


def ring_frac(t): return min(1.0, 0.28 + 0.72 * (t % CYC) / 1.6)


def eb(i, t):
    p = t % CYC; b = 1.0
    for o in (-CYC, 0, CYC):
        dt = abs(p + o - centers[i])
        if dt < 0.45: b = min(b, 1 - 0.24 * 0.5 * (1 + np.cos(np.pi * dt / 0.45)))
    return b


def img(t, still=False):
    im = bg.copy(); fr = ring_frac(t)
    br = np.ones_like(ang) if fr >= 1 else np.clip((fr * 360 - ang) * np.pi * R / 180 + 0.5, 0, 1)
    col = DIM * (1 - br[..., None]) + TEAL * br[..., None]
    im = im * (1 - ring[..., None]) + col * ring[..., None]
    for i, e in enumerate(elems):
        c = ICON * (1.0 if still else eb(i, t)); im = im * (1 - e[..., None]) + c * e[..., None]
    im = im * (1 - cap[..., None]) + CAPC * cap[..., None]
    im = im * (1 - arr[..., None]) + TEAL * 0.85 * arr[..., None]
    return im.clip(0, 255).astype(np.uint8)


name = sys.argv[1] if len(sys.argv) > 1 else 'jw-site-check-cover-640x360'
cv2.imwrite(name + '.png', img(0, True))
fps = 25
p = subprocess.Popen(['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{W}x{H}', '-r', str(fps),
                      '-i', '-', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-preset', 'slow', '-crf', '28',
                      '-movflags', '+faststart', name + '.mp4'], stdin=subprocess.PIPE)
for i in range(int(round(3 * CYC * fps))):
    p.stdin.write(img(i / fps).tobytes())
p.stdin.close(); p.wait()
# GIF-запаска из того же MP4: своя палитра, 15 кадров/с
subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', name + '.mp4', '-vf',
                'fps=15,split[a][b];[a]palettegen=max_colors=64[p];[b][p]paletteuse=dither=bayer:bayer_scale=4',
                '-loop', '0', name + '.gif'], check=True)
