"""Self-service roles for ANY server: /get role, /get config, /get panel.

  /get role     Everyone. A private menu: pick the roles you want to get (or drop).
  /get config   Server admins (Manage Roles). GOGO scans every role of the server, hides the ones it must not hand out
                (administrator-like permissions, roles above GOGO's own role, bot-managed roles) and lets you choose
                which roles members may claim. Then it posts a public panel with a menu.
  /get panel    Server admins. Posts (or moves) the public panel in the current channel.

Settings are saved per server:  guild_config.json -> "<server id>": {"getrole": {"role_ids": [...], "channel_id": ..., "message_id": ...}}
Servers that never ran /get config keep the previous behaviour (the built-in list), so nothing breaks.
Safety: every click re-checks the role (still safe, still below GOGO's role, still on the list) before it is given.
"""
import asyncio

import discord
from discord import app_commands

MAX_ROLES = 25                                    # one Discord select menu
DANGEROUS = ("administrator", "manage_guild", "manage_roles", "manage_channels", "manage_messages", "manage_webhooks",
             "manage_nicknames", "manage_expressions", "manage_emojis_and_stickers", "manage_threads", "manage_events",
             "kick_members", "ban_members", "moderate_members", "mention_everyone", "view_audit_log", "move_members",
             "mute_members", "deafen_members", "view_guild_insights")


def safe_role(guild, role):
    """Can GOGO hand this role out without risk?"""
    me = guild.me
    if role.is_default() or role.managed or me is None or role >= me.top_role:
        return False
    return not any(getattr(role.permissions, p, False) for p in DANGEROUS)


def candidates(guild):
    """Every role GOGO could offer, highest first."""
    return sorted((r for r in guild.roles if safe_role(guild, r)), key=lambda r: r.position, reverse=True)


def hidden_count(guild):
    return sum(1 for r in guild.roles if not r.is_default() and not safe_role(guild, r))


def allowed_roles(bot, guild):
    """The roles members can claim here: the admin's choice, else the old built-in list (if the server has one)."""
    cfg = (bot.config.get(str(guild.id)) or {})
    chosen = (cfg.get("getrole") or {}).get("role_ids")
    if chosen is not None:
        roles = [guild.get_role(int(i)) for i in chosen]
        return [r for r in roles if r is not None and safe_role(guild, r)]
    try:
        import rolesys
        return list(rolesys.giveable_roles(guild, bot.gcfg(guild)))
    except Exception:
        return []


async def toggle(bot, interaction, role_ids):
    """Give the roles the member does not have, remove the ones he has. Only roles on the list are touched."""
    guild, member = interaction.guild, interaction.user
    allowed = {r.id: r for r in allowed_roles(bot, guild)}
    added, removed, refused = [], [], []
    for rid in role_ids:
        role = allowed.get(int(rid))
        if role is None or not safe_role(guild, role):
            refused.append(str(rid))
            continue
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="/get role self-service")
                removed.append(role.name)
            else:
                await member.add_roles(role, reason="/get role self-service")
                added.append(role.name)
        except discord.HTTPException:
            refused.append(role.name)
    lines = []
    if added:
        lines.append("✅ **Added:** " + ", ".join(added))
    if removed:
        lines.append("➖ **Removed:** " + ", ".join(removed))
    if refused:
        lines.append("⚠️ **Not available any more:** " + ", ".join(refused) + " (ask an admin to run `/get config`).")
    await interaction.response.send_message("\n".join(lines) or "Nothing changed.", ephemeral=True)
    if added or removed:
        try:
            await bot.event_log(guild, "members", "🎭 Self-service roles",
                                f"{member.mention}: " + "; ".join(x for x in (
                                    "+ " + ", ".join(added) if added else "", "- " + ", ".join(removed) if removed else "") if x), 0x9B59B6)
        except Exception:
            pass


def _options(roles, member=None, with_state=False):
    out = []
    for r in roles[:MAX_ROLES]:
        desc = None
        if with_state and member is not None:
            desc = "✅ You have it: select to drop it" if r in member.roles else "Select to get it"
        out.append(discord.SelectOption(label=r.name[:100], value=str(r.id), description=desc))
    return out


def panel_embed(roles):
    emb = discord.Embed(title="🎭  Get your roles", color=0x9B59B6,
                        description="Use the menu below to **get** or **drop** a role.\nChoosing a role you already have removes it.")
    emb.add_field(name="Available roles", value="\n".join(f"• {r.mention}" for r in roles)[:1000] or "None yet.", inline=False)
    emb.set_footer(text="Self-service roles • powered by GOGO")
    return emb


# ---------------------------------------------------------------- member side
class ClaimSelect(discord.ui.Select):
    """Used by the public panel (custom_id) and by /get role (private)."""

    def __init__(self, bot, roles=None, member=None, custom_id=None):
        self.bot = bot
        opts = _options(roles, member, with_state=member is not None) if roles else [discord.SelectOption(label="(roles)", value="0")]
        kwargs = {
            "placeholder": "🎭 Choose your roles",
            "min_values": 1,
            "max_values": max(1, len(opts)),
            "options": opts,
        }
        if custom_id is not None:
            kwargs["custom_id"] = custom_id
        super().__init__(**kwargs)

    async def callback(self, interaction):
        await toggle(self.bot, interaction, self.values)


class PanelView(discord.ui.View):
    def __init__(self, bot, roles=None):
        super().__init__(timeout=None)
        self.add_item(ClaimSelect(bot, roles, custom_id="gogo:get:panel"))


# ---------------------------------------------------------------- admin side
def can_configure(user):
    p = user.guild_permissions
    return p.administrator or p.manage_roles or p.manage_guild


class ConfigView(discord.ui.View):
    def __init__(self, bot, guild, owner_id, selected, page=0):
        super().__init__(timeout=600)
        self.bot, self.guild, self.owner_id, self.selected, self.page = bot, guild, owner_id, set(selected), page
        self.pool = candidates(guild)
        self.pages = max(1, (len(self.pool) + MAX_ROLES - 1) // MAX_ROLES)
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        chunk = self.pool[self.page * MAX_ROLES:(self.page + 1) * MAX_ROLES]
        if chunk:
            sel = discord.ui.Select(placeholder=f"Roles members may claim (page {self.page + 1}/{self.pages})", min_values=0,
                                    max_values=len(chunk),
                                    options=[discord.SelectOption(label=r.name[:100], value=str(r.id), default=r.id in self.selected)
                                             for r in chunk])
            sel.callback = self.on_select
            self.add_item(sel)
        buttons = [("◀", discord.ButtonStyle.secondary, self.on_prev, self.page <= 0),
                   ("▶", discord.ButtonStyle.secondary, self.on_next, self.page >= self.pages - 1),
                   ("💾 Save", discord.ButtonStyle.success, self.on_save, False),
                   ("📢 Save & post panel here", discord.ButtonStyle.primary, self.on_post, False),
                   ("🧹 Clear", discord.ButtonStyle.danger, self.on_clear, False)]
        for label, style, cb, disabled in buttons:
            b = discord.ui.Button(label=label, style=style, disabled=disabled)
            b.callback = cb
            self.add_item(b)

    def text(self):
        names = [r.name for r in self.pool if r.id in self.selected]
        hid = hidden_count(self.guild)
        return (f"**Self-service roles: configuration**\nI found **{len(self.pool)}** role(s) I can safely offer"
                + (f" (and hid {hid} that are too powerful or above my own role)" if hid else "") + ".\n"
                f"Selected ({len(self.selected)}/{MAX_ROLES}): " + (", ".join(names)[:900] or "*none*"))

    async def _guard(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the admin who opened this menu can use it.", ephemeral=True)
            return False
        return True

    async def on_select(self, interaction):
        if not await self._guard(interaction):
            return
        chunk_ids = {r.id for r in self.pool[self.page * MAX_ROLES:(self.page + 1) * MAX_ROLES]}
        self.selected = (self.selected - chunk_ids) | {int(v) for v in interaction.data.get("values", [])}
        self.rebuild()
        await interaction.response.edit_message(content=self.text(), view=self)

    async def on_prev(self, interaction):
        if await self._guard(interaction):
            self.page = max(0, self.page - 1)
            self.rebuild()
            await interaction.response.edit_message(content=self.text(), view=self)

    async def on_next(self, interaction):
        if await self._guard(interaction):
            self.page = min(self.pages - 1, self.page + 1)
            self.rebuild()
            await interaction.response.edit_message(content=self.text(), view=self)

    async def on_clear(self, interaction):
        if await self._guard(interaction):
            self.selected = set()
            self.rebuild()
            await interaction.response.edit_message(content=self.text(), view=self)

    def _save(self):
        if len(self.selected) > MAX_ROLES:
            return False
        cfg = self.bot.config.setdefault(str(self.guild.id), {})
        old = cfg.get("getrole") or {}
        ordered = [r.id for r in self.pool if r.id in self.selected]
        cfg["getrole"] = {**old, "role_ids": ordered}
        self.bot.persist()
        return True

    async def on_save(self, interaction):
        if not await self._guard(interaction):
            return
        if not self._save():
            await interaction.response.send_message(f"Please choose at most {MAX_ROLES} roles.", ephemeral=True)
            return
        await refresh_panel(self.bot, self.guild)
        await interaction.response.edit_message(content="✅ Saved. Members can now use `/get role`." + (
            " The public panel was updated." if (self.bot.config[str(self.guild.id)]["getrole"].get("message_id")) else
            " Use **Save & post panel here** (or `/get panel`) to add a public menu to a channel."), view=None)

    async def on_post(self, interaction):
        if not await self._guard(interaction):
            return
        if not self._save():
            await interaction.response.send_message(f"Please choose at most {MAX_ROLES} roles.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        msg = await post_panel(self.bot, self.guild, interaction.channel)
        await interaction.edit_original_response(content=msg, view=None)


async def post_panel(bot, guild, channel):
    roles = allowed_roles(bot, guild)
    if not roles:
        return "⚠️ No roles are selected yet. Run `/get config` first."
    try:
        msg = await channel.send(embed=panel_embed(roles), view=PanelView(bot, roles))
    except discord.HTTPException as e:
        return f"❌ I could not post in {channel.mention}: {e}"
    cfg = bot.config.setdefault(str(guild.id), {}).setdefault("getrole", {})
    old = (cfg.get("channel_id"), cfg.get("message_id"))
    cfg["channel_id"], cfg["message_id"] = channel.id, msg.id
    bot.persist()
    if old[1] and old[0] == channel.id:                                  # replace the previous panel of this channel
        try:
            prev = await channel.fetch_message(old[1])
            await prev.delete()
        except discord.HTTPException:
            pass
    return f"✅ Panel posted in {channel.mention}."


async def refresh_panel(bot, guild):
    cfg = (bot.config.get(str(guild.id)) or {}).get("getrole") or {}
    ch = guild.get_channel(int(cfg["channel_id"])) if cfg.get("channel_id") else None
    if ch is None or not cfg.get("message_id"):
        return
    roles = allowed_roles(bot, guild)
    try:
        msg = await ch.fetch_message(int(cfg["message_id"]))
        await msg.edit(embed=panel_embed(roles), view=PanelView(bot, roles))
    except discord.HTTPException:
        pass


def register(bot):
    try:                                                                  # replace the old, server-specific /get role
        bot.tree.remove_command("get")
    except Exception:
        pass
    group = app_commands.Group(name="get", description="Self-service roles", guild_only=True)

    @group.command(name="role", description="Get or drop one of the roles this server offers")
    async def get_role(inter: discord.Interaction):
        roles = allowed_roles(bot, inter.guild)
        if not roles:
            await inter.response.send_message("❌ This server has not chosen any self-service roles yet.\n"
                                              "An admin can set them up with `/get config`.", ephemeral=True)
            return
        view = discord.ui.View(timeout=180)
        view.add_item(ClaimSelect(bot, roles, inter.user))
        await inter.response.send_message("🎭 **Choose your roles**\nSelect a role to get it, or one you already have to drop it.",
                                          view=view, ephemeral=True)

    @group.command(name="config", description="(Admin) Choose which roles members can claim")
    async def get_config(inter: discord.Interaction):
        if not can_configure(inter.user):
            await inter.response.send_message("You need the **Manage Roles** permission.", ephemeral=True)
            return
        current = {r.id for r in allowed_roles(bot, inter.guild)}          # the current choice (or the old built-in list)
        view = ConfigView(bot, inter.guild, inter.user.id, current)
        if not view.pool:
            await inter.response.send_message("I could not find any role I am allowed to hand out.\n"
                                              "Move my role **above** the roles you want to offer (Server Settings → Roles).",
                                              ephemeral=True)
            return
        await inter.response.send_message(view.text(), view=view, ephemeral=True)

    @group.command(name="panel", description="(Admin) Post the public roles panel in this channel")
    async def get_panel(inter: discord.Interaction):
        if not can_configure(inter.user):
            await inter.response.send_message("You need the **Manage Roles** permission.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        await inter.followup.send(await post_panel(bot, inter.guild, inter.channel), ephemeral=True)

    bot.tree.add_command(group)
    started = {"v": False}
    original_on_ready = bot.on_ready

    async def on_ready():
        await original_on_ready()
        if not started["v"]:
            started["v"] = True
            bot.add_view(PanelView(bot))                                   # the public panels survive a restart

    bot.on_ready = on_ready
