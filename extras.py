"""Extra modules: LFG teams, clip starboard, coins + shop, anti-nuke, lockdown."""
import asyncio
import random
import time

import discord
from discord import app_commands

COIN = "🪙"


def init_tables(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS coins(guild INTEGER, user INTEGER, balance INTEGER DEFAULT 0,"
        " last_msg REAL DEFAULT 0, last_daily REAL DEFAULT 0, PRIMARY KEY(guild, user))")
    conn.execute("CREATE TABLE IF NOT EXISTS starred(guild INTEGER, message INTEGER,"
                 " PRIMARY KEY(guild, message))")
    conn.execute("CREATE TABLE IF NOT EXISTS temp_vc(guild INTEGER, channel INTEGER PRIMARY KEY,"
                 " created REAL)")
    conn.commit()


def conf(bot, guild, key):
    return bot.gcfg(guild).get(key) or {}


def _row(conn, guild, user):
    r = conn.execute("SELECT balance, last_msg, last_daily FROM coins WHERE guild=? AND user=?",
                     (guild, user)).fetchone()
    return r or (0, 0.0, 0.0)


def _save(conn, guild, user, balance=None, last_msg=None, last_daily=None):
    b, lm, ld = _row(conn, guild, user)
    b = b if balance is None else balance
    lm = lm if last_msg is None else last_msg
    ld = ld if last_daily is None else last_daily
    conn.execute(
        "INSERT INTO coins(guild, user, balance, last_msg, last_daily) VALUES(?,?,?,?,?) "
        "ON CONFLICT(guild, user) DO UPDATE SET balance=?, last_msg=?, last_daily=?",
        (guild, user, b, lm, ld, b, lm, ld))
    conn.commit()


# ------------------------------------------------------------------ alerts / anti-nuke
async def alert_staff(bot, guild, text):
    """Always reaches the staff, even when another bot handles the normal logs."""
    ch = guild.get_channel((bot.gcfg(guild).get("logs") or {}).get("channel_id") or 0)
    ch = ch or guild.system_channel
    if ch is None:
        return
    ids = bot.gcfg(guild).get("staff_role_ids") or []
    ping = " ".join(f"<@&{i}>" for i in ids)
    try:
        await ch.send(f"{ping}\n{text}"[:1900])
    except discord.HTTPException:
        pass


async def nuke_hit(bot, guild, executor, action):
    """Count destructive actions per person; strip their dangerous roles above the limit."""
    cfg = conf(bot, guild, "antinuke")
    if not cfg.get("enabled") or guild.id in bot.building:
        return
    if executor is None or executor.bot or executor.id == guild.owner_id:
        return
    if bot.user and executor.id == bot.user.id:
        return
    member = guild.get_member(executor.id)
    white = set(cfg.get("whitelist_role_ids") or [])
    if member and any(r.id in white for r in member.roles):
        return
    q = bot.mass[(guild.id, executor.id)]
    now = time.time()
    q.append(now)
    window = int(cfg.get("seconds", 30))
    while q and now - q[0] > window:
        q.popleft()
    if len(q) < int(cfg.get("max_actions", 3)):
        return
    q.clear()
    stripped = []
    if member:
        top = guild.me.top_role
        risky = [r for r in member.roles
                 if not r.is_default() and not r.managed and r < top and (
                     r.permissions.administrator or r.permissions.manage_channels
                     or r.permissions.manage_roles or r.permissions.manage_guild
                     or r.permissions.kick_members or r.permissions.ban_members)]
        if risky:
            try:
                await member.remove_roles(*risky, reason="Anti-nuke: mass destructive actions")
                stripped = [r.name for r in risky]
            except discord.HTTPException:
                pass
    await alert_staff(
        bot, guild,
        f"🚨 **Anti-nuke**: {executor.mention} did {action} too many times in {window}s.\n"
        + (f"Removed their roles: {', '.join(stripped)}" if stripped
           else "I could not remove their roles (my role must be above theirs). Check the server now!"))


# ------------------------------------------------------------------ economy
async def earn_message(bot, message):
    eco = conf(bot, message.guild, "economy")
    if not eco.get("enabled"):
        return
    conn = bot.db.conn
    gid, uid = message.guild.id, message.author.id
    b, lm, _ = _row(conn, gid, uid)
    now = time.time()
    if now - lm < 60:
        return
    lo, hi = (eco.get("per_message") or [1, 3])[:2]
    _save(conn, gid, uid, balance=b + random.randint(int(lo), int(hi)), last_msg=now)


async def background(bot):
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await _tick(bot)
        except Exception as e:  # never kill the loop
            bot.log(f"Background task error: {e}")
        await asyncio.sleep(60)


async def _tick(bot):
    conn = bot.db.conn
    now = time.time()
    for guild in bot.guilds:
        eco = conf(bot, guild, "economy")
        vpm = int(eco.get("voice_per_minute", 0)) if eco.get("enabled") else 0
        if vpm <= 0:
            continue
        for vc in guild.voice_channels:
            if "afk" in vc.name.lower() or (guild.afk_channel and vc.id == guild.afk_channel.id):
                continue
            humans = [m for m in vc.members if not m.bot]
            if len(humans) < 2:
                continue  # nobody gets coins for sitting alone
            for m in humans:
                if m.voice and (m.voice.self_deaf or m.voice.deaf):
                    continue
                b, _, _ = _row(conn, guild.id, m.id)
                _save(conn, guild.id, m.id, balance=b + vpm)
    for gid, cid, created in conn.execute("SELECT guild, channel, created FROM temp_vc").fetchall():
        guild = bot.get_guild(gid)
        ch = guild.get_channel(cid) if guild else None
        if ch is None:
            conn.execute("DELETE FROM temp_vc WHERE channel=?", (cid,))
            conn.commit()
        elif not ch.members and now - created > 180:
            try:
                await ch.delete(reason="LFG team channel is empty")
            except discord.HTTPException:
                pass
            conn.execute("DELETE FROM temp_vc WHERE channel=?", (cid,))
            conn.commit()


# ------------------------------------------------------------------ LFG
class LfgView(discord.ui.View):
    def __init__(self, bot, owner, game, rank, size, note, cfg):
        super().__init__(timeout=7200)
        self.bot, self.owner, self.game, self.rank = bot, owner, game, rank
        self.size, self.note, self.cfg = size, note, cfg
        self.players = [owner]
        self.message = None
        self.done = False

    def embed(self, channel=None):
        e = discord.Embed(title=f"🎮 {self.game}", colour=0x7C3AED)
        e.add_field(name="Rank", value=self.rank)
        e.add_field(name="Team", value=f"{len(self.players)}/{self.size}")
        e.add_field(name="Players", value="\n".join(p.mention for p in self.players), inline=False)
        if self.note:
            e.add_field(name="Note", value=self.note, inline=False)
        e.set_footer(text=f"Looking for group by {self.owner.display_name}")
        if channel:
            e.add_field(name="Voice", value=channel.mention, inline=False)
            e.colour = 0x22C55E
        return e

    async def _finish(self):
        guild = self.owner.guild
        self.done = True
        cat = guild.get_channel(self.cfg.get("category_id") or 0)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True,
                                                  move_members=True),
        }
        for p in self.players:
            overwrites[p] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)
        name = f"🎮・{self.game[:20]} | {self.owner.display_name[:14]}"
        vc = await guild.create_voice_channel(name, category=cat if isinstance(
            cat, discord.CategoryChannel) else None, overwrites=overwrites, user_limit=self.size,
            reason="LFG team")
        conn = self.bot.db.conn
        conn.execute("INSERT OR REPLACE INTO temp_vc(guild, channel, created) VALUES(?,?,?)",
                     (guild.id, vc.id, time.time()))
        conn.commit()
        for p in self.players:
            if p.voice and p.voice.channel:
                try:
                    await p.move_to(vc)
                except discord.HTTPException:
                    pass
        for child in self.children:
            child.disabled = True
        if self.message:
            await self.message.edit(content="✅ Team ready! " + " ".join(p.mention for p in self.players),
                                    embed=self.embed(vc), view=self)
        self.stop()

    @discord.ui.button(label="Join", style=discord.ButtonStyle.green, emoji="➕")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.done:
            await interaction.response.send_message("This team is already closed.", ephemeral=True)
        elif interaction.user in self.players:
            await interaction.response.send_message("You are already in this team.", ephemeral=True)
        elif len(self.players) >= self.size:
            await interaction.response.send_message("The team is full.", ephemeral=True)
        else:
            self.players.append(interaction.user)
            await interaction.response.edit_message(embed=self.embed(), view=self)
            if len(self.players) >= self.size:
                await self._finish()

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.gray)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.done or interaction.user not in self.players:
            await interaction.response.send_message("You are not in this team.", ephemeral=True)
        elif interaction.user == self.owner:
            await interaction.response.send_message("The creator cannot leave; press Cancel.", ephemeral=True)
        else:
            self.players.remove(interaction.user)
            await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Start now", style=discord.ButtonStyle.blurple, emoji="▶️")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.owner or self.done:
            await interaction.response.send_message("Only the creator can start the team.", ephemeral=True)
            return
        if len(self.players) < 2:
            await interaction.response.send_message("Wait for at least one more player.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._finish()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.owner or self.done:
            await interaction.response.send_message("Only the creator can cancel.", ephemeral=True)
            return
        self.done = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❌ Cancelled.", embed=None, view=self)
        self.stop()


# ------------------------------------------------------------------ starboard
async def on_reaction(bot, payload):
    if not payload.guild_id:
        return
    guild = bot.get_guild(payload.guild_id)
    cfg = conf(bot, guild, "starboard") if guild else {}
    if not cfg or str(payload.emoji) != cfg.get("emoji", "⭐"):
        return
    if payload.channel_id not in (cfg.get("source_ids") or []):
        return
    channel = guild.get_channel(payload.channel_id)
    target = guild.get_channel(cfg.get("target_id") or 0)
    if channel is None or target is None:
        return
    try:
        msg = await channel.fetch_message(payload.message_id)
    except discord.HTTPException:
        return
    if msg.author.bot:
        return
    count = 0
    for r in msg.reactions:
        if str(r.emoji) == cfg.get("emoji", "⭐"):
            count = len([u async for u in r.users() if u.id != msg.author.id and not u.bot])
    if count < int(cfg.get("threshold", 3)):
        return
    cur = bot.db.conn.execute("INSERT OR IGNORE INTO starred(guild, message) VALUES(?,?)",
                              (guild.id, msg.id))
    bot.db.conn.commit()
    if cur.rowcount == 0:
        return  # already posted
    emb = discord.Embed(description=msg.content[:3500], colour=0xF1C40F, timestamp=msg.created_at)
    emb.set_author(name=msg.author.display_name, icon_url=msg.author.display_avatar.url)
    image = next((a for a in msg.attachments if (a.content_type or "").startswith("image/")), None)
    if image:
        emb.set_image(url=image.url)
    others = [a.url for a in msg.attachments if a is not image]
    if others:
        emb.add_field(name="Files", value="\n".join(others)[:900], inline=False)
    emb.add_field(name="Source", value=f"[Jump to clip]({msg.jump_url}) in {channel.mention}")
    emb.set_footer(text=f"{cfg.get('emoji', '⭐')} {count}")
    try:
        await target.send(embed=emb)
    except discord.HTTPException:
        pass


# ------------------------------------------------------------------ lockdown
async def do_lockdown(bot, guild, lock):
    gcfg = bot.config.setdefault(str(guild.id), {})
    state = gcfg.setdefault("lockdown", {})
    everyone = guild.default_role
    ver = guild.get_role((gcfg.get("verification") or {}).get("verified_role_id") or 0)
    count = 0
    if lock:
        for ch in guild.text_channels:
            ow_e = ch.overwrites_for(everyone)
            if ow_e.view_channel is False or str(ch.id) in state:
                continue  # private channel or already locked
            ow_m = ch.overwrites_for(ver) if ver else None
            state[str(ch.id)] = {"e": ow_e.send_messages, "m": ow_m.send_messages if ow_m else None}
            try:
                ow_e.send_messages = False
                await ch.set_permissions(everyone, overwrite=ow_e, reason="lockdown")
                if ow_m is not None:
                    ow_m.send_messages = False
                    await ch.set_permissions(ver, overwrite=ow_m, reason="lockdown")
                count += 1
            except discord.HTTPException:
                state.pop(str(ch.id), None)
    else:
        for cid, old in list(state.items()):
            ch = guild.get_channel(int(cid))
            if ch is not None:
                try:
                    ow_e = ch.overwrites_for(everyone)
                    ow_e.send_messages = old.get("e")
                    await ch.set_permissions(everyone, overwrite=None if ow_e.is_empty() else ow_e,
                                             reason="unlock")
                    if ver is not None:
                        ow_m = ch.overwrites_for(ver)
                        ow_m.send_messages = old.get("m")
                        await ch.set_permissions(ver, overwrite=None if ow_m.is_empty() else ow_m,
                                                 reason="unlock")
                    count += 1
                except discord.HTTPException:
                    continue
            state.pop(cid, None)
    bot.persist()
    return count


# ------------------------------------------------------------------ slash commands
def register(bot):
    tree = bot.tree
    none_mentions = discord.AllowedMentions.none()

    def eco_off(interaction):
        return not conf(bot, interaction.guild, "economy").get("enabled")

    @tree.command(name="balance", description="Show your coins or someone else's")
    @app_commands.guild_only()
    async def balance(interaction: discord.Interaction, member: discord.Member | None = None):
        if eco_off(interaction):
            await interaction.response.send_message("The coin system is off.", ephemeral=True)
            return
        m = member or interaction.user
        b, _, _ = _row(bot.db.conn, interaction.guild.id, m.id)
        await interaction.response.send_message(f"{m.mention} has **{b}** {COIN}",
                                                allowed_mentions=none_mentions)

    @tree.command(name="daily", description="Claim your daily coins")
    @app_commands.guild_only()
    async def daily(interaction: discord.Interaction):
        if eco_off(interaction):
            await interaction.response.send_message("The coin system is off.", ephemeral=True)
            return
        eco = conf(bot, interaction.guild, "economy")
        gid, uid = interaction.guild.id, interaction.user.id
        b, _, ld = _row(bot.db.conn, gid, uid)
        wait = 20 * 3600 - (time.time() - ld)
        if wait > 0:
            await interaction.response.send_message(
                f"Come back in {int(wait // 3600)}h {int(wait % 3600 // 60)}m.", ephemeral=True)
            return
        amount = int(eco.get("daily", 100))
        _save(bot.db.conn, gid, uid, balance=b + amount, last_daily=time.time())
        await interaction.response.send_message(f"You got **{amount}** {COIN}! Balance: **{b + amount}**",
                                                ephemeral=True)

    @tree.command(name="shop", description="See what you can buy with coins")
    @app_commands.guild_only()
    async def shop(interaction: discord.Interaction):
        eco = conf(bot, interaction.guild, "economy")
        items = eco.get("shop") or []
        if not eco.get("enabled") or not items:
            await interaction.response.send_message("The shop is empty.", ephemeral=True)
            return
        lines = [f"**{i['name']}**: {i['price']} {COIN}" for i in items]
        emb = discord.Embed(title="🛒 Shop", description="\n".join(lines) + "\n\nBuy with `/buy`.",
                            colour=0x7C3AED)
        await interaction.response.send_message(embed=emb, ephemeral=True)

    @tree.command(name="buy", description="Buy an item from the shop")
    @app_commands.guild_only()
    async def buy(interaction: discord.Interaction, item: str):
        eco = conf(bot, interaction.guild, "economy")
        shop_items = eco.get("shop") or []
        it = next((s for s in shop_items if s["name"].lower() == item.lower()), None)
        if not eco.get("enabled") or it is None:
            await interaction.response.send_message("That item does not exist. Use `/shop`.", ephemeral=True)
            return
        role = interaction.guild.get_role(it["role_id"])
        member = interaction.user
        if role is None:
            await interaction.response.send_message("That role no longer exists.", ephemeral=True)
            return
        if role in member.roles:
            await interaction.response.send_message("You already own it.", ephemeral=True)
            return
        gid = interaction.guild.id
        b, _, _ = _row(bot.db.conn, gid, member.id)
        if b < it["price"]:
            await interaction.response.send_message(
                f"You need {it['price'] - b} more {COIN}.", ephemeral=True)
            return
        try:
            if eco.get("exclusive", True):
                owned = {s["role_id"] for s in shop_items}
                others = [r for r in member.roles if r.id in owned and r.id != role.id]
                if others:
                    await member.remove_roles(*others, reason="shop")
            await member.add_roles(role, reason="shop purchase")
        except discord.HTTPException:
            await interaction.response.send_message(
                "I cannot give that role (my role must be above it).", ephemeral=True)
            return
        _save(bot.db.conn, gid, member.id, balance=b - it["price"])
        await interaction.response.send_message(
            f"Bought **{it['name']}**! Balance: **{b - it['price']}** {COIN}", ephemeral=True)

    @buy.autocomplete("item")
    async def buy_autocomplete(interaction: discord.Interaction, current: str):
        items = conf(bot, interaction.guild, "economy").get("shop") or []
        return [app_commands.Choice(name=f"{i['name']} ({i['price']})"[:100], value=i["name"][:100])
                for i in items if current.lower() in i["name"].lower()][:25]

    @tree.command(name="richest", description="Top 10 richest members")
    @app_commands.guild_only()
    async def richest(interaction: discord.Interaction):
        if eco_off(interaction):
            await interaction.response.send_message("The coin system is off.", ephemeral=True)
            return
        rows = bot.db.conn.execute(
            "SELECT user, balance FROM coins WHERE guild=? ORDER BY balance DESC LIMIT 10",
            (interaction.guild.id,)).fetchall()
        text = "\n".join(f"**{i}.** <@{u}>: {b} {COIN}" for i, (u, b) in enumerate(rows, 1)) or "Nobody yet."
        await interaction.response.send_message(embed=discord.Embed(
            title="💰 Richest", description=text, colour=0xF1C40F), allowed_mentions=none_mentions)

    @tree.command(name="lfg", description="Look for teammates")
    @app_commands.describe(game="Which game", rank="Your rank or level", players="Team size including you",
                           note="Optional note")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 60.0, key=lambda i: i.user.id)
    async def lfg(interaction: discord.Interaction, game: str, rank: str = "any",
                  players: app_commands.Range[int, 0, 10] = 0, note: str = ""):
        cfg = conf(bot, interaction.guild, "lfg")
        if not cfg:
            await interaction.response.send_message("LFG is not set up on this server.", ephemeral=True)
            return
        if cfg.get("channel_id") and interaction.channel_id != cfg["channel_id"]:
            await interaction.response.send_message(
                f"Use this command in <#{cfg['channel_id']}>.", ephemeral=True)
            return
        size = max(2, players or int(cfg.get("max_players", 5)))
        view = LfgView(bot, interaction.user, game[:40], rank[:30], size, note[:200], cfg)
        await interaction.response.send_message(embed=view.embed(), view=view)
        view.message = await interaction.original_response()

    @tree.command(name="lockdown", description="Lock every public text channel (admins only)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def lockdown(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        n = await do_lockdown(bot, interaction.guild, True)
        await alert_staff(bot, interaction.guild, f"🔒 Lockdown enabled by {interaction.user.mention} ({n} channels).")
        await interaction.followup.send(f"Locked {n} channels. Use `/unlock` to restore them.", ephemeral=True)

    @tree.command(name="unlock", description="Undo the lockdown (admins only)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def unlock(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        n = await do_lockdown(bot, interaction.guild, False)
        await alert_staff(bot, interaction.guild, f"🔓 Lockdown lifted by {interaction.user.mention} ({n} channels).")
        await interaction.followup.send(f"Restored {n} channels.", ephemeral=True)

    @tree.error
    async def on_tree_error(interaction: discord.Interaction, error):
        msg = "Something went wrong."
        if isinstance(error, app_commands.CommandOnCooldown):
            msg = f"Slow down: try again in {error.retry_after:.0f}s."
        elif isinstance(error, (app_commands.MissingPermissions, app_commands.CheckFailure)):
            msg = "You do not have permission."
        else:
            bot.log(f"Command error: {error}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass
