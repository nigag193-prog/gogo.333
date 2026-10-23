import asyncio
import datetime
import os
import sqlite3
import time

import discord

APP_DIR = os.path.join(os.path.expanduser("~"), "DiscordAIBuilder")
os.makedirs(APP_DIR, exist_ok=True)


def level_for(xp):
    return int((xp / 50) ** 0.5)


class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS xp(guild INTEGER, user INTEGER, xp INTEGER DEFAULT 0,"
            " last REAL DEFAULT 0, PRIMARY KEY(guild, user))")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS warns(id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " guild INTEGER, user INTEGER, reason TEXT, ts REAL)")
        self.conn.commit()

    def add_xp(self, guild, user, amount, cooldown=60):
        """Returns (old_xp, new_xp), or None while the user is on cooldown."""
        now = time.time()
        row = self.conn.execute(
            "SELECT xp, last FROM xp WHERE guild=? AND user=?", (guild, user)).fetchone()
        xp, last = row if row else (0, 0)
        if now - last < cooldown:
            return None
        new = xp + amount
        self.conn.execute(
            "INSERT INTO xp(guild, user, xp, last) VALUES(?,?,?,?) "
            "ON CONFLICT(guild, user) DO UPDATE SET xp=?, last=?",
            (guild, user, new, now, new, now))
        self.conn.commit()
        return xp, new

    def rank(self, guild, user):
        row = self.conn.execute(
            "SELECT xp FROM xp WHERE guild=? AND user=?", (guild, user)).fetchone()
        xp = row[0] if row else 0
        ahead = self.conn.execute(
            "SELECT COUNT(*) FROM xp WHERE guild=? AND xp>?", (guild, xp)).fetchone()[0]
        return ahead + 1, xp

    def top(self, guild, n=10):
        return self.conn.execute(
            "SELECT user, xp FROM xp WHERE guild=? ORDER BY xp DESC LIMIT ?", (guild, n)).fetchall()

    def add_warn(self, guild, user, reason):
        self.conn.execute(
            "INSERT INTO warns(guild, user, reason, ts) VALUES(?,?,?,?)",
            (guild, user, reason, time.time()))
        self.conn.commit()

    def warns(self, guild, user):
        return self.conn.execute(
            "SELECT reason, ts FROM warns WHERE guild=? AND user=? ORDER BY id DESC LIMIT 10",
            (guild, user)).fetchall()


# ---------------- tickets ----------------
class TicketPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open ticket", style=discord.ButtonStyle.green,
                       custom_id="ticket:open", emoji="🎫")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild, user = interaction.guild, interaction.user
        for ch in guild.text_channels:
            if ch.topic == f"ticket:{user.id}":
                await interaction.response.send_message(
                    f"You already have a ticket: {ch.mention}", ephemeral=True)
                return
        cfg = interaction.client.config.get(str(guild.id), {}).get("tickets", {})
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, attach_files=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True),
        }
        for rid in cfg.get("staff_role_ids", []):
            role = guild.get_role(rid)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True)
        channel = await guild.create_text_channel(
            f"ticket-{user.name}"[:90], category=interaction.channel.category,
            overwrites=overwrites, topic=f"ticket:{user.id}")
        await channel.send(
            f"{user.mention} the team will be with you shortly. Describe your issue here.",
            view=TicketClose())
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)


class TicketClose(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close ticket", style=discord.ButtonStyle.red,
                       custom_id="ticket:close", emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        owner = (interaction.channel.topic or "") == f"ticket:{interaction.user.id}"
        if not (owner or interaction.user.guild_permissions.manage_messages):
            await interaction.response.send_message("Only the ticket owner or staff can close it.", ephemeral=True)
            return
        await interaction.response.send_message("Closing in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete(reason="ticket closed")



# ---------------- verification ----------------
class VerifyPanel(discord.ui.View):
    def __init__(self, label="Verify me"):
        super().__init__(timeout=None)
        self.children[0].label = label[:80]

    @discord.ui.button(label="Verify me", style=discord.ButtonStyle.green,
                       custom_id="verify:go", emoji="✅")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild, member = interaction.guild, interaction.user
        cfg = interaction.client.config.get(str(guild.id), {}).get("verification", {})
        verified = guild.get_role(cfg.get("verified_role_id") or 0)
        unverified = guild.get_role(cfg.get("unverified_role_id") or 0)
        if verified is None:
            await interaction.response.send_message(
                "Verification is not configured. Tell the staff.", ephemeral=True)
            return
        if verified in member.roles and (unverified is None or unverified not in member.roles):
            await interaction.response.send_message("You are already verified.", ephemeral=True)
            return
        try:
            await member.add_roles(verified, reason="verified")
            if unverified and unverified in member.roles:
                await member.remove_roles(unverified, reason="verified")
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"Could not verify you (the bot role must be above the roles): {e}", ephemeral=True)
            return
        await interaction.response.send_message(
            "You are verified. Welcome! All channels are now unlocked.", ephemeral=True)
        await interaction.client.guild_log(guild, f"Verified: {member} ({member.id})")

# ---------------- role buttons ----------------
class RoleButton(discord.ui.Button):
    def __init__(self, role_id, label):
        super().__init__(label=label[:80], style=discord.ButtonStyle.secondary,
                         custom_id=f"rolebtn:{role_id}")
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id)
        if role is None:
            await interaction.response.send_message("This role no longer exists.", ephemeral=True)
            return
        member = interaction.user
        if role in member.roles:
            await member.remove_roles(role)
            msg = f"Removed role {role.name}"
        else:
            await member.add_roles(role)
            msg = f"Added role {role.name}"
        await interaction.response.send_message(msg, ephemeral=True)


class RolePanel(discord.ui.View):
    def __init__(self, roles):
        super().__init__(timeout=None)
        for rid, label in roles[:25]:
            self.add_item(RoleButton(rid, label))


# ---------------- text commands: levels + moderation ----------------
async def handle_command(bot, message):
    """Handles !rank !top !kick !ban !mute !unmute !warn !warns !clear. True if handled."""
    tokens = message.content.split()
    cmd = tokens[0][1:].lower()
    g, a = message.guild, message.author
    perms = a.guild_permissions
    target = message.mentions[0] if message.mentions else None
    send = message.channel.send

    if cmd == "rank":
        member = target or a
        pos, xp = bot.db.rank(g.id, member.id)
        await send(f"{member.display_name}: level {level_for(xp)} - {xp} XP - rank #{pos}")
        return True
    if cmd == "top":
        rows = bot.db.top(g.id)
        if not rows:
            await send("No XP yet.")
            return True
        lines = []
        for i, (uid, xp) in enumerate(rows, 1):
            m = g.get_member(uid)
            lines.append(f"{i}. {m.display_name if m else uid} - level {level_for(xp)} ({xp} XP)")
        await send("\n".join(lines))
        return True

    allowed = {
        "kick": perms.kick_members, "ban": perms.ban_members,
        "mute": perms.moderate_members, "unmute": perms.moderate_members,
        "warn": perms.moderate_members, "warns": perms.moderate_members,
        "clear": perms.manage_messages,
    }
    if cmd not in allowed:
        return False
    if not allowed[cmd]:
        await send("You don't have permission for that.", delete_after=5)
        return True

    if cmd == "clear":
        n = int(tokens[1]) if len(tokens) > 1 and tokens[1].isdigit() else 0
        if not 1 <= n <= 100:
            await send("Usage: !clear <1-100>", delete_after=5)
            return True
        deleted = await message.channel.purge(limit=n + 1)
        await send(f"Deleted {len(deleted) - 1} messages.", delete_after=5)
        await bot.guild_log(g, f"{a} cleared {len(deleted) - 1} messages in {message.channel.mention}")
        return True

    if target is None:
        await send(f"Usage: !{cmd} @user", delete_after=8)
        return True
    if (target.id == g.owner_id or target.id == bot.user.id
            or (a.id != g.owner_id and target.top_role >= a.top_role)):
        await send("You can't do that to this member.", delete_after=5)
        return True

    reason = " ".join(tokens[2:]) or "no reason"
    try:
        if cmd == "kick":
            await target.kick(reason=reason)
            msg = f"{target} was kicked ({reason})"
        elif cmd == "ban":
            await target.ban(reason=reason)
            msg = f"{target} was banned ({reason})"
        elif cmd == "mute":
            mins = int(tokens[2]) if len(tokens) > 2 and tokens[2].isdigit() else 10
            await target.timeout(datetime.timedelta(minutes=mins), reason=f"by {a}")
            msg = f"{target} was muted for {mins} min"
        elif cmd == "unmute":
            await target.timeout(None)
            msg = f"{target} was unmuted"
        elif cmd == "warn":
            bot.db.add_warn(g.id, target.id, reason)
            msg = f"{target} was warned ({reason})"
        else:  # warns
            rows = bot.db.warns(g.id, target.id)
            msg = "\n".join(
                f"- {r} ({datetime.datetime.fromtimestamp(t):%Y-%m-%d})" for r, t in rows
            ) or "No warnings."
    except discord.HTTPException as e:
        await send(f"Discord refused: {e}", delete_after=8)
        return True
    await send(msg)
    if cmd != "warns":
        await bot.guild_log(g, f"{a}: {msg}")
    return True
