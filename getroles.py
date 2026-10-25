"""Self-service roles for ANY server: /get role, /get config, /get panel.

  /get role     Everyone. A private menu: pick the roles you want to get (or drop).
  /get config   Admins (Manage Roles). GOGO lists EVERY role of the server, sorted and labelled, and you choose which ones
                members may claim. Then it can post a public panel with a menu.
  /get panel    Admins. Posts (or moves) the public panel in the current channel.

Every role is classified:
   ✅ normal         anybody with Manage Roles can offer it
   ⚠️ powerful       has permissions like Kick/Ban/Manage...: ONLY THE SERVER OWNER can offer it (clearly marked)
   ⛔ not possible   Administrator roles (never offered), bot-managed roles, and roles ABOVE GOGO's role (Discord forbids it;
                     move GOGO's role up and they appear)
Saved per server in guild_config.json -> "<server id>": {"getrole": {"role_ids": [...], "risky_ok": [...], ...}}
Servers that never ran /get config keep the previous behaviour, so nothing breaks. Every click re-checks the role.
"""
import discord
from discord import app_commands

PAGE = 25                                         # options of one Discord select menu
MAX_ROLES = 100                                   # 4 select menus in the public panel
HARD_BLOCK = ("administrator",)
RISKY = ("manage_guild", "manage_roles", "manage_channels", "manage_messages", "manage_webhooks", "manage_nicknames",
         "manage_expressions", "manage_emojis_and_stickers", "manage_threads", "manage_events", "kick_members", "ban_members",
         "moderate_members", "mention_everyone", "view_audit_log", "move_members", "mute_members", "deafen_members",
         "view_guild_insights")


# ---------------------------------------------------------------- classification
def risk(role):
    p = role.permissions
    if any(getattr(p, x, False) for x in HARD_BLOCK):
        return "blocked"
    return "risky" if any(getattr(p, x, False) for x in RISKY) else "safe"


def giveable(guild, role):
    """Can GOGO technically give this role? (Discord rules)"""
    me = guild.me
    return not (role.is_default() or role.managed or me is None or role >= me.top_role)


def survey(guild):
    """{'safe': [...], 'risky': [...], 'blocked': [...], 'above': [...], 'managed': [...]} (highest role first)"""
    out = {"safe": [], "risky": [], "blocked": [], "above": [], "managed": []}
    for r in sorted(guild.roles, key=lambda x: x.position, reverse=True):
        if r.is_default():
            continue
        if r.managed:
            out["managed"].append(r)
        elif not giveable(guild, r):
            out["above"].append(r)
        else:
            out[risk(r)].append(r)
    return out


def safe_role(guild, role):                       # kept for compatibility
    return giveable(guild, role) and risk(role) == "safe"


def candidates(guild):
    return survey(guild)["safe"]


def hidden_count(guild):
    s = survey(guild)
    return len(s["risky"]) + len(s["blocked"]) + len(s["above"]) + len(s["managed"])


def is_owner(guild, user):
    if user.id == guild.owner_id:
        return True
    try:
        import owner_ai
        return owner_ai.is_owner(guild, user)
    except Exception:
        return False


def allowed_roles(bot, guild):
    """The roles members can claim here: the admin's choice, else the old built-in list (if the server has one)."""
    cfg = (bot.config.get(str(guild.id)) or {})
    g = cfg.get("getrole") or {}
    if g.get("role_ids") is not None:
        ok = set(int(i) for i in g.get("risky_ok") or [])
        out = []
        for i in g["role_ids"]:
            r = guild.get_role(int(i))
            if r is None or not giveable(guild, r):
                continue
            kind = risk(r)
            if kind == "safe" or (kind == "risky" and r.id in ok):
                out.append(r)
        return out
    try:
        import rolesys
        return list(rolesys.giveable_roles(guild, bot.gcfg(guild)))
    except Exception:
        return []


# ---------------------------------------------------------------- member side
async def toggle(bot, interaction, role_ids):
    """Give the roles the member does not have, remove the ones he has. Only roles on the list are touched."""
    guild, member = interaction.guild, interaction.user
    allowed = {r.id: r for r in allowed_roles(bot, guild)}
    added, removed, refused = [], [], []
    for rid in role_ids:
        role = allowed.get(int(rid))
        if role is None or not giveable(guild, role) or risk(role) == "blocked":
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


def _options(roles, member=None):
    out = []
    for r in roles:
        desc = None
        if member is not None:
            desc = "✅ You have it: select to drop it" if r in member.roles else "Select to get it"
        out.append(discord.SelectOption(label=r.name[:100], value=str(r.id), description=desc))
    return out


def panel_embed(roles, guild=None):
    emb = discord.Embed(title="🎭  Get your roles", color=0x9B59B6,
                        description="Use the menus below to **get** or **drop** a role.\nChoosing a role you already have removes it.")
    emb.add_field(name="Available roles", value="\n".join(f"• {r.mention}" for r in roles)[:1000] or "None yet.", inline=False)
    emb.set_footer(text="Self-service roles • powered by GOGO")
    return emb


class ClaimSelect(discord.ui.Select):
    """One select menu (max 25 roles). Used by the public panel (custom_id) and by /get role (private)."""

    def __init__(self, bot, roles=None, member=None, custom_id=None, index=0, total=1):
        self.bot = bot
        opts = _options(roles, member) if roles else [discord.SelectOption(label="(roles)", value="0")]
        label = "🎭 Choose your roles" + (f" ({index + 1}/{total})" if total > 1 else "")
        extra = {"custom_id": custom_id} if custom_id else {}       # discord.py refuses custom_id=None: leave it out
        super().__init__(placeholder=label, min_values=1, max_values=max(1, len(opts)), options=opts, **extra)

    async def callback(self, interaction):
        await toggle(self.bot, interaction, self.values)


def _selects(bot, roles, member=None, persistent=False):
    chunks = [roles[i:i + PAGE] for i in range(0, min(len(roles), MAX_ROLES), PAGE)] or [[]]
    out = []
    for i, chunk in enumerate(chunks):
        out.append(ClaimSelect(bot, chunk, member, custom_id=f"gogo:get:panel{i}" if persistent else None, index=i, total=len(chunks)))
    return out


class PanelView(discord.ui.View):
    def __init__(self, bot, roles=None):
        super().__init__(timeout=None)
        if roles:
            for s in _selects(bot, roles, persistent=True):
                self.add_item(s)
        else:                                       # the copy registered at start-up: it only needs the 4 custom ids
            for i in range(MAX_ROLES // PAGE):
                self.add_item(ClaimSelect(bot, None, custom_id=f"gogo:get:panel{i}"))


# ---------------------------------------------------------------- admin side
def can_configure(user):
    p = user.guild_permissions
    return p.administrator or p.manage_roles or p.manage_guild


class ConfigView(discord.ui.View):
    def __init__(self, bot, guild, user, selected, page=0):
        super().__init__(timeout=600)
        self.bot, self.guild, self.owner_id, self.page = bot, guild, user.id, page
        self.is_owner = is_owner(guild, user)
        self.survey = survey(guild)
        self.pool = self.survey["safe"] + (self.survey["risky"] if self.is_owner else [])
        self.pool.sort(key=lambda r: r.position, reverse=True)
        risky_ids = {r.id for r in self.survey["risky"]}
        self.selected = set(selected)
        # powerful roles chosen earlier by the owner stay untouched when a normal admin edits the list
        self.locked = set() if self.is_owner else {i for i in self.selected if i in risky_ids}
        self.selected -= self.locked
        self.pages = max(1, (len(self.pool) + PAGE - 1) // PAGE)
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        chunk = self.pool[self.page * PAGE:(self.page + 1) * PAGE]
        risky_ids = {r.id for r in self.survey["risky"]}
        if chunk:
            sel = discord.ui.Select(placeholder=f"Roles members may claim (page {self.page + 1}/{self.pages})", min_values=0,
                                    max_values=len(chunk),
                                    options=[discord.SelectOption(label=r.name[:100], value=str(r.id), default=r.id in self.selected,
                                                                  description="⚠️ Powerful permissions (owner choice)" if r.id in risky_ids else None)
                                             for r in chunk])
            sel.callback = self.on_select
            self.add_item(sel)
        for label, style, cb, disabled in (("◀", discord.ButtonStyle.secondary, self.on_prev, self.page <= 0),
                                           ("▶", discord.ButtonStyle.secondary, self.on_next, self.page >= self.pages - 1),
                                           ("💾 Save", discord.ButtonStyle.success, self.on_save, False),
                                           ("📢 Save & post panel here", discord.ButtonStyle.primary, self.on_post, False),
                                           ("🧹 Clear", discord.ButtonStyle.danger, self.on_clear, False)):
            b = discord.ui.Button(label=label, style=style, disabled=disabled)
            b.callback = cb
            self.add_item(b)

    def text(self):
        s = self.survey
        names = [("⚠️" if r in s["risky"] else "") + r.name for r in self.pool if r.id in self.selected]
        total = sum(len(v) for v in s.values())
        lines = ["**Self-service roles: configuration**",
                 f"I scanned **{total}** role(s) of this server:",
                 f"✅ **{len(s['safe'])}** normal" + (f" · ⚠️ **{len(s['risky'])}** powerful" +
                                                      (" (you can offer them: marked ⚠️)" if self.is_owner else " (only the server owner can offer them)") if s["risky"] else "")]
        if s["above"]:
            lines.append(f"⛔ **{len(s['above'])}** above my role, I cannot give them: " + ", ".join(r.name for r in s["above"][:8])
                         + ("…" if len(s["above"]) > 8 else "") + "\n   ↳ move my role **up** in Server Settings → Roles to see them here.")
        if s["blocked"]:
            lines.append(f"⛔ **{len(s['blocked'])}** Administrator role(s), never offered: " + ", ".join(r.name for r in s["blocked"][:5]))
        if s["managed"]:
            lines.append(f"🤖 **{len(s['managed'])}** bot-managed role(s), skipped.")
        if self.locked:
            lines.append(f"🔒 {len(self.locked)} powerful role(s) set by the owner are kept as they are.")
        lines.append(f"\n**Selected ({len(self.selected) + len(self.locked)}/{MAX_ROLES}):** " + (", ".join(names)[:600] or "*none*"))
        return "\n".join(lines)[:1990]

    async def _guard(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the admin who opened this menu can use it.", ephemeral=True)
            return False
        return True

    async def on_select(self, interaction):
        if not await self._guard(interaction):
            return
        chunk_ids = {r.id for r in self.pool[self.page * PAGE:(self.page + 1) * PAGE]}
        picked = {int(v) for v in interaction.data.get("values", []) if str(v).isdigit()} & chunk_ids   # only what this page offers
        self.selected = (self.selected - chunk_ids) | picked
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
        risky_ids = {r.id for r in self.survey["risky"]}
        total = self.selected | self.locked
        if not self.is_owner:                                    # a normal admin can never add a powerful role
            total = {i for i in total if i not in risky_ids or i in self.locked}
        if len(total) > MAX_ROLES:
            return False
        cfg = self.bot.config.setdefault(str(self.guild.id), {})
        old = cfg.get("getrole") or {}
        risky_ids = {r.id for r in self.survey["risky"]}
        ordered = [r.id for r in sorted(self.guild.roles, key=lambda x: x.position, reverse=True) if r.id in total]
        cfg["getrole"] = {**old, "role_ids": ordered, "risky_ok": [i for i in ordered if i in risky_ids]}
        self.bot.persist()
        return True

    async def on_save(self, interaction):
        if not await self._guard(interaction):
            return
        if not self._save():
            await interaction.response.send_message(f"Please choose at most {MAX_ROLES} roles.", ephemeral=True)
            return
        await refresh_panel(self.bot, self.guild)
        done = self.bot.config[str(self.guild.id)]["getrole"].get("message_id")
        await interaction.response.edit_message(content="✅ Saved. Members can now use `/get role`." + (
            " The public panel was updated." if done else
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
        msg = await channel.send(embed=panel_embed(roles, guild), view=PanelView(bot, roles))
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
        await msg.edit(embed=panel_embed(roles, guild), view=PanelView(bot, roles))
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
        for s in _selects(bot, roles, inter.user):
            view.add_item(s)
        await inter.response.send_message("🎭 **Choose your roles**\nSelect a role to get it, or one you already have to drop it.",
                                          view=view, ephemeral=True)

    @group.command(name="config", description="(Admin) Choose which roles members can claim")
    async def get_config(inter: discord.Interaction):
        if not can_configure(inter.user):
            await inter.response.send_message("You need the **Manage Roles** permission.", ephemeral=True)
            return
        current = {r.id for r in allowed_roles(bot, inter.guild)}          # the current choice (or the old built-in list)
        view = ConfigView(bot, inter.guild, inter.user, current)
        if not view.pool:
            await inter.response.send_message(view.text() + "\n\nI have no role I can offer yet. Move my role **above** the roles "
                                              "you want to offer (Server Settings → Roles).", ephemeral=True)
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
