"""Animated role celebration cards (free - Pillow only, no API key).

When a member picks (or drops) a role from the self-service menu (/get role,
rolesys._RoleSelect) or from a blueprint role panel (modules.RoleButton),
GOGO answers the SAME member with a short animated GIF:
  * the role name lights up IN THE ROLE'S OWN COLOR,
  * particles burst around it, stars twinkle,
  * his avatar sits in a pulsing golden ring.

IMPORTANT (requested behaviour):
  * ONLY the member who picked the role sees it (ephemeral followup),
  * the message auto-deletes itself after ~20 s,
  * no congratulation text, no ping.

Works for ANY role of the server: color comes from role.colour.

Install: copy this file next to bot.py and in bot.py __init__ (right under
antispam_guard.register(self)) add:

    import rolecard, welcomecard
    welcomecard.register(self)
    rolecard.register(self)

register(bot) patches rolesys._RoleSelect.callback and modules.RoleButton.callback
at runtime - you do NOT need to edit rolesys.py or modules.py.
"""
import asyncio
import io
import math
import os
import random
import re

import discord
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

W, H = 900, 300
FRAMES = 26
DURATION = 70                 # ms/frame -> ~1.8 s seamless loop
STAR_COUNT = 40
PART_COUNT = 90
PALETTE_COLORS = 96
DELETE_AFTER = 20             # seconds before the ephemeral card disappears

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


def _rgb(value):
    return ((value >> 16) & 255, (value >> 8) & 255, value & 255)


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


def _build_title(role_name, member_name, accent, gained, cx):
    t = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(t)
    max_w = (W - cx) * 2 - 60
    tag = "ROLE UNLOCKED" if gained else "ROLE REMOVED"
    d.text((cx, 50), tag, font=_font(22, True),
           fill=(accent if gained else (130, 130, 140)) + (255,), anchor="mm")
    name_clean = _EMOJI.sub("", str(role_name)).strip() or "Role"
    f_role = _fit(d, name_clean, 62, max_w)
    d.text((cx, 128), name_clean, font=f_role, fill=(255, 255, 255, 255), anchor="mm")
    wl = d.textlength(name_clean, font=f_role)
    d.rounded_rectangle([cx - wl / 2, 166, cx + wl / 2, 172], radius=3, fill=accent + (255,))
    shaped = _shape(_EMOJI.sub("", str(member_name)).strip() or str(member_name))
    f_name = _fit(d, shaped, 30, max_w)
    d.text((cx, 212), shaped, font=f_name, fill=(205, 205, 220, 255), anchor="mm")
    return t, t.filter(ImageFilter.GaussianBlur(10))


def render_role_gif(role_name, color_value, member_name, avatar_png=None, gained=True):
    """-> GIF bytes: animated celebration card in the role's own color."""
    accent = _rgb(color_value) if color_value else (124, 58, 237)
    rng = random.Random((str(role_name) + str(member_name))[:64])

    bg = Image.new("RGBA", (W, H))
    bd = ImageDraw.Draw(bg)
    for y in range(H):
        k = y / H
        bd.line([(0, y), (W, y)], fill=(int(18 + 10 * k), int(12 + 8 * k), int(30 + 16 * k), 255))
    bd.rounded_rectangle([4, 4, W - 5, H - 5], radius=24, outline=accent + (170,), width=3)
    stars = [(rng.uniform(24, W - 24), rng.uniform(20, H - 20),
              rng.uniform(0.6, 2.2), rng.uniform(0, 6.283)) for _ in range(STAR_COUNT)]

    AV = 150
    avatar_img = _circle_avatar(avatar_png, AV) if avatar_png else None
    if avatar_img is not None:
        ax, ay = 90, (H - AV) // 2
        cx = (ax + AV + 30 + W - 40) // 2
    else:
        ax = ay = 0
        cx = W // 2
    pcy = 140
    parts = []
    for _ in range(PART_COUNT):
        a = rng.uniform(0, 6.283)
        sp = rng.uniform(1.2, 5.2)
        parts.append({"x": cx, "y": pcy, "vx": math.cos(a) * sp,
                      "vy": math.sin(a) * sp - rng.uniform(0.5, 2.0),
                      "s": rng.randint(2, 5),
                      "c": accent if rng.random() < 0.7 else (255, 255, 255),
                      "d": rng.randint(0, 5)})

    title, title_glow = _build_title(role_name, str(member_name), accent, gained, cx)

    frames_out = []
    for f in range(FRAMES):
        im = bg.copy()
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        for (sx, sy, sr, ph) in stars:
            tw = 0.5 + 0.5 * math.sin(2 * math.pi * (f / FRAMES) + ph)
            od.ellipse([sx - sr, sy - sr, sx + sr, sy + sr],
                       fill=(255, 255, 255, int(40 + 150 * tw)))
        for p in parts:
            t = f - p["d"]
            if t < 0:
                continue
            x = p["x"] + p["vx"] * t
            y = p["y"] + p["vy"] * t + 0.10 * t * t
            a = 255 * max(0.0, 1 - t / 22)
            if a <= 0:
                continue
            s = p["s"]
            od.ellipse([x - s, y - s, x + s, y + s], fill=p["c"] + (int(a),))
        im.alpha_composite(ov)

        if avatar_img is not None:
            pulse = 0.5 + 0.5 * math.sin(2 * math.pi * (f / FRAMES))
            gl = Image.new("RGBA", (AV + 80, AV + 80), (0, 0, 0, 0))
            ImageDraw.Draw(gl).ellipse([20, 20, AV + 60, AV + 60],
                                       fill=accent + (int(60 + 80 * pulse),))
            im.alpha_composite(gl.filter(ImageFilter.GaussianBlur(16)), (ax - 40, ay - 40))
            im.paste(avatar_img, (ax, ay), avatar_img)
            rd = ImageDraw.Draw(im)
            ang = f * (360 / FRAMES)
            for k in range(3):
                s0 = ang + k * 120
                rd.arc([ax - 10, ay - 10, ax + AV + 10, ay + AV + 10],
                       start=s0, end=s0 + 70, fill=(255, 220, 120, 255), width=4)

        im.alpha_composite(title_glow)
        band = Image.new("L", (W, H), 0)
        bnd = ImageDraw.Draw(band)
        bx = -200 + (W + 400) * (f / FRAMES)
        bnd.polygon([(bx, 0), (bx + 90, 0), (bx - 40, H), (bx - 130, H)], fill=110)
        m = ImageChops.multiply(title.split()[3], band)
        shine = Image.new("RGBA", (W, H), (255, 255, 255, 0))
        shine.putalpha(m)
        im.alpha_composite(title)
        im.alpha_composite(shine)
        frames_out.append(im.convert("RGB"))

    pal = frames_out[0].quantize(colors=PALETTE_COLORS)
    qframes = [fr.quantize(palette=pal) for fr in frames_out]
    out = io.BytesIO()
    qframes[0].save(out, format="GIF", save_all=True, append_images=qframes[1:],
                    duration=DURATION, loop=0, disposal=2)
    return out.getvalue()


def _resolve_role(view_item, interaction):
    """Figure out which role this interaction touched (select values or button id)."""
    guild = interaction.guild
    if guild is None:
        return None
    rid = None
    values = getattr(view_item, "values", None)
    if values:
        try:
            rid = int(values[0])
        except Exception:
            rid = None
    if rid is None:
        cid = getattr(view_item, "custom_id", "") or ""
        for sep in (":", "_", "-"):
            if sep in cid:
                try:
                    rid = int(cid.split(sep, 1)[1])
                    break
                except Exception:
                    pass
    if rid is None:
        return None
    return guild.get_role(rid)


async def _celebrate(view_item, interaction, bot):
    """Ephemeral animated card, ONLY for the member who picked the role,
    auto-deletes after DELETE_AFTER seconds. No text, no ping."""
    role = _resolve_role(view_item, interaction)
    member = interaction.user
    if role is None or member is None:
        return
    gained = role in member.roles          # toggle: re-check AFTER the original callback
    avatar = None
    try:
        avatar = await member.display_avatar.replace(size=160, format="png").read()
    except Exception:
        pass
    gif = await asyncio.to_thread(render_role_gif, role.name,
                                  role.colour.value or 0x7C3AED,
                                  member.display_name, avatar, gained)
    try:
        await interaction.followup.send(
            file=discord.File(io.BytesIO(gif), filename="role.gif"),
            ephemeral=True, delete_after=DELETE_AFTER)
    except TypeError:
        await interaction.followup.send(
            file=discord.File(io.BytesIO(gif), filename="role.gif"), ephemeral=True)
    except discord.HTTPException as e:
        bot.log(f"[rolecard] send failed: {e}")


def _patch_view_class(cls, bot, label):
    orig = getattr(cls, "callback", None)
    if orig is None or getattr(cls, "_fx_patched", False):
        return
    async def callback(view_item, interaction):
        await orig(view_item, interaction)     # let the original logic (and its errors) run
        try:
            await _celebrate(view_item, interaction, bot)
        except Exception as e:
            bot.log(f"[rolecard] {_label if False else label} {type(e).__name__}: {e}")
    cls.callback = callback
    cls._fx_patched = True
    bot.log(f"[rolecard] patched {label}")


def register(bot):
    """Runtime patch of rolesys._RoleSelect and modules.RoleButton.
    No need to edit those files manually."""
    try:
        import rolesys
        _patch_view_class(rolesys._RoleSelect, bot, "rolesys._RoleSelect")
    except Exception as e:
        bot.log(f"[rolecard] rolesys patch skipped: {e}")
    try:
        import modules
        _patch_view_class(modules.RoleButton, bot, "modules.RoleButton")
    except Exception as e:
        bot.log(f"[rolecard] modules patch skipped: {e}")
