"""Animated prayer-times card (GIF): night sky with twinkling stars, a crescent moon, a shooting star and a mosque.

render_gif(title, subtitle, times, next_name=None, countdown="", note="")  -> GIF bytes
    times = [("Fajr", "05:41"), ("Dhuhr", "13:05"), ...]  (empty list = a banner without times)
Only Latin text is drawn (Arabic needs a text-shaping library that a bare host may not have).
"""
import io
import math
import random

from PIL import Image, ImageDraw

from weather_card import Canvas, S, W, font

H = 620
FRAMES = 12
GOLD = (255, 214, 120, 255)


class Layer(Canvas):
    """A transparent drawing layer (mosque, glass cards, texts) that is pasted over the animated sky."""

    def __init__(self, height):
        self.h = height
        self.img = Image.new("RGBA", (W * S, height * S), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    def glass(self, x, y, w, h, r=26, alpha=40, outline=(255, 255, 255, 45), fill=None):
        self.d.rounded_rectangle([x * S, y * S, (x + w) * S, (y + h) * S], r * S,
                                 fill=fill or (255, 255, 255, alpha), outline=outline, width=S)


def _sky():
    c = Canvas(H, (5, 9, 28), (22, 38, 84))
    for i, (r, a) in enumerate(((330, 16), (250, 20), (170, 26), (100, 30))):      # warm glow behind the mosque
        ov = Image.new("RGBA", c.img.size, (0, 0, 0, 0))
        ImageDraw.Draw(ov).ellipse([(450 - r) * S, (H - r * 0.55 - 40) * S, (450 + r) * S, (H + r * 0.55 - 40) * S],
                                   fill=(255, 170, 90, a))
        base = c.img.convert("RGBA")
        base.alpha_composite(ov)
        c.img = base.convert("RGB")
    c.d = ImageDraw.Draw(c.img, "RGBA")
    # crescent moon
    cx, cy, r = 790, 105, 34
    c.circle(cx, cy, r * 1.9, (255, 240, 200, 14))
    c.circle(cx, cy, r * 1.4, (255, 240, 200, 22))
    mask = Image.new("L", c.img.size, 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S], fill=255)
    md.ellipse([(cx + r * 0.42 - r * 0.9) * S, (cy - r * 0.15 - r * 0.9) * S, (cx + r * 0.42 + r * 0.9) * S, (cy - r * 0.15 + r * 0.9) * S], fill=0)
    c.img.paste((252, 242, 208), mask=mask)
    return c


def _mosque(L):
    oy = H - 560                                   # the mosque is drawn for a 560 px tall image, then moved down
    ink, rim = (7, 11, 26, 255), (255, 200, 120, 80)

    def dome(cx, base_y, rx, ry):
        L.d.pieslice([(cx - rx) * S, (base_y - ry + oy) * S, (cx + rx) * S, (base_y + ry + oy) * S], 180, 360, fill=ink, outline=rim, width=2 * S)

    def minaret(cx, top):
        top += oy
        L.d.rectangle([(cx - 17) * S, (top + 95) * S, (cx + 17) * S, H * S], fill=ink)
        L.d.rectangle([(cx - 25) * S, (top + 90) * S, (cx + 25) * S, (top + 108) * S], fill=ink, outline=rim, width=S)   # balcony
        L.d.rectangle([(cx - 13) * S, (top + 30) * S, (cx + 13) * S, (top + 92) * S], fill=ink)
        L.d.polygon([((cx - 17) * S, (top + 32) * S), ((cx + 17) * S, (top + 32) * S), (cx * S, top * S)], fill=ink, outline=rim)
        L.d.line([(cx * S, top * S), (cx * S, (top - 26) * S)], fill=ink, width=3 * S)
        L.circle(cx, top - 28, 5, ink)
        for k in range(3):                                                                              # lit slits
            L.d.rounded_rectangle([(cx - 4) * S, (top + 130 + k * 70) * S, (cx + 4) * S, (top + 160 + k * 70) * S], 4 * S,
                                  fill=(255, 196, 96, 230))

    L.d.rectangle([260 * S, (395 + oy) * S, 640 * S, H * S], fill=ink)                               # prayer hall
    dome(450, 402, 128, 112)
    L.d.line([(450 * S, (290 + oy) * S), (450 * S, (255 + oy) * S)], fill=ink, width=4 * S)
    L.circle(450, 250 + oy, 8, ink)
    L.circle(456, 238 + oy, 11, ink)                                                                  # finial crescent
    L.circle(462, 236 + oy, 9, (22, 38, 84, 255))
    dome(318, 430, 62, 52)
    dome(582, 430, 62, 52)
    dome(385, 445, 34, 30)
    dome(515, 445, 34, 30)
    minaret(150, 235)
    minaret(750, 235)
    L.d.rectangle([0, (538 + oy) * S, W * S, H * S], fill=ink)
    for x in (330, 400, 450, 500, 570):                                                               # arched windows
        L.d.rounded_rectangle([(x - 10) * S, (465 + oy) * S, (x + 10) * S, (520 + oy) * S], 10 * S, fill=(255, 196, 96, 215))
    L.d.rounded_rectangle([436 * S, (490 + oy) * S, 464 * S, (538 + oy) * S], 14 * S, fill=(255, 214, 140, 235))


def _static(title, subtitle, times, next_name, countdown, note):
    sky = _sky()
    L = Layer(H)
    _mosque(L)
    L.text(60, 38, "PRAYER TIMES", 24, fill=GOLD, weight="bold")
    L.text(60, 72, title[:26], 56, weight="light")
    L.text(60, 142, subtitle[:70], 24, fill=(255, 255, 255, 190))
    if times:
        n = len(times)
        cw, gap = 148, 14
        x0 = (W - (n * cw + (n - 1) * gap)) / 2
        for i, (name, t) in enumerate(times):
            x = x0 + i * (cw + gap)
            nxt = name == next_name
            L.glass(x, 190, cw, 118, fill=(255, 214, 120, 60) if nxt else (10, 16, 40, 150),
                    outline=GOLD if nxt else (255, 255, 255, 50))
            L.text(x + cw / 2, 208, name.upper(), 22, fill=GOLD if nxt else (255, 255, 255, 200), weight="bold", anchor="ma")
            L.text(x + cw / 2, 243, t, 44, anchor="ma")
        if countdown:
            L.text(W / 2, 330, countdown, 32, fill=GOLD, weight="bold", anchor="ma")
        if note:
            L.text(W / 2, 376, note[:80], 20, fill=(255, 255, 255, 170), anchor="ma")
    else:
        L.text(60, 215, "Choose your region, then your city", 30, fill=(255, 255, 255, 220))
        L.text(60, 262, "Times • next prayer • countdown", 22, fill=(255, 255, 255, 150))
    sky_png = sky.img.resize((W, H), Image.LANCZOS)
    fg = L.img.resize((W, H), Image.LANCZOS)
    return sky_png, fg


def render_gif(title, subtitle, times, next_name=None, countdown="", note=""):
    sky, fg = _static(title, subtitle, times, next_name, countdown, note)
    rnd = random.Random(7)
    stars = []
    for _ in range(95):
        x, y = rnd.randint(8, W - 8), rnd.randint(8, 340)
        if 40 < x < 860 and 175 < y < 330 and times:
            continue
        stars.append((x, y, rnd.choice((1, 1, 1.4, 1.8, 2.4)), rnd.random()))
    frames = []
    for f in range(FRAMES):
        fr = sky.copy()
        d = ImageDraw.Draw(fr, "RGBA")
        for x, y, r, ph in stars:
            b = 0.30 + 0.70 * (0.5 + 0.5 * math.sin(2 * math.pi * (f / FRAMES + ph)))
            col = (255, 255, 255, int(255 * b))
            d.ellipse([x - r, y - r, x + r, y + r], fill=col)
            if r >= 1.8:
                d.line([(x - r * 3, y), (x + r * 3, y)], fill=(255, 255, 255, int(120 * b)), width=1)
                d.line([(x, y - r * 3), (x, y + r * 3)], fill=(255, 255, 255, int(120 * b)), width=1)
        pulse = 0.5 + 0.5 * math.sin(2 * math.pi * f / FRAMES)
        d.ellipse([790 - 78, 105 - 78, 790 + 78, 105 + 78], fill=(255, 240, 200, int(8 + 10 * pulse)))
        if 2 <= f <= 6:                                                                      # a shooting star
            t = f - 2
            x0, y0 = 130 + t * 60, 30 + t * 28
            for k in range(8):
                a = int(230 * (1 - k / 8))
                d.line([(x0 - k * 9, y0 - k * 4.2), (x0 - (k + 1) * 9, y0 - (k + 1) * 4.2)], fill=(255, 255, 255, a), width=2)
        fr.paste(fg, (0, 0), fg)
        frames.append(fr)
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=130, loop=0, optimize=False)
    return buf.getvalue(), frames
