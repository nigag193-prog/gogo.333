
"""Role self-service, warning/moderation and command guard for GOGO."""
import re
import time
import datetime

import discord
from discord import app_commands

WARN_ROLE_NAMES = ["⚠️ Warn 1", "⚠️ Warn 2", "⚠️ Warn 3"]
MAX_GIVE_ROLE = "🌟 [HR] Legend"
LEGEND_OK = {"mention_everyone", "move_members"}

SELF_CLAIM_ROLE_ORDER = [
    "🌟 [HR] Legend",
    "🎮 [HR] Gamer",
    "🤍 [HR] Friends",
    "🎥 [HR] Streamer",
]

INSULT_WORDS = {
    "fuck", "fucking", "shit", "bitch", "asshole", "idiot", "stupid", "retard",
    "pute", "putain", "merde", "connard", "connasse", "salope",
    "zamel", "zaml", "9a7ba", "m9wd", "mkhwr", "7mar", "hmar", "kelb",
    "kalb", "sb", "sbb", "sabb",
    "كلب", "حمار", "قحبة", "قحب", "خرا", "وسخ", "متخلف", "غبي", "سير تقود",
}
SERVER_WORDS = {"server", "serveur", "سيرفر", "السيرفر", "servera", "srv"}
OWNER_WORDS = {"owner", "المالك", "مول السيرفر", "مولسيرفر", "مول", "admin", "ادمين"}

# Detect a custom Discord emoji or a Unicode emoji at the start of a role name.
_EMOJI_PREFIX = re.compile(
    r"^(<a?:[A-Za-z0-9_]{2,32}:\d+>|"
    r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]"
    r"(?:\ufe0f|\u200d[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]"
    r"|[\U0001F3FB-\U0001F3FF]|\u20e3)*)\s*"
)


def _role_emoji_and_label(role):
    name = role.name.strip()
    match = _EMOJI_PREFIX.match(name)

    if not match:
        return None, name[:80] or "Role"

    token = match.group(1)
    label = name[match.end():].strip() or name

    try:
        emoji = (
            discord.PartialEmoji.from_str(token)
            if token.startswith("<")
            else token
        )
    except (TypeError, ValueError):
        emoji = None

    return emoji, label[:80]


def init_tables(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS warns("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER, "
        "user INTEGER, reason TEXT, ts REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS role_requests("
        "guild INTEGER, user INTEGER, role INTEGER, ts REAL)"
    )
    conn.commit()


def _cfg(bot, guild):
    return bot.gcfg(guild)


def _roles(guild):
    return {r.name: r for r in guild.roles}


def _max_role(guild, cfg):
    role = discord.utils.get(guild.roles, name=MAX_GIVE_ROLE)
    if role:
        return role

    acfg = cfg.get("assistant") or {}
    rid = acfg.get("max_role_id")
    return guild.get_role(int(rid or 0))


def giveable_roles(guild, cfg):
    """Only the approved self-claim roles, in the requested order."""
    limit = _max_role(guild, cfg)
    me = guild.me

    if limit is None or me is None:
        return []

    excluded = set((cfg.get("assistant") or {}).get("exclude_ids") or [])
    roles_by_name = {r.name: r for r in guild.roles}
    out = []

    dangerous = (
        "administrator", "manage_guild", "manage_roles", "manage_channels",
        "manage_messages", "manage_webhooks", "manage_nicknames",
        "manage_emojis_and_stickers", "manage_threads", "manage_events",
        "kick_members", "ban_members", "moderate_members", "mention_everyone",
        "view_audit_log", "move_members", "mute_members", "deafen_members",
        "view_guild_insights",
    )

    for name in SELF_CLAIM_ROLE_ORDER:
        role = roles_by_name.get(name)

        if (
            role is None
            or role.is_default()
            or role.managed
            or role.id in excluded
        ):
            continue

        tolerated = LEGEND_OK if role.name == MAX_GIVE_ROLE else ()

        if any(
            getattr(role.permissions, perm, False)
            for perm in dangerous
            if perm not in tolerated
        ):
            continue

        out.append(role)

    return out


def _warn_count(bot, guild, user_id):
    row = bot.db.conn.execute(
        "SELECT COUNT(*) FROM warns WHERE guild=? AND user=?",
        (guild.id, user_id),
    ).fetchone()
    return int(row[0] if row else 0)


def _set_warn_role(guild, member, level):
    roles = [discord.utils.get(guild.roles, name=n) for n in WARN_ROLE_NAMES]
    target = roles[min(level, 3) - 1] if level else None

    async def apply():
        for role in roles:
            if role and role in member.roles and role != target:
                try:
                    await member.remove_roles(role, reason="warning escalation")
                except discord.HTTPException:
                    pass

        if target and target not in member.roles:
            try:
                await member.add_roles(target, reason=f"warning {level}/3")
            except discord.HTTPException:
                pass

    return apply


def _looks_like_insult(content):
    s = content.lower().strip()
    words = set(re.findall(r"[\w\u0600-\u06ff']+", s))
    obvious = bool(words & INSULT_WORDS) or any(
        insult in s for insult in INSULT_WORDS if len(insult) >= 4
    )
    return obvious


async def anti_insult(bot, message):
    """Delete obvious insults and escalate Warn 1 -> Warn 3."""
    if not message.guild or message.author.bot or not _looks_like_insult(message.content):
        return False

    guild = message.guild

    try:
        await message.delete()
    except discord.HTTPException:
        pass

    bot.db.add_warn(guild.id, message.author.id, "insulting the server/owner")
    level = min(_warn_count(bot, guild, message.author.id), 3)
    member = guild.get_member(message.author.id) or message.author

    try:
        await _set_warn_role(guild, member, level)()
    except Exception:
        pass

    if level >= 3:
        try:
            await member.timeout(
                datetime.timedelta(hours=1),
                reason="Warn 3: insulting server/owner",
            )
        except discord.HTTPException:
            pass

        text = (
            f"⚠️ {member.mention} Warn 3/3. احترام السيرفر والـOwner واجب. "
            "خديتي timeout لمدة **1h**."
        )
    elif level == 2:
        text = (
            f"⚠️ {member.mention} عندك دابا **Warn 2/3**. "
            "بقا غير إنذار واحد قبل العقوبة ديال Warn 3."
        )
    else:
        text = (
            f"⚠️ {member.mention} هادي **Warn 1/3**. "
            "ما تسبّش السيرفر ولا الـOwner."
        )

    try:
        await message.channel.send(text, delete_after=8)
    except discord.HTTPException:
        pass

    try:
        await bot.event_log(
            guild,
            "mod",
            f"⚠️ Warning {level}/3",
            f"{member.mention} — insulting server/owner",
            0xE67E22,
        )
    except Exception:
        pass

    return True


def _is_prefix_command(content):
    return content.startswith("!") and len(content) > 1


async def guard_command(bot, message):
    """Guard legacy !commands."""
    if not message.guild or not _is_prefix_command(message.content):
        return False

    raw = message.content[1:].strip()
    name = raw.split()[0].lower() if raw else ""

    allowed = {
        "help", "balance", "daily", "shop", "buy", "richest",
        "lfg", "lockdown", "unlock",
    }

    if name in allowed or message.author.guild_permissions.manage_messages:
        return False

    try:
        await message.delete()
    except discord.HTTPException:
        pass

    try:
        await message.channel.send(
            f"⚠️ {message.author.mention} ماعندكش الحق تستعمل هاد command هنا. "
            "استعمل غير commands المسموحين ليك.",
            delete_after=6,
        )
    except discord.HTTPException:
        pass

    return True


class _RoleSelect(discord.ui.Select):
    def __init__(self, roles, page=0, per_page=25):
        self.roles_all = roles
        self.page = page
        self.per_page = per_page

        start = page * per_page
        chunk = roles[start:start + per_page]

        options = []
        for role in chunk:
            emoji, label = _role_emoji_and_label(role)
            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=str(role.id),
                    description="اختار هاد الرول",
                    emoji=emoji,
                )
            )

        super().__init__(
            placeholder="اختار الرول اللي بغيتي…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        bot = interaction.client
        cfg = bot.gcfg(guild)
        allowed = giveable_roles(guild, cfg)

        target_id = int(self.values[0])
        target = guild.get_role(target_id)

        if target is None or target not in allowed:
            await interaction.response.send_message(
                "❌ هاد الرول ما بقاتش متاحة ليك. عاود `/get role`.",
                ephemeral=True,
            )
            return

        member = interaction.user

        if target in member.roles:
            await interaction.response.send_message(
                f"ℹ️ عندك **{target.name}** من قبل.",
                ephemeral=True,
            )
            return

        try:
            await member.add_roles(target, reason="/get role self-service")
        except discord.HTTPException:
            await interaction.response.send_message(
                f"❌ ماقدرتش نعطيك **{target.name}**. "
                "خاص رتبة البوت تكون فوق هاد الرول فـDiscord، "
                "وتكون عندو Manage Roles.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ خديتي **{target.name}** 🎉",
            ephemeral=True,
        )

        try:
            await bot.event_log(
                guild,
                "members",
                "🎭 Self-service role",
                f"{member.mention} → **{target.name}**",
                0x9B59B6,
            )
        except Exception:
            pass


class _RolePage(discord.ui.View):
    def __init__(self, roles, page=0, per_page=25):
        super().__init__(timeout=120)
        self.roles = roles
        self.page = page
        self.per_page = per_page

        self.add_item(_RoleSelect(roles, page, per_page))

        pages = max(1, (len(roles) + per_page - 1) // per_page)
        self.prev_page.disabled = page <= 0
        self.next_page.disabled = page >= pages - 1
        self.page_label.label = f"Page {page + 1}/{pages}"

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page <= 0:
            await interaction.response.defer()
            return

        self.page -= 1
        await interaction.response.edit_message(
            view=_RolePage(self.roles, self.page, self.per_page)
        )

    @discord.ui.button(
        label="Page 1/1",
        style=discord.ButtonStyle.secondary,
        disabled=True,
    )
    async def page_label(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        pages = max(1, (len(self.roles) + self.per_page - 1) // self.per_page)

        if self.page >= pages - 1:
            await interaction.response.defer()
            return

        self.page += 1
        await interaction.response.edit_message(
            view=_RolePage(self.roles, self.page, self.per_page)
        )


class _RolePanelView(discord.ui.View):
    """Public role panel using the existing animated RoleButton callback."""

    def __init__(self, roles):
        super().__init__(timeout=None)

        from modules import RoleButton

        for role in roles[:25]:
            emoji, label = _role_emoji_and_label(role)

            button = RoleButton(role.id, label)

            if emoji is not None:
                button.emoji = emoji

            self.add_item(button)


def register(bot):
    tree = bot.tree
    group = app_commands.Group(
        name="get",
        description="Self-service roles",
    )

    @group.command(
        name="role",
        description="خذ رول مسموح ليك بهاد السيرفر",
    )
    @app_commands.guild_only()
    async def get_role(interaction: discord.Interaction):
        guild = interaction.guild
        cfg = bot.gcfg(guild)
        allowed = giveable_roles(guild, cfg)
        limit = _max_role(guild, cfg)

        if not allowed:
            reason = ""

            if limit is None:
                reason = "ما لقيتش الرول القصوى **🌟 [HR] Legend** فالسيرفر."
            elif guild.me is None or guild.me.top_role.position <= 0:
                reason = "bot role ما بايناش عندي."
            else:
                reason = "تأكد أن bot role طالع فوق الرولات اللي بغيتي Gogo يعطيها."

            await interaction.response.send_message(
                f"❌ ما كايناش دابا حتى رول قابلة للاختيار. {reason}",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"🎭 اختار الرول اللي بغيتي.\n"
            f"**أقصى رول يقدر Gogo يعطيها:** "
            f"{limit.name if limit else MAX_GIVE_ROLE}",
            view=_RolePage(allowed),
            ephemeral=True,
        )

    @group.command(
        name="panel",
        description="نشر لوحة اختيار الرولات فهاد القناة",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def get_panel(interaction: discord.Interaction):
        guild = interaction.guild
        allowed = giveable_roles(guild, bot.gcfg(guild))

        if not allowed:
            await interaction.response.send_message(
                "❌ ما لقيتش رولات متاحة. تأكد من إعدادات الرولات "
                "ووجود 🌟 [HR] Legend.",
                ephemeral=True,
            )
            return

        lines = []

        for role in allowed:
            emoji, label = _role_emoji_and_label(role)
            prefix = str(emoji) if emoji else "🎭"
            lines.append(f"{prefix} **{label}**")

        embed = discord.Embed(
            title="🎭 CHOOSE YOUR ROLES",
            description=(
                "اختار الرولات اللي بغيتي من الأزرار لتحت.\n"
                "ضغط مرة أخرى على نفس الزر باش تحيد الرول.\n\n"
                + "\n".join(lines)
            ),
            color=discord.Colour.from_rgb(124, 58, 237),
        )
        embed.set_footer(text="GOGO • SELF ROLE PANEL")

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            await interaction.channel.send(
                embed=embed,
                view=_RolePanelView(allowed),
            )
            await interaction.followup.send(
                "✅ تنشرات Role Panel بنجاح فهاد القناة.",
                ephemeral=True,
            )
        except discord.HTTPException as error:
            await interaction.followup.send(
                f"❌ ماقدرتش ننشر Panel: {str(error)[:300]}",
                ephemeral=True,
            )

    tree.add_command(group)
