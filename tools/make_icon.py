"""Generate the BondScribe app icon: a dark rounded badge (matching the app's
#0e1116 UI) with a white 'B' monogram beside an indigo->cyan equalizer waveform.

Renders at 1024px and saves a multi-size Windows .ico to assets/bondscribe.ico.
Run: .venv/Scripts/python.exe tools/make_icon.py
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

S = 1024  # master canvas size (supersampled, ICO downsamples from this)
OUT = os.path.join(os.path.dirname(__file__), "..", "assets", "bondscribe.ico")

# ---- palette (dark app UI + bright accent gradient) ----
BG_TOP = (20, 25, 33)      # #141921
BG_BOT = (10, 12, 16)      # #0a0c10  (close to app's #0e1116)
INDIGO = (99, 102, 241)    # #6366f1
CYAN = (34, 211, 238)      # #22d3ee
WHITE = (244, 247, 251)


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def load_bold_font(px):
    for name in ("segoeuib.ttf", "arialbd.ttf", "calibrib.ttf"):
        p = os.path.join("C:\\Windows\\Fonts", name)
        if os.path.exists(p):
            return ImageFont.truetype(p, px)
    return ImageFont.load_default()


img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# ---- rounded badge with vertical gradient background ----
margin = int(S * 0.045)
radius = int(S * 0.22)
grad = Image.new("RGBA", (S, S), (0, 0, 0, 0))
gp = grad.load()
for y in range(S):
    c = lerp(BG_TOP, BG_BOT, y / S)
    for x in range(S):
        gp[x, y] = c + (255,)
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle(
    [margin, margin, S - margin, S - margin], radius=radius, fill=255
)
img.paste(grad, (0, 0), mask)

# subtle inner border
ImageDraw.Draw(img).rounded_rectangle(
    [margin, margin, S - margin, S - margin],
    radius=radius, outline=(255, 255, 255, 22), width=max(2, S // 220),
)

# ---- soft cyan glow behind the waveform ----
glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
gd = ImageDraw.Draw(glow)
gd.ellipse([int(S * 0.52), int(S * 0.30), int(S * 0.92), int(S * 0.70)],
           fill=CYAN + (90,))
glow = glow.filter(ImageFilter.GaussianBlur(S // 12))
img = Image.alpha_composite(img, Image.composite(
    glow, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask))
draw = ImageDraw.Draw(img)

# ---- 'B' monogram (left) ----
font = load_bold_font(int(S * 0.62))
text = "B"
bbox = draw.textbbox((0, 0), text, font=font)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
bx = int(S * 0.30) - tw // 2 - bbox[0]
by = S // 2 - th // 2 - bbox[1]
# faint shadow for depth
draw.text((bx + S // 120, by + S // 120), text, font=font, fill=(0, 0, 0, 110))
draw.text((bx, by), text, font=font, fill=WHITE + (255,))

# ---- equalizer waveform (right): rounded bars, indigo->cyan across ----
bars = [0.34, 0.62, 0.92, 0.66, 0.40]  # relative heights -> audio pulse
bw = int(S * 0.058)
gap = int(S * 0.030)
total = len(bars) * bw + (len(bars) - 1) * gap
x0 = int(S * 0.50)
cy = S // 2
cap = bw // 2
for i, h in enumerate(bars):
    bh = int(h * S * 0.46)
    x = x0 + i * (bw + gap)
    col = lerp(INDIGO, CYAN, i / (len(bars) - 1))
    draw.rounded_rectangle(
        [x, cy - bh // 2, x + bw, cy + bh // 2], radius=cap, fill=col + (255,)
    )

# ---- export multi-size .ico (used by electron-builder for the .exe) ----
sizes = [256, 128, 64, 48, 32, 24, 16]
os.makedirs(os.path.dirname(OUT), exist_ok=True)
img.save(OUT, format="ICO", sizes=[(s, s) for s in sizes])

# ---- export PNG copies ----
# Electron's Windows image loader renders blank from ICOs that contain a
# PNG-compressed 256px entry, which is what shows up as a white box on the
# taskbar. A plain PNG loads reliably, so the BrowserWindow uses this at runtime.
png256 = img.resize((256, 256), Image.LANCZOS)
png256.save(os.path.join(os.path.dirname(OUT), "bondscribe.png"))
png256.save(os.path.join(os.path.dirname(OUT), "bondscribe-preview.png"))
# bundle a copy next to main.js so __dirname/icon.png always resolves,
# packaged or not (no dependency on the repo layout).
png256.save(os.path.join(
    os.path.dirname(__file__), "..", "desktop", "icon.png"))
print("wrote", os.path.abspath(OUT), "+ bondscribe.png + desktop/icon.png")
