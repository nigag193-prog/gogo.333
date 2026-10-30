
"""Animated role celebration cards for GOGO Discord bot."""
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
DURATION = 70
STAR_COUNT = 40
PART_COUNT = 90
PALETTE_COLORS = 96
DELETE_AFTER = 20

_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans{b}.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans{b}.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-{r}.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-{r}.ttf",
    "C:/Windows/Fonts/segoeui{w}.ttf",
    "C:/Windows/Fonts/arial{w}.ttf",
]

_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200d\u20E3]"
)


def _font(size, bold=True):
    for pat in _FONTS:
        path = pat.format(
            b="-Bold" if bold else "",
            r="Bold" if bold else "Regular",
            w="b" if bold else "",
        )
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
    return (
        (value >> 16) & 255,
        (value >> 8) & 255,
        value & 255,
    )


def _fit(draw, text, size0, max_w, bold=True):
    size = size0
    while size > 14:
        font = _font(size, bold)
        if draw.textlength(text, font=font) <= max_w:
            return font
        size -= 2
    return _font(14, bold)


def _circle_avatar(png, size):
    avatar = Image.open(io.BytesIO(png)).convert("RGBA")
    avatar = avatar.resize((size, size))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse(
        (0, 0, size - 1, size - 1), fill=255
    )

    output = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    output.paste(avatar, (0, 0), mask)
    return output


def _build_title(role_name, member_name, accent, gained, cx):
    title = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(title)

    max_w = (W - cx) * 2 - 60
    tag = "ROLE UNLOCKED" if gained else "ROLE REMOVED"

    draw.text(
        (cx, 50),
        tag,
        font=_font(22, True),
        fill=(accent if gained else (130, 130, 140)) + (255,),
        anchor="mm",
    )

    # Emoji are removed here because standard Pillow fonts may not render them.
    name_clean = _EMOJI.sub("", str(role_name)).strip() or "Role"
    role_font = _fit(draw, name_clean, 62, max_w)

    draw.text(
        (cx, 128),
        name_clean,
        font=role_font,
        fill=(255, 255, 255, 255),
        anchor="mm",
    )

    text_width = draw.textlength(name_clean, font=role_font)
    draw.rounded_rectangle(
        [
            cx - text_width / 2,
            166,
            cx + text_width / 2,
            172,
        ],
        radius=3,
        fill=accent + (255,),
    )

    shaped_name = _shape(
        _EMOJI.sub("", str(member_name)).strip() or str(member_name)
    )
    member_font = _fit(draw, shaped_name, 30, max_w)

    draw.text(
        (cx, 212),
        shaped_name,
        font=member_font,
        fill=(205, 205, 220, 255),
        anchor="mm",
    )

    return title, title.filter(ImageFilter.GaussianBlur(10))


def render_role_gif(
    role_name,
    color_value,
    member_name,
    avatar_png=None,
    gained=True,
):
    """Create an animated role card GIF."""
    accent = _rgb(color_value) if color_value else (124, 58, 237)
    rng = random.Random((str(role_name) + str(member_name))[:64])

    background = Image.new("RGBA", (W, H))
    background_draw = ImageDraw.Draw(background)

    for y in range(H):
        k = y / H
        background_draw.line(
            [(0, y), (W, y)],
            fill=(
                int(18 + 10 * k),
                int(12 + 8 * k),
                int(30 + 16 * k),
                255,
            ),
        )

    background_draw.rounded_rectangle(
        [4, 4, W - 5, H - 5],
        radius=24,
        outline=accent + (170,),
        width=3,
    )

    stars = [
        (
            rng.uniform(24, W - 24),
            rng.uniform(20, H - 20),
            rng.uniform(0.6, 2.2),
            rng.uniform(0, 6.283),
        )
        for _ in range(STAR_COUNT)
    ]

    avatar_size = 150
    avatar_image = _circle_avatar(avatar_png, avatar_size) if avatar_png else None

    if avatar_image is not None:
        avatar_x = 90
        avatar_y = (H - avatar_size) // 2
        center_x = (avatar_x + avatar_size + 30 + W - 40) // 2
    else:
        avatar_x = avatar_y = 0
        center_x = W // 2

    particle_center_y = 140
    particles = []

    for _ in range(PART_COUNT):
        angle = rng.uniform(0, 6.283)
        speed = rng.uniform(1.2, 5.2)

        particles.append({
            "x": center_x,
            "y": particle_center_y,
            "vx": math.cos(angle) * speed,
            "vy": math.sin(angle) * speed - rng.uniform(0.5, 2.0),
            "s": rng.randint(2, 5),
            "c": accent if rng.random() < 0.7 else (255, 255, 255),
            "d": rng.randint(0, 5),
        })

    title, title_glow = _build_title(
        role_name, str(member_name), accent, gained, center_x
    )

    frames_out = []

    for frame_index in range(FRAMES):
        image = background.copy()
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)

        # Twinkling stars.
        for sx, sy, sr, phase in stars:
            twinkle = 0.5 + 0.5 * math.sin(
                2 * math.pi * (frame_index / FRAMES) + phase
            )
            overlay_draw.ellipse(
                [sx - sr, sy - sr, sx + sr, sy + sr],
                fill=(255, 255, 255, int(40 + 150 * twinkle)),
            )

        # Animated particle burst.
        for particle in particles:
            t = frame_index - particle["d"]
            if t < 0:
                continue

            x = particle["x"] + particle["vx"] * t
            y = (
                particle["y"]
                + particle["vy"] * t
                + 0.10 * t * t
            )
            alpha = 255 * max(0.0, 1 - t / 22)

            if alpha <= 0:
                continue

            size = particle["s"]
            overlay_draw.ellipse(
                [x - size, y - size, x + size, y + size],
                fill=particle["c"] + (int(alpha),),
            )

        image.alpha_composite(overlay)

        # Avatar with a pulsing glow and rotating golden arcs.
        if avatar_image is not None:
            pulse = 0.5 + 0.5 * math.sin(
                2 * math.pi * (frame_index / FRAMES)
            )

            glow = Image.new(
                "RGBA",
                (avatar_size + 80, avatar_size + 80),
                (0, 0, 0, 0),
            )
            ImageDraw.Draw(glow).ellipse(
                [20, 20, avatar_size + 60, avatar_size + 60],
                fill=accent + (int(60 + 80 * pulse),),
            )

            image.alpha_composite(
                glow.filter(ImageFilter.GaussianBlur(16)),
                (avatar_x - 40, avatar_y - 40),
            )
            image.paste(avatar_image, (avatar_x, avatar_y), avatar_image)

            ring_draw = ImageDraw.Draw(image)
            angle = frame_index * (360 / FRAMES)

            for k in range(3):
                start_angle = angle + k * 120
                ring_draw.arc(
                    [
                        avatar_x - 10,
                        avatar_y - 10,
                        avatar_x + avatar_size + 10,
                        avatar_y + avatar_size + 10,
                    ],
                    start=start_angle,
                    end=start_angle + 70,
                    fill=(255, 220, 120, 255),
                    width=4,
                )

        image.alpha_composite(title_glow)

        # Moving shine across the title.
        band = Image.new("L", (W, H), 0)
        band_draw = ImageDraw.Draw(band)
        bx = -200 + (W + 400) * (frame_index / FRAMES)

        band_draw.polygon(
            [
                (bx, 0),
                (bx + 90, 0),
                (bx - 40, H),
                (bx - 130, H),
            ],
            fill=110,
        )

        title_mask = ImageChops.multiply(title.split()[3], band)
        shine = Image.new("RGBA", (W, H), (255, 255, 255, 0))
        shine.putalpha(title_mask)

        image.alpha_composite(title)
        image.alpha_composite(shine)
        frames_out.append(image.convert("RGB"))

    palette = frames_out[0].quantize(colors=PALETTE_COLORS)
    quantized_frames = [
        frame.quantize(palette=palette) for frame in frames_out
    ]

    output = io.BytesIO()
    quantized_frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=quantized_frames[1:],
        duration=DURATION,
        loop=0,
        disposal=2,
    )

    return output.getvalue()


def _resolve_role(view_item, interaction):
    """Find the role selected from a dropdown or role button."""
    guild = interaction.guild
    if guild is None:
        return None

    role_id = None
    values = getattr(view_item, "values", None)

    if values:
        try:
            role_id = int(values[0])
        except (TypeError, ValueError):
            role_id = None

    if role_id is None:
        custom_id = getattr(view_item, "custom_id", "") or ""

        for separator in (":", "_", "-"):
            if separator not in custom_id:
                continue

            try:
                candidate = custom_id.split(separator, 1)[1]
                role_id = int(candidate)
                break
            except (TypeError, ValueError):
                continue

    if role_id is None:
        return None

    return guild.get_role(role_id)


async def _celebrate(view_item, interaction, bot, gained):
    """Send the animated card privately after a confirmed role change."""
    role = _resolve_role(view_item, interaction)
    member = interaction.user

    if role is None or member is None:
        return

    avatar = None
    try:
        avatar = await member.display_avatar.replace(
            size=160, format="png"
        ).read()
    except Exception:
        pass

    gif = await asyncio.to_thread(
        render_role_gif,
        role.name,
        role.colour.value or 0x7C3AED,
        member.display_name,
        avatar,
        gained,
    )

    try:
        await interaction.followup.send(
            file=discord.File(io.BytesIO(gif), filename="role.gif"),
            ephemeral=True,
            delete_after=DELETE_AFTER,
        )
    except TypeError:
        # Compatibility fallback for Discord library versions that don't
        # support delete_after on ephemeral followups.
        try:
            await interaction.followup.send(
                file=discord.File(io.BytesIO(gif), filename="role.gif"),
                ephemeral=True,
            )
        except discord.HTTPException as error:
            bot.log(f"[rolecard] send failed: {error}")
    except discord.HTTPException as error:
        bot.log(f"[rolecard] send failed: {error}")


def _patch_view_class(cls, bot, label):
    """Wrap the existing callback without changing its role logic."""
    original_callback = getattr(cls, "callback", None)

    if original_callback is None or getattr(cls, "_fx_patched", False):
        return

    async def callback(view_item, interaction):
        role = _resolve_role(view_item, interaction)
        member = interaction.user

        before_has = (
            role in member.roles
            if role is not None and member is not None
            else None
        )

        # Run the original callback first; it remains responsible for
        # adding/removing the role and responding to the interaction.
        await original_callback(view_item, interaction)

        try:
            if (
                role is None
                or member is None
                or before_has is None
                or interaction.guild is None
            ):
                return

            # Fetch the member again because the cached roles may be stale.
            fresh_member = await interaction.guild.fetch_member(member.id)
            after_has = role in fresh_member.roles

            # No animation for failed actions or unchanged role membership.
            if after_has != before_has:
                await _celebrate(
                    view_item,
                    interaction,
                    bot,
                    gained=after_has,
                )

        except Exception as error:
            bot.log(
                f"[rolecard] {label} "
                f"{type(error).__name__}: {error}"
            )

    cls.callback = callback
    cls._fx_patched = True
    bot.log(f"[rolecard] patched {label}")


def register(bot):
    """Patch the self-service role selector and role-panel buttons."""
    try:
        import rolesys
        _patch_view_class(
            rolesys._RoleSelect,
            bot,
            "rolesys._RoleSelect",
        )
    except Exception as error:
        bot.log(f"[rolecard] rolesys patch skipped: {error}")

    try:
        import modules
        _patch_view_class(
            modules.RoleButton,
            bot,
            "modules.RoleButton",
        )
    except Exception as error:
        bot.log(f"[rolecard] modules patch skipped: {error}")
