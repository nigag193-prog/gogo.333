"""Personalized animated welcome banner (free - Pillow only, no API key).

On member join GOGO sends an animated GIF (loops forever) for the new member:
  * "WELCOME TO" in gold, the SERVER NAME huge with a red glow + light sweep,
  * the member's DISPLAY NAME under it,
  * his Discord PROFILE PICTURE on the side inside a rotating golden ring,
  * rising embers, twinkling stars, red light streaks (HERMANOS-style design).

Optional per-server theme in guild_config.json:
    "welcome": {..., "card": true, "theme": ["#ff2d55", "#ff9f0a", "#ffd60a"]}
("card": false turns the banner off).

Install: copy this file next to bot.py and in bot.py __init__ (right under
antispam_guard.register(self)) add:

    import rolecard, welcomecard
    welcomecard.register(self)
    rolecard.register(self)

NOTE: delete welcome_banner.png / .gif / .jpg next to bot.py if you do not want
the old static banner next to this animated one.
"""
import asyncio
import io
import math
import os
import random
import re

import discord
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

W, H = 1280, 540
FRAMES = 24
DURATION = 85
STAR_COUNT = 55
EMBER_COUNT = 42
PALETTE_COLORS = 96
DEFAULT_THEME = ("#ff2d55", "#ff9f0a", "#ffd60a")     # crimson / orange / gold

_FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans{b}.ttf",
          "/usr/share/fonts/dejavu/DejaVuSans{b}.ttf",
          "/usr/share/fonts/truetype/noto/NotoSans-{r}.ttf",
          "/usr/share/fonts/truetype/liberation/LiberationSans-{r}.ttf",
          "C:/Windows/Fonts/segoeui{w}.ttf",
          "C:/Windows/Fonts/arial{w}.ttf"]
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200d\u20E3]")


def _font(size, bold=True):
    for pat in _FONTS:
        path = pat.format(b="-Bold" if bold else "", r="Bold" if bold else "Regular",
                          w="b" if bold else "")
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
    return ImageFont.load_default(size)


def _shape(text):
    """Arabic: native with Pillow+raqm, else arabic_reshaper, else raw."""
    try:
        from PIL import features
        if features.check("raqm"):
            return text
    except Exception:
        pass
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except ImportError:
        return text


def _hex(c, default=(255, 45, 85)):
    try:
        c = str(c).lstrip("#")
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    except Exception:
        return default


def _fit(draw, text, size0, max_w, bold=True):
    size = size0
    while size > 14:
        f = _font(size, bold)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return _font(14, bold)


def _circle_avatar(png, size):
    av = Image.open(io.BytesIO(png)).convert("RGBA").resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(av, (0, 0), mask)
    return out


def _glow_sprite(color, r, alpha=80):
    s = Image.new("RGBA", (r * 2, r * 2), (0, 0, 0, 0))
    ImageDraw.Draw(s).ellipse([0, 0, r * 2 - 1, r * 2 - 1], fill=color + (alpha,))
    return s.filter(ImageFilter.GaussianBlur(r // 3))


def _spaced(s):
    return " ".join(list(s))


def _build_title(server_name, member_name, cx):
    """Static text layer + its glow: 'WELCOME TO' / SERVER NAME / member name."""
    t = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(t)
    gold = (255, 205, 90)
    max_w = 860
    # --- "WELCOME TO" letterspaced, gold, with thin lines on both sides
    f_w = _font(24, True)
    wl_text = "WELCOME TO"
    sw = d.textlength(wl_text, font=f_w)
    d.text((cx, 108), wl_text, font=f_w, fill=gold + (255,), anchor="mm")
    for side in (-1, 1):
        d.line([(cx + side * (sw / 2 + 16), 108), (cx + side * (sw / 2 + 110), 108)],
               fill=gold + (170,), width=2)
    # --- SERVER NAME huge (red glow + white fill + shine sweep applied later)
    srv = _EMOJI.sub("", server_name).strip() or "SERVER"
    f_srv = _fit(d, srv, 104, max_w)
    d.text((cx, 248), srv, font=f_srv, fill=(255, 255, 255, 255), anchor="mm")
    swl = d.textlength(srv, font=f_srv)
    # angled red accent bars under the name (HERMANOS banner vibe)
    for side in (-1, 1):
        x0 = cx + side * (swl / 2 + 26)
        d.line([(x0, 296), (x0 + side * 90, 296)], fill=(255, 45, 85, 230), width=6)
        d.line([(x0, 310), (x0 + side * 60, 310)], fill=(255, 45, 85, 140), width=3)
    # --- member name under it
    shaped = _shape(_EMOJI.sub("", str(member_name)).strip() or str(member_name))
    f_name = _fit(d, shaped, 52, max_w)
    d.text((cx, 372), shaped, font=f_name, fill=gold + (255,), anchor="mm")
    return t, t.filter(ImageFilter.GaussianBlur(14))


def render_welcome_gif(server_name, member_name, member_number, avatar_png=None, theme=None):
    """-> GIF bytes: personalized, animated, HERM-style welcome banner."""
    theme = [_hex(c) for c in (theme or DEFAULT_THEME)]
    crimson, orange, gold = theme[0], theme[1] if len(theme) > 1 else theme[0], (255, 205, 90)
    rng = random.Random(str(member_name)[:64])

    bg = Image.new("RGBA", (W, H))
    bd = ImageDraw.Draw(bg)
    for y in range(H):
        k = y / H
        bd.line([(0, y), (W, y)], fill=(int(8 + 8 * k), int(5 + 5 * k), int(10 + 8 * k), 255))
    # static diagonal red streaks
    for i in range(6):
        x = 60 + i * 220
        bd.polygon([(x, 0), (x + 26, 0), (x - 120, H), (x - 146, H)],
                   fill=crimson + (10 + (i % 3) * 5,))

    stars = [(rng.uniform(30, W - 30), rng.uniform(24, H - 24),
              rng.uniform(0.6, 2.4), rng.uniform(0, 6.283)) for _ in range(STAR_COUNT)]
    embers = []
    for _ in range(EMBER_COUNT):
        embers.append({"x": rng.uniform(40, W - 40), "y": rng.uniform(0, H),
                       "sp": rng.uniform(2.2, 4.6), "s": rng.uniform(1.2, 3.2),
                       "ph": rng.uniform(0, 6.283),
                       "c": orange if rng.random() < 0.6 else crimson})

    AV = 190
    avatar_img = _circle_avatar(avatar_png, AV) if avatar_png else None
    if avatar_img is not None:
        axc, ayc = 1075, H // 2
        ax, ay = axc - AV // 2, ayc - AV // 2
        cx = 60 + (ax - 60) // 2
        av_glow = _glow_sprite(crimson, AV // 2 + 34, alpha=90)
        av_glow2 = _glow_sprite(gold, AV // 2 + 10, alpha=45)
    else:
        ax = ay = 0
        cx = W // 2
        av_glow = av_glow2 = None

    title, title_glow = _build_title(server_name, str(member_name), cx)
    f_small = _font(22, True)

    frames_out = []
    for f in range(FRAMES):
        im = bg.copy()
        # big pulsing crimson glow behind avatar side
        if av_glow is not None:
            pulse = 0.55 + 0.45 * math.sin(2 * math.pi * (f / FRAMES))
            gl = av_glow.copy()
            gl.putalpha(gl.split()[3].point(lambda a: int(a * pulse)))
            im.alpha_composite(gl, (axc - av_glow.width // 2, ayc - av_glow.height // 2))
            im.alpha_composite(av_glow2, (axc - av_glow2.width // 2, ayc - av_glow2.height // 2))
        # rising embers (seamless: they wrap off-screen)
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        for e in embers:
            y = e["y"] - f * e["sp"]
            span = H + 60
            y = (y % span) - 30                     # wrap happens off-screen
            x = e["x"] + 10 * math.sin(2 * math.pi * (f / FRAMES) + e["ph"])
            tw = 0.5 + 0.5 * math.sin(2 * math.pi * (f / FRAMES) + e["ph"] * 2)
            od.ellipse([x - e["s"], y - e["s"], x + e["s"], y + e["s"]],
                       fill=e["c"] + (int(60 + 150 * tw),))
        for (sx, sy, sr, ph) in stars:
            tw = 0.5 + 0.5 * math.sin(2 * math.pi * (f / FRAMES) + ph)
            od.ellipse([sx - sr, sy - sr, sx + sr, sy + sr],
                       fill=(255, 255, 255, int(28 + 150 * tw)))
        im.alpha_composite(ov)

        if avatar_img is not None:
            im.paste(avatar_img, (ax, ay), avatar_img)
            rd = ImageDraw.Draw(im)
            ang = f * (360 / FRAMES)
            for k in range(3):
                s0 = ang + k * 120
                rd.arc([ax - 12, ay - 12, ax + AV + 12, ay + AV + 12],
                       start=s0, end=s0 + 68, fill=gold + (255,), width=6)

        # red glow under the title, then title + moving shine
        im.alpha_composite(title_glow)
        band = Image.new("L", (W, H), 0)
        bnd = ImageDraw.Draw(band)
        bx = -220 + (W + 440) * (f / FRAMES)
        bnd.polygon([(bx, 0), (bx + 110, 0), (bx - 30, H), (bx - 140, H)], fill=95)
        m = ImageChops.multiply(title.split()[3], band)
        shine = Image.new("RGBA", (W, H), (255, 255, 255, 0))
        shine.putalpha(m)
        im.alpha_composite(title)
        im.alpha_composite(shine)

        fd = ImageDraw.Draw(im)
        fd.text((60, H - 42), "powered by GOGO", font=f_small,
                fill=(150, 150, 170, 220), anchor="lm")
        fd.text((W - 60, H - 42), f"MEMBER #{member_number}", font=f_small,
                fill=gold + (255,), anchor="rm")
        frames_out.append(im.convert("RGB"))

    pal = frames_out[0].quantize(colors=PALETTE_COLORS)
    qframes = [fr.quantize(palette=pal) for fr in frames_out]
    out = io.BytesIO()
    qframes[0].save(out, format="GIF", save_all=True, append_images=qframes[1:],
                    duration=DURATION, loop=0, disposal=2)
    return out.getvalue()


async def send_welcome(bot, member):
    guild = member.guild
    cfg = (bot.config.get(str(guild.id)) or {}).get("welcome") or {}
    if cfg.get("card") is False:
        return
    ch = guild.get_channel(int(cfg.get("channel_id") or 0))
    if ch is not None and not ch.permissions_for(guild.me).send_messages:
        ch = None
    if ch is None:
        # fallback for servers without a welcome config: system channel, else the
        # first text channel where the bot is allowed to send -> ANY server works
        ch = guild.system_channel
        if ch is None or not ch.permissions_for(guild.me).send_messages:
            ch = None
            for c in guild.text_channels:
                try:
                    if c.permissions_for(guild.me).send_messages:
                        ch = c
                        break
                except Exception:
                    continue
    if ch is None:
        return
    avatar = None
    try:
        avatar = await member.display_avatar.replace(size=256, format="png").read()
    except Exception:
        pass
    theme = cfg.get("theme") if isinstance(cfg.get("theme"), list) else None
    gif = await asyncio.to_thread(render_welcome_gif, guild.name,
                                  member.display_name,
                                  guild.member_count or 0, avatar, theme)
    try:
        await ch.send(file=discord.File(io.BytesIO(gif), filename="welcome.gif"))
    except discord.HTTPException as e:
        bot.log(f"[welcomecard] cannot send in {guild.name}: {e}")


def register(bot):
    """Wraps on_member_join: original handler first (welcome text), then the GIF."""
    original = getattr(bot, "on_member_join", None)

    async def on_member_join(member):
        if original is not None:
            try:
                await original(member)
            except Exception as e:
                bot.log(f"[welcomecard] original on_member_join failed: {e}")
        try:
            await send_welcome(bot, member)
        except Exception as e:
            bot.log(f"[welcomecard] {type(e).__name__}: {e}")

    bot.on_member_join = on_member_join
    bot.log("[welcomecard] registered")
