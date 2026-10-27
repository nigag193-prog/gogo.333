import asyncio
import datetime
import json
import os
import random
import re
import threading
import time
from collections import defaultdict, deque

import discord
from discord import app_commands

import assistant
import antispam_guard
import extras
import rolesys
import rolecard, welcomecard

from modules import (APP_DIR, DB, RolePanel, TicketClose, TicketPanel, VerifyPanel,
                     handle_command, level_for)
from visuals import make_banner, make_emoji, make_icon

CONFIG_FILE = os.path.join(APP_DIR, "guild_config.json")
BACKUP_DIR = os.path.join(APP_DIR, "backups")
ASSETS_DIR = os.path.join(APP_DIR, "assets")
for d in (BACKUP_DIR, ASSETS_DIR):
    os.makedirs(d, exist_ok=True)

def _welcome_banner():
    """welcome_banner.gif / .png / .jpg next to bot.py (or in the assets folder) -> a fresh discord.File,
    else None. A GIF is tried first, so an animated banner wins over a still one."""
    for name in ("welcome_banner.gif", "welcome_banner.png", "welcome_banner.jpg"):
        for folder in (os.path.dirname(os.path.abspath(__file__)), ASSETS_DIR):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return discord.File(path, filename=name)
    return None


INVITE_RE = re.compile(r"(discord\.gg|discord(app)?\.com/invite)/\w+", re.I)
LINK_RE = re.compile(r"https?://\S+", re.I)

PERMS = {
    # 👑 Owner: full administrator
    "admin": discord.Permissions(administrator=True),
    # 💫 Founder: EVERYTHING except the administrator flag
    #    (manage roles/channels/guild, kick, ban, move members, webhooks, emojis...)
    "senior": discord.Permissions(
        manage_guild=True, manage_channels=True, manage_roles=True, manage_messages=True,
        kick_members=True, ban_members=True, moderate_members=True, manage_nicknames=True,
        manage_emojis_and_stickers=True, manage_webhooks=True, move_members=True,
        view_audit_log=True, mention_everyone=True),
    # ⚡ Admin: moderation only - kick, ban, move members, timeout, manage messages
    #    (NO access to roles / channels / guild settings)
    "mod": discord.Permissions(
        kick_members=True, ban_members=True, moderate_members=True, manage_messages=True,
        manage_nicknames=True, move_members=True, view_audit_log=True),
    # 🔰 Mod / 🤝 Helper: light moderation - timeout + delete messages + read audit log
    "helper": discord.Permissions(
        moderate_members=True, manage_messages=True, view_audit_log=True),
    "member": discord.Permissions(
        view_channel=True, send_messages=True, read_message_history=True,
        add_reactions=True, attach_files=True, embed_links=True,
        connect=True, speak=True),
    "none": discord.Permissions.none(),
}

# permission values that count as "staff" (access staff channels, logs, ticket duty)
STAFF_LEVELS = ("admin", "senior", "mod", "helper")

ANTISPAM_DEFAULTS = {
    "enabled": True, "max_messages": 5, "seconds": 6,
    "block_invites": True, "block_links": False,
    "action": "timeout", "timeout_minutes": 10,
}


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def parse_color(value):
    try:
        return discord.Colour(int(str(value).lstrip("#"), 16))
    except (ValueError, TypeError):
        return discord.Colour.default()


def norm_name(name, voice=False):
    return name if voice else name.lower().replace(" ", "-")


def snapshot(guild):
    """A record of the current structure (not a full restore)."""
    return {
        "server": guild.name,
        "time": datetime.datetime.now().isoformat(timespec="seconds"),
        "roles": [{"name": r.name, "color": f"#{r.colour.value:06x}", "permissions": r.permissions.value}
                  for r in guild.roles if not r.is_default()],
        "channels": [{"name": c.name, "type": str(c.type),
                      "category": c.category.name if c.category else None}
                     for c in guild.channels],
    }


# Bots that are commonly added next to this one. IDs are the official bot user IDs
# (as far as I know); detection also falls back to the bot's name.
KNOWN_BOTS = {
    155149108183695360: {"name": "Dyno", "key": "dyno",
                         "covers": {"logs", "antispam", "welcome"}},
    282859044593598464: {"name": "ProBot", "key": "probot",
                         "covers": {"logs", "antispam", "welcome", "levels"}},
    159985870458322944: {"name": "MEE6", "key": "mee6",
                         "covers": {"logs", "antispam", "welcome", "levels"}},
    235148962103951360: {"name": "Carl-bot", "key": "carl",
                         "covers": {"logs", "antispam", "welcome"}},
}

DASH = {"Dyno": "dyno.gg", "ProBot": "probot.io", "MEE6": "mee6.xyz", "Carl-bot": "carl.gg"}

# modules that can be left to another bot (used by the GUI too)
MODULES = {
    "logs": "Logs (joins, leaves, kicks, bans, deletes, edits, voice, roles)",
    "antispam": "Anti-spam / invite blocking",
    "levels": "Levels / XP",
    "welcome": "Welcome message",
}


class ServerBot(discord.Client):
    def __init__(self, log, ai_key=None):
        intents = discord.Intents.default()
        intents.members = True          # enable "Server Members Intent" in the portal
        intents.message_content = True  # enable "Message Content Intent" in the portal
        super().__init__(intents=intents)
        self.log = log
        self.ai_key = ai_key      # Gemini key for the AI assistant
        self.config = load_config()
        self.db = DB(os.path.join(APP_DIR, "data.db"))
        self.history = defaultdict(deque)
        self.mass = defaultdict(deque)   # (guild, executor) -> timestamps of kicks/bans
        self.building = set()            # guilds being built: no event logs meanwhile
        self.tree = app_commands.CommandTree(self)
        self._synced = False
        extras.register(self)
        assistant.register(self)
        rolesys.register(self)
        antispam_guard.register(self)
        welcomecard.register(self)
        rolecard.register(self)

    async def setup_hook(self):
        self.add_view(TicketPanel())
        self.add_view(TicketClose())
        self.add_view(VerifyPanel())
        self.add_view(assistant.GogoPanel())
        extras.init_tables(self.db.conn)
        assistant.init_tables(self.db.conn)
        rolesys.init_tables(self.db.conn)
        asyncio.create_task(assistant.cleanup_loop(self))
        self.bg_task = asyncio.create_task(extras.background(self))
        for cfg in self.config.values():
            rp = cfg.get("role_panel")
            if rp and rp.get("roles"):
                self.add_view(RolePanel([(r["role_id"], r["label"]) for r in rp["roles"]]))

    async def on_ready(self):
        self.log(f"Bot connected as {self.user}")
        if not self._synced:
            self._synced = True
            for g in self.guilds:
                try:
                    self.tree.copy_global_to(guild=g)
                    await self.tree.sync(guild=g)
                except discord.HTTPException as e:
                    self.log(f"Could not register slash commands in {g.name}: {e}")
            self.log("Slash commands registered: /lfg /balance /daily /shop /buy /richest /lockdown /unlock")

    def persist(self):
        save_config(self.config)

    async def on_raw_reaction_add(self, payload):
        await extras.on_reaction(self, payload)

    def gcfg(self, guild):
        return self.config.get(str(guild.id), {})

    async def guild_log(self, guild, text):
        ch = guild.get_channel((self.gcfg(guild).get("logs") or {}).get("channel_id") or 0)
        if ch:
            try:
                await ch.send(text[:1900])
            except discord.HTTPException:
                pass

    # ---------- logging helpers ----------
    def external(self, guild, module):
        return (self.gcfg(guild).get("external") or {}).get(module)

    def log_channel(self, guild, kind):
        lg = self.gcfg(guild).get("logs") or {}
        cid = (lg.get("channels") or {}).get(kind) or lg.get("channel_id")
        return guild.get_channel(cid or 0)

    async def event_log(self, guild, kind, title, desc="", color=0x7C3AED,
                        fields=None, ping_staff=False):
        """Embed log. kind: members | messages | voice | mod | server."""
        if guild.id in self.building or self.external(guild, "logs"):
            return
        ch = self.log_channel(guild, kind)
        if ch is None:
            return
        emb = discord.Embed(title=title[:250], description=desc[:3800], colour=color,
                            timestamp=discord.utils.utcnow())
        for name, value in (fields or []):
            emb.add_field(name=name[:250], value=(str(value) or "-")[:1000], inline=True)
        content = None
        if ping_staff:
            ids = self.gcfg(guild).get("staff_role_ids") or []
            content = " ".join(f"<@&{i}>" for i in ids) or None
        try:
            await ch.send(content=content, embed=emb)
        except discord.HTTPException:
            pass

    async def _audit(self, guild, action, target_id, seconds=20):
        try:
            async for e in guild.audit_logs(limit=6, action=action):
                if (e.target is not None and e.target.id == target_id
                        and (discord.utils.utcnow() - e.created_at).total_seconds() < seconds):
                    return e
        except (discord.Forbidden, discord.HTTPException):
            pass
        return None

    async def track_mod_action(self, guild, executor, action):
        """Alert the staff when one person kicks/bans 3+ people in 60 seconds."""
        if (self.gcfg(guild).get("antinuke") or {}).get("enabled"):
            return  # the anti-nuke module handles it
        if executor is None or (self.user and executor.id == self.user.id):
            return
        q = self.mass[(guild.id, executor.id)]
        now = time.time()
        q.append(now)
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= 3:
            q.clear()
            await self.event_log(
                guild, "mod", "⚠️ Mass moderation alert",
                f"{executor.mention} used {action} several times within 60 seconds.",
                0xE74C3C, ping_staff=True)

    # ---------- events ----------
    async def _apply_show_locked(self, guild, channel_list, category_list, member_role, unverified):
        """"show_locked": members SEE these channels (a padlock shows) but cannot read, write or join.
        Roles that were explicitly allowed to view a channel keep FULL access; people who are not
        verified yet still see nothing."""
        TEXT_LOCK = dict(send_messages=False, add_reactions=False, read_message_history=False,
                         create_public_threads=False, create_private_threads=False,
                         send_messages_in_threads=False, attach_files=False, embed_links=False)
        VOICE_LOCK = dict(connect=False, speak=False, stream=False, use_voice_activation=False,
                          send_messages=False, read_message_history=False,
                          attach_files=False, embed_links=False)
        TEXT_OPEN = dict(view_channel=True, send_messages=True, read_message_history=True,
                         add_reactions=True, attach_files=True, embed_links=True)
        VOICE_OPEN = dict(view_channel=True, connect=True, speak=True, stream=True,
                          use_voice_activation=True, send_messages=True, read_message_history=True)
        skip = {guild.default_role.id, member_role.id}
        if unverified is not None:
            skip.add(unverified.id)
        await asyncio.sleep(1)   # let Discord's permission updates reach the cache
        for category in category_list:
            try:
                ow = category.overwrites_for(member_role)
                ow.update(view_channel=True)
                await category.set_permissions(member_role, overwrite=ow)
            except discord.HTTPException as e:
                self.log(f"Could not show category {category.name}: {e}")
        for ch in channel_list:
            voice = isinstance(ch, discord.VoiceChannel)
            try:
                insiders = [t for t, o in ch.overwrites.items()
                            if isinstance(t, discord.Role) and t.id not in skip and o.view_channel is True]
                ow = ch.overwrites_for(member_role)
                ow.update(view_channel=True, **(VOICE_LOCK if voice else TEXT_LOCK))
                await ch.set_permissions(member_role, overwrite=ow)
                for role in insiders:
                    ow = ch.overwrites_for(role)
                    ow.update(**(VOICE_OPEN if voice else TEXT_OPEN))
                    await ch.set_permissions(role, overwrite=ow)
                if unverified is not None:
                    ow = ch.overwrites_for(unverified)
                    ow.update(view_channel=False)
                    await ch.set_permissions(unverified, overwrite=ow)
                self.log(f"Visible but locked: {ch.name} (full access kept for {len(insiders)} role(s))")
            except discord.HTTPException as e:
                self.log(f"Could not lock {ch.name}: {e}")

    async def on_member_join(self, member):
        guild = member.guild
        guild_cfg = self.gcfg(guild)
        welcome_cfg = guild_cfg.get("welcome") or {}
        verification_cfg = guild_cfg.get("verification") or {}

        # Always assign Unverified first when verification is configured.
        # The old flow relied only on welcome.auto_role_id, which may be empty
        # when the blueprint was read from an existing server.
        unverified_id = verification_cfg.get("unverified_role_id")
        unverified_role = guild.get_role(int(unverified_id or 0)) if unverified_id else None
        if unverified_role is None:
            unverified_role = guild.get_role(int(welcome_cfg.get("auto_role_id") or 0))
        if unverified_role and unverified_role not in member.roles:
            try:
                await member.add_roles(unverified_role, reason="Verification gate: new member")
            except discord.HTTPException as e:
                self.log(f"Could not assign Unverified to {member}: {e}. Check bot role hierarchy and Manage Roles permission.")

        cfg = welcome_cfg
        if cfg:
            channel = guild.get_channel(cfg.get("channel_id") or 0)
            if channel and not self.external(guild, "welcome"):
                text = cfg["message"].replace("{user}", member.mention).replace("{server}", guild.name)
                try:
                    await channel.send(text)
                except discord.HTTPException:
                    pass
                # (static banner removed: welcomecard.py sends the animated
                #  personalized GIF right after this message)
        age = (discord.utils.utcnow() - member.created_at).days
        await self.event_log(
            guild, "members", "📥 Member joined", f"{member.mention} ({member})", 0x2ECC71,
            [("Account age", f"{age} days"), ("ID", member.id),
             ("Members", guild.member_count)])

    async def on_member_remove(self, member):
        guild = member.guild
        if self.external(guild, "logs") or guild.id in self.building:
            return
        await asyncio.sleep(1.5)  # let the audit log catch up
        if await self._audit(guild, discord.AuditLogAction.ban, member.id):
            return  # on_member_ban reports it
        entry = await self._audit(guild, discord.AuditLogAction.kick, member.id)
        if entry:
            by = entry.user.mention if entry.user else "?"
            await self.event_log(
                guild, "mod", "👢 Member kicked", f"{member} ({member.id})", 0xE67E22,
                [("By", by), ("Reason", entry.reason or "no reason")], ping_staff=True)
            await self.track_mod_action(guild, entry.user, "kick")
            await extras.nuke_hit(self, guild, entry.user, "kick")
        else:
            await self.event_log(
                guild, "members", "📤 Member left", f"{member} ({member.id})", 0x95A5A6,
                [("Members", guild.member_count)])

    async def on_member_ban(self, guild, user):
        if self.external(guild, "logs") or guild.id in self.building:
            return
        await asyncio.sleep(1.5)
        entry = await self._audit(guild, discord.AuditLogAction.ban, user.id)
        by = entry.user.mention if entry and entry.user else "?"
        reason = (entry.reason if entry else None) or "no reason"
        await self.event_log(guild, "mod", "🔨 Member banned", f"{user} ({user.id})",
                             0xE74C3C, [("By", by), ("Reason", reason)], ping_staff=True)
        if entry:
            await self.track_mod_action(guild, entry.user, "ban")
            await extras.nuke_hit(self, guild, entry.user, "ban")

    async def on_member_unban(self, guild, user):
        await self.event_log(guild, "mod", "♻️ Member unbanned", f"{user} ({user.id})", 0x3498DB)

    async def on_member_update(self, before, after):
        guild = after.guild
        if guild.id in self.building or self.external(guild, "logs"):
            return
        if before.timed_out_until != after.timed_out_until:
            if after.timed_out_until:
                entry = await self._audit(guild, discord.AuditLogAction.member_update, after.id)
                by = entry.user.mention if entry and entry.user else "?"
                await self.event_log(
                    guild, "mod", "⏳ Member timed out", after.mention, 0xF1C40F,
                    [("Until", discord.utils.format_dt(after.timed_out_until, "R")), ("By", by)])
            else:
                await self.event_log(guild, "mod", "✅ Timeout removed", after.mention, 0x2ECC71)
        added = [r for r in after.roles if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        if added or removed:
            fields = []
            if added:
                fields.append(("Added", ", ".join(r.mention for r in added)))
            if removed:
                fields.append(("Removed", ", ".join(r.mention for r in removed)))
            await self.event_log(guild, "members", "🎭 Roles updated", after.mention,
                                 0x9B59B6, fields)
        if before.nick != after.nick:
            await self.event_log(
                guild, "members", "✏️ Nickname changed", after.mention, 0x5DADE2,
                [("Before", before.nick or "-"), ("After", after.nick or "-")])

    async def on_voice_state_update(self, member, before, after):
        if before.channel != after.channel:
            await assistant.on_voice(self, member, before, after)
        if before.channel == after.channel:
            return
        guild = member.guild
        if before.channel is None:
            await self.event_log(guild, "voice", "🔊 Joined voice",
                                 f"{member.mention} joined {after.channel.mention}", 0x2ECC71)
        elif after.channel is None:
            await self.event_log(guild, "voice", "🔇 Left voice",
                                 f"{member.mention} left {before.channel.mention}", 0xE67E22)
        else:
            await self.event_log(
                guild, "voice", "🔀 Moved voice", member.mention, 0x3498DB,
                [("From", before.channel.mention), ("To", after.channel.mention)])

    def _log_channel_ids(self, guild):
        lg = self.gcfg(guild).get("logs") or {}
        ids = set((lg.get("channels") or {}).values())
        ids.add(lg.get("channel_id"))
        return ids

    async def on_message_delete(self, message):
        if not message.guild or message.author.bot:
            return
        if message.channel.id in self._log_channel_ids(message.guild):
            return
        extra = f"\n📎 {len(message.attachments)} attachment(s)" if message.attachments else ""
        await self.event_log(
            message.guild, "messages", "🗑️ Message deleted",
            (message.content[:1500] or "(no text)") + extra, 0xE74C3C,
            [("Author", f"{message.author.mention}"), ("Channel", message.channel.mention),
             ("ID", message.author.id)])

    async def on_message_edit(self, before, after):
        if not after.guild or after.author.bot or before.content == after.content:
            return
        if after.channel.id in self._log_channel_ids(after.guild):
            return
        await self.event_log(
            after.guild, "messages", "✏️ Message edited", f"[Jump to message]({after.jump_url})",
            0xF1C40F,
            [("Author", after.author.mention), ("Channel", after.channel.mention),
             ("Before", before.content[:900] or "-"), ("After", after.content[:900] or "-")])

    async def on_guild_channel_create(self, channel):
        await self.event_log(channel.guild, "server", "➕ Channel created",
                             f"{channel.mention if hasattr(channel, 'mention') else channel.name}",
                             0x2ECC71)

    async def _nuke_from_audit(self, guild, action, target_id, label):
        if guild.id in self.building:
            return
        await asyncio.sleep(1.5)
        entry = await self._audit(guild, action, target_id)
        if entry and entry.user:
            await extras.nuke_hit(self, guild, entry.user, label)

    async def on_guild_channel_delete(self, channel):
        asyncio.create_task(self._nuke_from_audit(
            channel.guild, discord.AuditLogAction.channel_delete, channel.id, "channel deletion"))
        await self.event_log(channel.guild, "server", "➖ Channel deleted",
                             f"#{channel.name}", 0xE74C3C, ping_staff=True)

    async def on_guild_role_create(self, role):
        await self.event_log(role.guild, "server", "➕ Role created", role.mention, 0x2ECC71)

    async def on_guild_role_delete(self, role):
        asyncio.create_task(self._nuke_from_audit(
            role.guild, discord.AuditLogAction.role_delete, role.id, "role deletion"))
        await self.event_log(role.guild, "server", "➖ Role deleted", role.name, 0xE74C3C,
                             ping_staff=True)

    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        await assistant.on_message(self, message)
        if await rolesys.anti_insult(self, message):
            return
        if await self.check_antispam(message):
            return
        if await antispam_guard.on_message(self, message):
            return
        if message.content.startswith("!") and len(message.content) > 1:
            if await rolesys.guard_command(self, message):
                return
            if await handle_command(self, message):
                return
        await self.give_xp(message)
        await extras.earn_message(self, message)

    async def check_antispam(self, message):
        cfg = self.gcfg(message.guild).get("antispam")
        if not cfg or not cfg.get("enabled") or self.external(message.guild, "antispam"):
            return False
        if message.author.guild_permissions.manage_messages:
            return False  # staff are exempt
        reason = None
        if cfg.get("block_invites") and INVITE_RE.search(message.content):
            reason = "invite"
        elif cfg.get("block_links") and LINK_RE.search(message.content):
            reason = "link"
        else:
            now = time.time()
            q = self.history[(message.guild.id, message.author.id)]
            q.append(now)
            while q and now - q[0] > cfg["seconds"]:
                q.popleft()
            if len(q) > cfg["max_messages"]:
                reason = "spam"
                q.clear()
        if not reason:
            return False
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        try:
            await message.channel.send(
                f"{message.author.mention} message removed ({reason}).", delete_after=5)
            if reason == "spam" and cfg.get("action") == "timeout":
                await message.author.timeout(
                    datetime.timedelta(minutes=cfg.get("timeout_minutes", 10)), reason="anti-spam")
        except discord.HTTPException:
            pass
        await self.guild_log(message.guild, f"Anti-spam ({reason}): {message.author}")
        return True

    async def give_xp(self, message):
        lv = self.gcfg(message.guild).get("levels")
        if not lv or not lv.get("enabled") or self.external(message.guild, "levels"):
            return
        res = self.db.add_xp(message.guild.id, message.author.id, random.randint(15, 25))
        if not res:
            return
        old, new = res
        if level_for(new) <= level_for(old):
            return
        level = level_for(new)
        channel = message.guild.get_channel(lv.get("channel_id") or 0) or message.channel
        try:
            await channel.send(f"{message.author.mention} reached level {level}!")
        except discord.HTTPException:
            pass
        for reward in lv.get("roles", []):
            role = message.guild.get_role(reward["role_id"])
            if role and level >= reward["level"] and role not in message.author.roles:
                try:
                    await message.author.add_roles(role, reason="level reward")
                except discord.HTTPException:
                    pass

    # ---------- helpers for the GUI ----------
    async def _guild(self, guild_id):
        for _ in range(80):  # wait up to ~40s for the bot to connect
            if self.is_ready():
                break
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("Bot did not connect. Check the token and intents.")
        guild = self.get_guild(int(guild_id))
        if guild is None:
            raise RuntimeError("The bot is not in this server. Invite it first.")
        return guild

    def _write_checklist(self, bp, assigned, channels):
        """Other bots cannot be configured from here: write the exact steps for their dashboards."""
        lines = ["SETUP CHECKLIST: other bots",
                 "These bots can only be configured in their own web dashboards (nothing can do it for you).",
                 "Names of options may differ slightly in each dashboard.", ""]
        for mod, info in assigned.items():
            lines.append(f"== {mod.upper()} -> {info['name']}  ({DASH.get(info['name'], 'its dashboard')}) ==")
            if mod == "welcome":
                w = bp.get("welcome") or {}
                ch = channels.get(w.get("channel"))
                lines.append(f"1. Enable its Welcome message and choose the channel: #{ch.name if ch else '?'}")
                lines.append("2. Message text (adapt the variables to that bot's own syntax):")
                lines.append(f"   {w.get('message', '')}")
                lines.append("   My bot still gives the auto role; it only skips the message.")
            elif mod == "logs":
                lg = bp.get("logs") or {}
                lines.append("1. Enable its Logging / Logs module.")
                labels = {"channel": "general", "members": "joins, leaves, roles, nicknames",
                          "messages": "deleted / edited messages", "voice": "voice join / leave / move",
                          "mod": "kicks, bans, timeouts", "server": "channels and roles created / deleted"}
                for k, desc in labels.items():
                    c = channels.get(lg.get(k))
                    if c:
                        lines.append(f"2. {desc}: #{c.name}")
                lines.append("   If it only allows one log channel, use the general one.")
            elif mod == "levels":
                lines.append("1. Enable its Levels / XP module and set the level-up channel.")
            elif mod == "antispam":
                lines.append("1. Enable its anti-spam / auto-moderation module.")
            lines.append("")
        path = os.path.join(APP_DIR, "setup_checklist.txt")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except OSError:
            path = None
        self.log("---- Do this in the other bots' dashboards ----")
        for line in lines[3:]:
            if line:
                self.log(line)
        if path:
            self.log(f"Checklist saved to {path}")

    async def wait_ready(self):
        for _ in range(80):  # ~40 s
            if self.is_ready():
                return
            await asyncio.sleep(0.5)
        raise RuntimeError("Bot did not connect. Check the token and intents.")

    async def connect_info(self, guild_id=None):
        """Wait for the bot, list its servers, and scan other bots if a server is chosen."""
        await self.wait_ready()
        guilds = [{"id": g.id, "name": g.name, "members": g.member_count}
                  for g in sorted(self.guilds, key=lambda x: x.name.lower())]
        self.log(f"The bot is in {len(guilds)} server(s).")
        found = None
        if guild_id and str(guild_id).isdigit() and self.get_guild(int(guild_id)):
            found = await self.scan_bots(guild_id)
        return {"guilds": guilds, "bots": found}

    def _backup(self, guild):
        path = os.path.join(BACKUP_DIR, f"{guild.id}_{time.strftime('%Y%m%d_%H%M%S')}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot(guild), f, indent=2, ensure_ascii=False)
        self.log(f"Backup saved: {path}")

    async def backup_only(self, guild_id):
        self._backup(await self._guild(guild_id))

    # ---------- build / edit the server ----------
    async def detect_bots(self, guild):
        out = []
        try:
            members = [m async for m in guild.fetch_members(limit=None)]
        except discord.HTTPException:
            members = list(guild.members)
        for m in members:
            if not m.bot or (self.user and m.id == self.user.id):
                continue
            info = KNOWN_BOTS.get(m.id)
            if info is None:
                low = m.name.lower()
                info = next((v for v in KNOWN_BOTS.values() if v["key"] in low), None)
            out.append({"id": m.id, "name": info["name"] if info else m.name,
                        "covers": sorted(info["covers"]) if info else [],
                        "known": info is not None})
        return out

    async def scan_bots(self, guild_id):
        guild = await self._guild(guild_id)
        found = await self.detect_bots(guild)
        if found:
            self.log("Bots in the server: " + ", ".join(
                f"{f['name']}{'' if f['known'] else ' (unknown)'}" for f in found))
        else:
            self.log("No other bots found in the server.")
        return found

    async def export_server(self, guild_id):
        """Export a detailed, restorable snapshot of roles, categories, channels and overwrites."""
        guild = await self._guild(guild_id)

        def serialize_overwrites(overwrites):
            out = []
            for target, overwrite in overwrites.items():
                allow, deny = overwrite.pair()
                if isinstance(target, discord.Role):
                    target_type = "role"
                elif isinstance(target, discord.Member):
                    target_type = "member"
                else:
                    continue
                out.append({
                    "target_type": target_type,
                    "target": target.name,
                    "target_id": str(target.id),
                    "allow": allow.value,
                    "deny": deny.value,
                })
            return out

        roles = []
        for r in sorted(guild.roles, key=lambda x: x.position, reverse=True):
            # Managed/integration roles cannot be recreated by the bot. Record their
            # names in metadata, but do not try to create or edit them on import.
            if r.is_default() or r.managed:
                continue
            roles.append({
                "name": r.name,
                "color": "#%06x" % (r.colour.value or 0x99AAB5),
                "permissions": "none",
                "permissions_value": r.permissions.value,
                "hoist": r.hoist,
                "mentionable": r.mentionable,
                "position": r.position,
            })

        # Verification needs an Unverified role even on older servers where it was never created.
        # Include it as a blueprint-only role so applying the snapshot can create it when
        # the user enables "Add missing roles from JSON".
        if not any(item.get("name") == "🔒 Unverified" for item in roles):
            roles.append({
                "name": "🔒 Unverified", "color": "#95a5a6", "permissions": "none",
                "permissions_value": 0, "hoist": False, "mentionable": False, "position": 0,
            })

        categories = []
        for cat, chans in guild.by_category():
            items = []
            for ch in chans:
                if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
                    kind = "voice"
                elif isinstance(ch, (discord.TextChannel, discord.ForumChannel)):
                    kind = "text"
                else:
                    continue
                entry = {
                    "name": ch.name,
                    "type": kind,
                    "permission_overwrites": serialize_overwrites(ch.overwrites),
                    "position": ch.position,
                    "nsfw": bool(getattr(ch, "nsfw", False)),
                    "slowmode_delay": int(getattr(ch, "slowmode_delay", 0) or 0),
                }
                topic = getattr(ch, "topic", None)
                if topic:
                    entry["topic"] = topic[:1000]
                if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
                    entry["bitrate"] = ch.bitrate
                    entry["user_limit"] = ch.user_limit
                items.append(entry)
            if items:
                categories.append({
                    "name": cat.name if cat else "UNCATEGORIZED",
                    "permission_overwrites": serialize_overwrites(cat.overwrites) if cat else [],
                    "position": cat.position if cat else -1,
                    "channels": items,
                })
        # Include runtime module settings too, not only the visual server structure.
        # Otherwise a copied snapshot recreates names/overwrites but Ask Gogo, Verify,
        # and Logs do not know which channels/roles they should use.
        live_cfg = self.gcfg(guild)
        all_channels = {str(ch.id): ch.name for ch in guild.channels}
        all_roles = {str(role.id): role.name for role in guild.roles}
        text_channels = [ch for ch in guild.text_channels]

        def find_channel(*needles):
            needles = [n.casefold().replace("_", "-") for n in needles if n]
            for ch in text_channels:
                low = ch.name.casefold().replace("_", "-")
                if any(n in low for n in needles):
                    return ch.name
            return None

        module_blocks = {}
        # Prefer saved IDs from the running bot config; infer by name as a fallback
        # for older configs that predate module IDs being saved.
        wc = live_cfg.get("welcome") or {}
        welcome_channel = all_channels.get(str(wc.get("channel_id"))) or find_channel("welcome", "bienvenue")
        if welcome_channel:
            block = {"channel": welcome_channel,
                     "message": wc.get("message") or "Welcome {user} to {server}!"}
            role_name = all_roles.get(str(wc.get("auto_role_id")))
            if role_name:
                block["auto_role"] = role_name
            module_blocks["welcome"] = block

        vc = live_cfg.get("verification") or {}
        verify_channel = all_channels.get(str(vc.get("channel_id"))) or find_channel("verify", "verification", "verifie")
        unverified_name = all_roles.get(str(vc.get("unverified_role_id"))) or "🔒 Unverified"
        verified_name = all_roles.get(str(vc.get("verified_role_id"))) or ("✅ Member" if any(r["name"] == "✅ Member" for r in roles) else None)
        if verify_channel and verified_name:
            module_blocks["verification"] = {
                "channel": verify_channel,
                "unverified_role": unverified_name,
                "verified_role": verified_name,
                "text": vc.get("text") or "Press the button below to verify yourself and unlock the server.",
                "button_label": vc.get("button_label") or "Verify me",
            }
            # Guarantee that a newly created Unverified role can see the verification channel,
            # while remaining unable to send messages there.
            for category in categories:
                for channel in category.get("channels", []):
                    if channel.get("name") == verify_channel:
                        overwrites = channel.setdefault("permission_overwrites", [])
                        found = next((o for o in overwrites if o.get("target_type") == "role" and o.get("target") == unverified_name), None)
                        if found is None:
                            overwrites.append({"target_type": "role", "target": unverified_name,
                                               "target_id": str(vc.get("unverified_role_id") or 0),
                                               "allow": 66560, "deny": 2048})
                        break

        # Custom voice-room trigger: export its settings as part of the blueprint.
        # The trigger is a VOICE channel, so do not search only text_channels here.
        cvcfg = live_cfg.get("custom_vc") or {}
        trigger_id = str(cvcfg.get("trigger_id") or "")
        trigger_channel = next((ch for ch in guild.voice_channels if str(ch.id) == trigger_id), None)
        if trigger_channel is None:
            trigger_channel = next((ch for ch in guild.voice_channels
                                    if "create room" in ch.name.casefold()
                                    or "create-your-room" in ch.name.casefold()), None)
        if trigger_channel is not None:
            module_blocks["custom_vc"] = {
                "trigger": trigger_channel.name,
                "category": trigger_channel.category.name if trigger_channel.category else "VOICE",
                "name_format": cvcfg.get("name_format") or "🔒 Private ┃ {name}",
            }

        ac = live_cfg.get("assistant") or {}
        ask_channel = all_channels.get(str(ac.get("channel_id"))) or find_channel("ask-gogo", "ask gogo", "gogo", "ask-ai")
        member_role_name = all_roles.get(str(ac.get("member_role_id"))) or ("✅ Member" if any(r["name"] == "✅ Member" for r in roles) else None)
        max_role_name = all_roles.get(str(ac.get("max_role_id"))) or ("🌟 [HR] Legend" if any(r["name"] == "🌟 [HR] Legend" for r in roles) else None)
        excluded_names = [all_roles[str(rid)] for rid in (ac.get("exclude_ids") or []) if str(rid) in all_roles]
        if ask_channel and member_role_name:
            module_blocks["assistant"] = {"channel": ask_channel, "member_role": member_role_name,
                                          "max_role": max_role_name or "🌟 [HR] Legend",
                                          "exclude_roles": excluded_names or [n for n in ("⚠️ Warn 1", "⚠️ Warn 2", "⚠️ Warn 3") if any(r["name"] == n for r in roles)]}

        lc = live_cfg.get("logs") or {}
        logs_channels = lc.get("channels") or {}
        logs_main = all_channels.get(str(lc.get("channel_id"))) or find_channel("logs", "log")
        log_block = {}
        if logs_main:
            log_block["channel"] = logs_main
        for key, candidates in {
            "members": ("member-logs", "members-log", "member-log", "joins", "leaves"),
            "messages": ("message-logs", "messages-log", "message-log"),
            "voice": ("voice-logs", "voice-log"),
            "mod": ("mod-logs", "moderation-logs", "mod-log"),
            "server": ("server-logs", "server-log", "audit-logs"),
        }.items():
            actual = all_channels.get(str(logs_channels.get(key))) if logs_channels.get(key) else None
            if not actual:
                actual = find_channel(*candidates)
            if actual:
                log_block[key] = actual
        if log_block:
            module_blocks["logs"] = log_block

        self.log(f"Read {len(roles)} roles and {sum(len(c['channels']) for c in categories)} "
                 f"channels in {len(categories)} categories, including exact permission overwrites and module settings.")
        result = {
            "server_snapshot_version": 3,
            "server_name": guild.name,
            "server_id": str(guild.id),
            "roles": roles,
            "categories": categories,
        }
        result.update(module_blocks)
        return result

    def _blueprint_overwrites(self, guild, roles, entries):
        """Convert exact exported overwrite records back into discord.py overwrites."""
        result = {}
        for item in entries or []:
            target = None
            if item.get("target_type") == "role":
                target = roles.get(item.get("target"))
                if target is None and str(item.get("target_id", "")) == str(guild.default_role.id):
                    target = guild.default_role
                if target is None:
                    target = discord.utils.get(guild.roles, name=item.get("target"))
            elif item.get("target_type") == "member":
                try:
                    target = guild.get_member(int(item.get("target_id", 0)))
                except (TypeError, ValueError):
                    target = None
            if target is None:
                continue
            try:
                allow = discord.Permissions(int(item.get("allow", 0)))
                deny = discord.Permissions(int(item.get("deny", 0)))
                result[target] = discord.PermissionOverwrite.from_pair(allow, deny)
            except (TypeError, ValueError):
                continue
        return result

    async def _apply_exact_overwrites(self, channel, guild, roles, entries):
        """Replace all stored overwrites with the exact snapshot values."""
        desired = self._blueprint_overwrites(guild, roles, entries)
        for target in list(channel.overwrites):
            if target not in desired:
                try:
                    await channel.set_permissions(target, overwrite=None)
                except discord.HTTPException as e:
                    self.log(f"Could not clear old permissions for {target}: {e}")
        for target, overwrite in desired.items():
            try:
                await channel.set_permissions(target, overwrite=overwrite)
            except discord.HTTPException as e:
                self.log(f"Could not restore permissions for {target}: {e}")

    async def apply_blueprint(self, guild_id, bp, wipe=False, make_visuals=False,
                              openai_key=None, own_modules=None, build=True,
                              keep_roles=False, keep_channels=False, keep_permissions=True,
                              add_missing_roles=False, add_missing_channels=False):
        guild = await self._guild(guild_id)
        self.building.add(guild.id)
        try:
            await self._apply_blueprint(guild, bp, wipe, make_visuals, openai_key,
                                        set(own_modules or []), build, keep_roles, keep_channels,
                                        keep_permissions, add_missing_roles, add_missing_channels)
        finally:
            await asyncio.sleep(3)  # let the creation events pass before logging again
            self.building.discard(guild.id)

    async def _apply_blueprint(self, guild, bp, wipe, make_visuals, openai_key, own_modules,
                               build=True, keep_roles=False, keep_channels=False, keep_permissions=True,
                               add_missing_roles=False, add_missing_channels=False):
        self._backup(guild)

        roles_rebuilt = False
        if wipe:
            # 1) delete every channel and category (unless kept)
            if keep_channels:
                self.log("Keeping existing channels and categories (Keep categories is on).")
            else:
                for ch in list(guild.channels):
                    try:
                        await ch.delete(reason="AI server builder: replace")
                    except discord.HTTPException as e:
                        self.log(f"Could not delete channel {ch.name}: {e}")
                self.log("Existing channels removed.")
            if keep_roles:
                self.log("Keeping existing roles: they are reused by name (nothing deleted).")
            else:
                # 2) delete every role the bot is allowed to delete
                #    (skips @everyone, bot/integration/booster roles and roles above the bot's own role)
                top = guild.me.top_role
                for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
                    if role.is_default() or role.managed or role >= top:
                        continue
                    try:
                        await role.delete(reason="AI server builder: replace")
                    except discord.HTTPException as e:
                        self.log(f"Could not delete role {role.name}: {e}")
                self.log("Existing roles removed.")
            roles_rebuilt = not keep_roles
            # forget the old saved settings of this server, they point to deleted objects
            self.config.pop(str(guild.id), None)
            save_config(self.config)
            await asyncio.sleep(2)  # let Discord's cache catch up
            if not build:
                self.log("Everything was deleted. Nothing was built (Replace is off).")
                return

        roles, role_perm = {}, {}
        for r in bp.get("roles", []):
            name = r.get("name")
            if not name:
                continue
            role_perm[name] = r.get("permissions", "none")
            existing = discord.utils.get(guild.roles, name=name)
            if existing:
                roles[name] = existing
                if keep_roles:
                    # "Keep roles" = completely untouched: name, colour, permissions, hoist
                    self.log(f"Role exists, kept untouched (Keep roles is on): {name}")
                else:
                    try:
                        await existing.edit(
                            colour=parse_color(r.get("color")),
                            permissions=(discord.Permissions(int(r["permissions_value"])) if r.get("permissions_value") is not None
                                          else PERMS.get(r.get("permissions"), PERMS["none"])),
                            hoist=bool(r.get("hoist")), mentionable=bool(r.get("mentionable", existing.mentionable)),
                            reason="AI server builder: sync with blueprint")
                        self.log(f"Role exists, synced with the JSON: {name}")
                    except discord.HTTPException:
                        self.log(f"Role exists, kept as is (above my role?): {name}")
                continue
            if not add_missing_roles:
                self.log(f"Role missing, not created (Add missing roles is off): {name}")
                continue
            roles[name] = await guild.create_role(
                name=name[:100], colour=parse_color(r.get("color")),
                permissions=(discord.Permissions(int(r["permissions_value"])) if r.get("permissions_value") is not None
                              else PERMS.get(r.get("permissions"), PERMS["none"])),
                hoist=bool(r.get("hoist")), mentionable=bool(r.get("mentionable", False)),
                reason="AI server builder")
            self.log(f"Role created: {name}")

        # Role hierarchy must follow the JSON order (first role = highest).
        # New roles are created at the bottom of the list, so after a full wipe
        # the order is already correct; when REUSING existing roles we fix it here.
        # Positions are counted DOWN from the bot's own top role: Discord answers
        # with 50013 Missing Permissions for any role placed above it.
        names_in_order = [r.get("name") for r in bp.get("roles", []) if r.get("name") in roles]
        top_pos = guild.me.top_role.position
        if names_in_order and top_pos >= 2 and not keep_roles:
            positions = {roles[n]: top_pos - 1 - i for i, n in enumerate(names_in_order)}
            positions = {r: p for r, p in positions.items() if r.position != p}
            if positions:
                try:
                    await guild.edit_role_positions(positions)
                    self.log("Role hierarchy ordered (first role in the JSON = top).")
                except discord.HTTPException as e:
                    self.log(f"Could not order the roles: {e}")
        elif names_in_order and not keep_roles:
            self.log("The bot has no role of its own: drag its role above the staff "
                     "roles in Server Settings, or the hierarchy cannot be enforced.")

        everyone = guild.default_role
        ver = bp.get("verification") or {}
        unverified = roles.get(ver.get("unverified_role")) if ver else None
        verified_role = roles.get(ver.get("verified_role")) if ver else None
        staff_names = [n for n, p in role_perm.items() if p in STAFF_LEVELS and n in roles]
        channels = {}

        def _need_perms(name):
            # If requested, never change overwrites on existing channels/categories.
            if keep_permissions and name not in created_channels:
                return False
            return (name in created_channels) or roles_rebuilt or (not keep_channels)
        created_channels = set()
        ordered_categories = []
        for cat in bp.get("categories", []):
            if not cat.get("name"):
                continue
            gate = bool(cat.get("unverified_only")) and unverified is not None
            category = discord.utils.get(guild.categories, name=cat["name"])
            if category is None and not add_missing_channels:
                self.log(f"Category missing, not created (Add missing categories/channels is off): {cat['name']}")
                continue
            if category is None:
                cat_ow = {}
                if "permission_overwrites" in cat:
                    cat_ow = self._blueprint_overwrites(guild, roles, cat.get("permission_overwrites"))
                # New members are completely locked until verification. Only the
                # verification category is visible to @everyone/unverified.
                if unverified is not None and "permission_overwrites" not in cat:
                    cat_ow[everyone] = discord.PermissionOverwrite(view_channel=False)
                    cat_ow[unverified] = discord.PermissionOverwrite(view_channel=(gate), send_messages=False, read_message_history=True)
                    if verified_role is not None and not gate:
                        cat_ow[verified_role] = discord.PermissionOverwrite(view_channel=True)
                    for rn in ("👑 Owner", "💫 Co-Owner", "⚡ Admin", "🔰 Moderator", "🤝 Helper"):
                        rr = roles.get(rn)
                        if rr:
                            cat_ow[rr] = discord.PermissionOverwrite(view_channel=True)
                category = await guild.create_category(cat["name"][:100], overwrites=cat_ow)
            ordered_categories.append(category)
            # Apply the same onboarding gate to existing categories when replacing/rebuilding.
            if _need_perms(cat["name"]):
                try:
                    if "permission_overwrites" in cat:
                        await self._apply_exact_overwrites(category, guild, roles, cat.get("permission_overwrites"))
                    elif unverified is not None:
                        # Before verification, everyone is locked out except the
                        # verification category. After verification, normal members
                        # get public categories and staff get their normal access.
                        await category.set_permissions(everyone, view_channel=False)
                        await category.set_permissions(
                            unverified,
                            view_channel=gate,
                            send_messages=False,
                            read_message_history=True,
                        )
                        if verified_role is not None and not gate:
                            await category.set_permissions(verified_role, view_channel=True)
                        for rn in ("👑 Owner", "💫 Co-Owner", "⚡ Admin", "🔰 Moderator", "🤝 Helper"):
                            rr = roles.get(rn)
                            if rr:
                                await category.set_permissions(rr, view_channel=True)
                except discord.HTTPException as e:
                    self.log(f"Could not set category permissions on {category.name}: {e}")
            for ch in cat.get("channels", []):
                if not ch.get("name"):
                    continue
                voice = ch.get("type") == "voice"
                existing = discord.utils.get(
                    category.voice_channels if voice else category.text_channels,
                    name=norm_name(ch["name"], voice))
                allowed = [roles[n] for n in ch.get("private_roles", []) if n in roles]

                # A channel with private_roles is PRIVATE. This must win over the
                # general "verified members can see public channels" rule, otherwise
                # a normal Member could see Owner/Staff channels and voices.
                if unverified is not None:
                    # New members may see only the verification channel and the
                    # dedicated welcome channel. Everything else stays hidden until
                    # the Member role is granted.
                    welcome_cfg = bp.get("welcome") or {}
                    is_welcome_channel = ch.get("name") == welcome_cfg.get("channel")
                    overwrites = {everyone: discord.PermissionOverwrite(view_channel=False)}
                    overwrites[unverified] = discord.PermissionOverwrite(
                        view_channel=(gate or is_welcome_channel) if not allowed else False,
                        send_messages=False,
                        read_message_history=True,
                    )
                    if allowed:
                        if verified_role is not None:
                            overwrites[verified_role] = discord.PermissionOverwrite(view_channel=False)
                        for rn in ("👑 Owner", "💫 Co-Owner", "⚡ Admin", "🔰 Moderator", "🤝 Helper"):
                            rr = roles.get(rn)
                            if rr:
                                overwrites[rr] = discord.PermissionOverwrite(view_channel=False)
                        for role in allowed:
                            overwrites[role] = discord.PermissionOverwrite(view_channel=True)
                    else:
                        if verified_role is not None and not gate:
                            overwrites[verified_role] = discord.PermissionOverwrite(view_channel=True)
                        for rn in ("👑 Owner", "💫 Co-Owner", "⚡ Admin", "🔰 Moderator", "🤝 Helper"):
                            rr = roles.get(rn)
                            if rr:
                                overwrites[rr] = discord.PermissionOverwrite(view_channel=True)
                else:
                    if allowed:
                        overwrites = {everyone: discord.PermissionOverwrite(view_channel=False)}
                        if verified_role is not None:
                            overwrites[verified_role] = discord.PermissionOverwrite(view_channel=False)
                        for rn in ("👑 Owner", "💫 Co-Owner", "⚡ Admin", "🔰 Moderator", "🤝 Helper"):
                            rr = roles.get(rn)
                            if rr:
                                overwrites[rr] = discord.PermissionOverwrite(view_channel=False)
                        for role in allowed:
                            overwrites[role] = discord.PermissionOverwrite(view_channel=True)
                    elif ch.get("readonly") and not voice:
                        overwrites = {everyone: discord.PermissionOverwrite(send_messages=False)}
                    else:
                        overwrites = {}

                if "permission_overwrites" in ch:
                    overwrites = self._blueprint_overwrites(guild, roles, ch.get("permission_overwrites"))

                if existing:
                    channels[ch["name"]] = existing
                    # Always re-apply the blueprint overwrites when we are rebuilding,
                    # replacing roles, or creating/replacing the channel. This also
                    # repairs old channels that accidentally exposed private areas.
                    if _need_perms(ch["name"]) or unverified is not None:
                        try:
                            if "permission_overwrites" in ch and _need_perms(ch["name"]):
                                await self._apply_exact_overwrites(existing, guild, roles, ch.get("permission_overwrites"))
                            else:
                                for target in [everyone, unverified, verified_role, *[roles.get(rn) for rn in ("👑 Owner", "💫 Co-Owner", "⚡ Admin", "🔰 Moderator", "🤝 Helper")], *allowed]:
                                    if target is not None:
                                        await existing.set_permissions(target, overwrite=overwrites.get(target, discord.PermissionOverwrite(view_channel=None, send_messages=None, read_message_history=None)))
                            # Enforce onboarding visibility even when the JSON contains
                            # explicit overwrites: @everyone is hidden until verification.
                            if unverified is not None:
                                welcome_cfg = bp.get("welcome") or {}
                                verify_cfg = bp.get("verification") or {}
                                is_welcome = ch.get("name") == welcome_cfg.get("channel")
                                is_verify = ch.get("name") == verify_cfg.get("channel")
                                is_private = bool(allowed)
                                await existing.set_permissions(everyone, view_channel=False)
                                await existing.set_permissions(
                                    unverified,
                                    view_channel=(is_welcome or is_verify) and not is_private,
                                    send_messages=False,
                                    read_message_history=True,
                                )
                                if verified_role is not None and not is_private:
                                    await existing.set_permissions(verified_role, view_channel=True)
                        except discord.HTTPException as e:
                            self.log(f"Could not fix permissions on {existing.name}: {e}")
                    continue
                if not add_missing_channels:
                    self.log(f"Channel missing, not created (Add missing categories/channels is off): {ch['name']}")
                    continue
                if voice:
                    created = await guild.create_voice_channel(
                        ch["name"][:100], category=category, overwrites=overwrites,
                        bitrate=min(int(ch.get("bitrate", 64000)), guild.bitrate_limit),
                        user_limit=int(ch.get("user_limit", 0)))
                else:
                    created = await guild.create_text_channel(
                        ch["name"][:100], category=category,
                        topic=(ch.get("topic") or None), overwrites=overwrites,
                        nsfw=bool(ch.get("nsfw", False)),
                        slowmode_delay=int(ch.get("slowmode_delay", 0) or 0))
                channels[ch["name"]] = created
                created_channels.add(ch["name"])
                self.log(f"Channel created: {ch['name']}")

        # Enforce the verification gate across every server channel, including channels
        # omitted from the blueprint. Do not grant Member access to channels explicitly
        # marked private in the blueprint (Owner/Staff/Logs, etc.).
        if unverified is not None:
            welcome_cfg_gate = bp.get("welcome") or {}
            verify_cfg_gate = bp.get("verification") or {}
            welcome_name_gate = welcome_cfg_gate.get("channel")
            verify_name_gate = verify_cfg_gate.get("channel")
            blueprint_channel_by_name = {
                ch.get("name"): ch
                for cat in bp.get("categories", [])
                for ch in cat.get("channels", [])
                if ch.get("name")
            }
            for live_channel in list(guild.text_channels) + list(guild.voice_channels):
                spec = blueprint_channel_by_name.get(live_channel.name) or {}
                raw_ows = spec.get("permission_overwrites") or []
                private_by_spec = bool(spec.get("private_roles"))
                # Explicit @everyone view deny plus a role view allow means this is
                # a deliberately private channel; preserve its intended staff access.
                everyone_view_denied = any(
                    str(ow.get("target", "")).lower() == "@everyone"
                    and (int(ow.get("deny") or 0) & 1024)
                    for ow in raw_ows
                )
                role_view_allowed = any(
                    ow.get("target_type") == "role"
                    and str(ow.get("target", "")).lower() != "@everyone"
                    and (int(ow.get("allow") or 0) & 1024)
                    for ow in raw_ows
                )
                is_private_gate = private_by_spec or everyone_view_denied
                is_open_gate = live_channel.name not in (welcome_name_gate, verify_name_gate)
                try:
                    await live_channel.set_permissions(everyone, view_channel=False)
                    await live_channel.set_permissions(
                        unverified,
                        view_channel=not is_open_gate,
                        send_messages=False,
                        read_message_history=True,
                    )
                    if verified_role is not None and not is_private_gate and is_open_gate:
                        await live_channel.set_permissions(verified_role, view_channel=True)
                    elif verified_role is not None and is_private_gate:
                        await live_channel.set_permissions(verified_role, view_channel=False)
                except discord.HTTPException as e:
                    self.log(f"Could not enforce verification gate on {live_channel.name}: {e}")

        # "show_locked": true on a category (or channel) = verified members can SEE it but not use it.
        # Runs after the verification gate above, which hides private channels from the Member role.
        lock_channels, lock_categories = [], []
        for cat in bp.get("categories", []):
            if cat.get("show_locked"):
                category_obj = discord.utils.get(guild.categories, name=cat.get("name"))
                if category_obj is not None:
                    lock_categories.append(category_obj)
            for ch in cat.get("channels", []):
                obj = channels.get(ch.get("name"))
                if obj is not None and (cat.get("show_locked") or ch.get("show_locked")):
                    lock_channels.append(obj)
        if lock_channels:
            await self._apply_show_locked(guild, lock_channels, lock_categories,
                                          verified_role or everyone, unverified)

        # put the categories in the same order as the blueprint (top to bottom)
        if keep_channels:
            self.log("Category order untouched (Keep categories is on).")
        else:
            for i, category in enumerate(ordered_categories):
                try:
                    await category.edit(position=i)
                except discord.HTTPException as e:
                    self.log(f"Could not reorder {category.name}: {e}")
            self.log("Categories ordered.")

        # Discord's real "Inactive channel" setting (Server Settings > Overview)
        afk = bp.get("afk")
        if afk and isinstance(channels.get(afk.get("channel")), discord.VoiceChannel):
            try:
                await guild.edit(afk_channel=channels[afk["channel"]],
                                 afk_timeout=int(afk.get("timeout", 300)),
                                 reason="AI server builder")
                self.log(f"AFK channel set: {afk['channel']} ({afk.get('timeout', 300)} s)")
            except (discord.HTTPException, ValueError) as e:
                self.log(f"Could not set the AFK channel: {e}")

        cfg = {}
        staff = [roles[n] for n in staff_names]

        w = bp.get("welcome")
        if w:
            role = roles.get(w.get("auto_role"))
            ch = channels.get(w.get("channel"))
            cfg["welcome"] = {
                "channel_id": ch.id if ch else None,
                "auto_role_id": role.id if role else None,
                "message": w.get("message") or "Welcome {user} to {server}!",
            }
        a = bp.get("antispam")
        if a:
            cfg["antispam"] = {**ANTISPAM_DEFAULTS, **a}

        if ver and unverified is not None and verified_role is not None:
            vch = channels.get(ver.get("channel"))
            cfg["verification"] = {"unverified_role_id": unverified.id,
                                   "verified_role_id": verified_role.id,
                                   "channel_id": vch.id if vch else None,
                                   "text": ver.get("text") or "Press the button to verify yourself and unlock the server.",
                                   "button_label": ver.get("button_label") or "Verify me"}
            if vch:
                await vch.send(
                    ver.get("text") or "Press the button to verify yourself and unlock the server.",
                    view=VerifyPanel(ver.get("button_label") or "Verify me"))
                self.log("Verification panel sent.")
            else:
                self.log("Verification channel not found in the blueprint.")

        t = bp.get("tickets")
        if t and t.get("channel") in channels:
            ch = channels[t["channel"]]
            if _need_perms(t["channel"]):
                await ch.set_permissions(everyone, send_messages=False)
            staff_ids = [roles[n].id for n in t.get("staff_roles", staff_names) if n in roles]
            cfg["tickets"] = {"staff_role_ids": staff_ids}
            await ch.send(t.get("text") or "Need help? Press the button to open a private ticket.",
                          view=TicketPanel())
            self.log("Ticket panel sent.")

        rp = bp.get("role_panel")
        if rp and rp.get("channel") in channels:
            items = [(roles[n].id, n) for n in rp.get("roles", [])
                     if n in roles and role_perm.get(n, "none") == "none"]
            if items:
                ch = channels[rp["channel"]]
                if _need_perms(rp["channel"]):
                    await ch.set_permissions(everyone, send_messages=False)
                await ch.send(rp.get("text") or "Pick your roles:", view=RolePanel(items))
                cfg["role_panel"] = {"roles": [{"role_id": i, "label": n} for i, n in items]}
                self.log("Role panel sent.")

        lv = bp.get("levels")
        if lv and lv.get("enabled", True):
            ch = channels.get(lv.get("channel"))
            rewards = []
            for x in lv.get("roles", []):
                if (x.get("role") in roles and role_perm.get(x["role"], "none") == "none"
                        and str(x.get("level", "")).isdigit()):
                    rewards.append({"level": int(x["level"]), "role_id": roles[x["role"]].id})
            cfg["levels"] = {"enabled": True, "channel_id": ch.id if ch else None, "roles": rewards}

        lg = bp.get("logs")
        if lg and lg.get("channel") in channels:
            ch = channels[lg["channel"]]
            sub = {k: channels[lg[k]] for k in ("voice", "messages", "members", "mod", "server")
                   if lg.get(k) in channels}
            log_items = [(lg["channel"], ch)] + [(lg[k], c) for k, c in sub.items()]
            for name, c in log_items:
                if not _need_perms(name):
                    continue
                await c.set_permissions(everyone, view_channel=False)
                for r in staff:
                    await c.set_permissions(r, view_channel=True)
            cfg["logs"] = {"channel_id": ch.id, "channels": {k: c.id for k, c in sub.items()}}
        cfg["staff_role_ids"] = [r.id for r in staff]

        # ---- extras: LFG, starboard, coins + shop, anti-nuke ----
        lf = bp.get("lfg")
        if lf and lf.get("channel") in channels:
            cat = discord.utils.get(guild.categories, name=lf.get("category")) if lf.get("category") else None
            cfg["lfg"] = {"channel_id": channels[lf["channel"]].id,
                          "category_id": cat.id if cat else None,
                          "max_players": int(lf.get("max_players", 5))}
            self.log(f"LFG ready: members use /lfg in #{channels[lf['channel']].name}")
        sb = bp.get("starboard")
        if sb and sb.get("target") in channels:
            cfg["starboard"] = {
                "target_id": channels[sb["target"]].id,
                "source_ids": [channels[n].id for n in sb.get("sources", []) if n in channels],
                "emoji": sb.get("emoji", "⭐"), "threshold": int(sb.get("threshold", 3))}
            self.log("Starboard ready.")
        eco = bp.get("economy")
        if eco and eco.get("enabled", True):
            shop = []
            for it in eco.get("shop", []):
                r = roles.get(it.get("role"))
                if r and role_perm.get(it["role"], "none") == "none" and str(it.get("price", "")).isdigit():
                    shop.append({"name": it.get("name") or it["role"], "role_id": r.id,
                                 "price": int(it["price"])})
            pm = eco.get("per_message") or [1, 3]
            cfg["economy"] = {"enabled": True, "daily": int(eco.get("daily", 100)),
                              "per_message": [int(pm[0]), int(pm[-1])],
                              "voice_per_minute": int(eco.get("voice_per_minute", 1)),
                              "exclusive": bool(eco.get("exclusive", True)), "shop": shop}
            self.log(f"Coins ready with {len(shop)} shop item(s): /daily /balance /shop /buy /richest")
        cv = bp.get("custom_vc") or {}
        trigger_name = cv.get("trigger")
        trigger_channel = channels.get(trigger_name) if trigger_name else None
        # Older exported JSONs omitted custom_vc. Recover by detecting the trigger
        # voice channel by its exact/common name instead of silently disabling the feature.
        if trigger_channel is None:
            trigger_channel = next((ch for ch in guild.voice_channels
                                    if "create room" in ch.name.casefold()
                                    or "create-your-room" in ch.name.casefold()), None)
        if trigger_channel is not None and isinstance(trigger_channel, (discord.VoiceChannel, discord.StageChannel)):
            cfg["custom_vc"] = {"trigger_id": trigger_channel.id,
                                "name_format": cv.get("name_format") or "🔒 Private ┃ {name}"}
            self.log(f"Custom channels ready: join #{trigger_channel.name} "
                     "to get your own voice channel (/vc rename, /vc limit, /vc kick, /vc close)")
        elif cv:
            self.log(f"Custom VC trigger not found: {trigger_name!r}; check that the JSON trigger name matches a voice channel.")
        asst = bp.get("assistant")
        if asst and asst.get("channel") in channels:
            need = roles.get(asst.get("member_role"))
            cfg["assistant"] = {"channel_id": channels[asst["channel"]].id,
                                "member_role_id": need.id if need else None}
            top = roles.get(asst.get("max_role") or "🔥 OG")
            if top:   # the AI may give roles up to (and including) this one, never above it
                cfg["assistant"]["max_role_id"] = top.id
                cfg["assistant"]["exclude_ids"] = [roles[n].id for n in asst.get("exclude_roles", [])
                                                   if n in roles]
            await channels[asst["channel"]].send(
                "🤖 **GOGO Assistant**\nاضغط على الزر باش تسول Gogo مباشرة.\n"
                "تقدر حتى تكتب سؤالك هنا عادي.",
                view=assistant.GogoPanel())
            self.log(f"AI assistant ready in #{channels[asst['channel']].name} "
                     "(answers about the server in any language)")

        an = bp.get("antinuke")
        if an and an.get("enabled", True):
            cfg["antinuke"] = {
                "enabled": True, "max_actions": int(an.get("max_actions", 3)),
                "seconds": int(an.get("seconds", 30)),
                "whitelist_role_ids": [roles[n].id for n in an.get("whitelist_roles", []) if n in roles]}
            self.log("Anti-nuke on. Admins can use /lockdown and /unlock.")

        # ---- other bots (ProBot, Dyno, ...) ----
        # "bots": {"welcome": "ProBot", "logs": "Dyno"} in the JSON hands those modules to them.
        # Modules without an assignment are left to a detected bot only when it usually covers them.
        found = await self.detect_bots(guild)
        assign = bp.get("bots") or {}
        external, assigned = {}, {}
        for mod in MODULES:
            if mod in own_modules or not bp.get(mod):
                continue
            want = str(assign.get(mod) or "").strip().lower()
            if want and want not in ("mine", "me", "own", "self"):
                info = next((f for f in found if f["name"].lower() == want), None) or \
                    next((f for f in found if want in f["name"].lower()), None)
                if info is None:
                    self.log(f"'{mod}' is assigned to {assign[mod]}, but that bot is not in the server. "
                             "Invite it first; my bot keeps doing this module meanwhile.")
                    continue
                external[mod] = info["name"]
                assigned[mod] = info
                continue
            if want:
                continue  # explicitly mine
            if mod == "welcome" and ver:
                continue  # our bot must give the Unverified role
            for info in found:
                if mod in info["covers"]:
                    external[mod] = info["name"]
                    break
        cfg["external"] = external
        for info in found:
            self.log(f"Detected bot: {info['name']} - usually covers: "
                     f"{', '.join(info['covers']) or 'unknown'}")
        for mod, name in external.items():
            self.log(f"'{mod}' is left to {name}.")

        # let the assigned bots read and write in the channels they will use
        for mod, info in assigned.items():
            try:
                member = guild.get_member(info["id"]) or await guild.fetch_member(info["id"])
            except discord.HTTPException:
                continue
            if mod == "welcome":
                chans = [channels.get((bp.get("welcome") or {}).get("channel"))]
            elif mod == "logs":
                chans = [channels.get((bp.get("logs") or {}).get(k))
                         for k in ("channel", "members", "messages", "voice", "mod", "server")]
            else:
                chans = []
            for c in filter(None, chans):
                try:
                    await c.set_permissions(member, view_channel=True, send_messages=True,
                                            embed_links=True, attach_files=True,
                                            reason=f"{info['name']} handles {mod}")
                except discord.HTTPException as e:
                    self.log(f"Could not give {info['name']} access to #{c.name}: {e}")
        if assigned:
            self._write_checklist(bp, assigned, channels)

        merged = self.config.get(str(guild.id), {})
        merged.update(cfg)
        self.config[str(guild.id)] = merged
        save_config(self.config)
        self.log("Modules configured.")

        if make_visuals and bp.get("visuals"):
            await self.apply_visuals(guild, bp["visuals"], openai_key)

    async def apply_visuals(self, guild, vis, openai_key):
        loop = asyncio.get_running_loop()
        self.log("Generating images (this can take a few minutes)...")

        if vis.get("icon_prompt"):
            try:
                data = await loop.run_in_executor(None, make_icon, vis["icon_prompt"], openai_key)
                with open(os.path.join(ASSETS_DIR, "icon.png"), "wb") as f:
                    f.write(data)
                await guild.edit(icon=data)
                self.log("Server icon set.")
            except Exception as e:
                self.log(f"Icon failed: {e}")

        if vis.get("banner_prompt"):
            try:
                data = await loop.run_in_executor(None, make_banner, vis["banner_prompt"], openai_key)
                path = os.path.join(ASSETS_DIR, "banner.png")
                with open(path, "wb") as f:
                    f.write(data)
                if "BANNER" in guild.features:
                    await guild.edit(banner=data)
                    self.log("Server banner set.")
                else:
                    self.log(f"Banner saved to {path} (the server needs Boost level 2 to use banners).")
            except Exception as e:
                self.log(f"Banner failed: {e}")

        for e in vis.get("emojis", [])[:8]:
            name = re.sub(r"\W", "_", str(e.get("name", "")))[:32]
            if len(name) < 2 or not e.get("prompt"):
                continue
            try:
                data = await loop.run_in_executor(None, make_emoji, e["prompt"], openai_key)
                await guild.create_custom_emoji(name=name, image=data, reason="AI server builder")
                self.log(f"Emoji created: :{name}:")
            except Exception as ex:
                self.log(f"Emoji {name} failed: {ex}")


class BotRunner:
    """Runs the Discord bot in a background thread so the GUI stays responsive."""

    def __init__(self, log):
        self.log = log
        self.loop = None
        self.bot = None
        self.thread = None

    def start(self, token, ai_key=None):
        if self.thread and self.thread.is_alive():
            return
        self.loop = asyncio.new_event_loop()
        self.bot = ServerBot(self.log, ai_key)

        def run():
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self.bot.start(token))
            except discord.LoginFailure:
                self.log("Invalid bot token.")
            except discord.PrivilegedIntentsRequired:
                self.log("Enable 'Server Members' and 'Message Content' intents in the Developer Portal.")
            except Exception as e:
                self.log(f"Bot error: {e}")
            finally:
                tasks = asyncio.all_tasks(self.loop)
                for t in tasks:
                    t.cancel()
                self.loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)
