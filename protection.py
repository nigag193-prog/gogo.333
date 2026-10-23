"""Server protection for the bot: backup / restore, anti-nuke, lockdown.

  /backup                  OWNER ONLY - saves roles, channels, permissions and members' roles to a file.
                           After the first /backup the bot also saves one automatically every day.
  /restore [backup]        OWNER ONLY - brings back what was DELETED (roles, categories, channels, their
                           permissions and the members' roles). It never deletes or changes what still exists.
  /antinuke on|off|status  OWNER ONLY - anti-nuke is ON by default.
  Protected roles          created by telling GOGO ("sawb rol X w 7miH") - nobody can take such a role away from a
                           member except the owner or a member who has an Owner / Founder role. The bot puts it back.
  /serverlock [reason]     OWNER ONLY - nobody can write in the text channels (staff roles with their own
                           "allow" on a channel can still write). /serverunlock puts everything back exactly as it was.

Anti-nuke: if a member (or another bot) deletes / creates / bans / kicks too much in a few seconds, the bot
punishes him at once and sends a DM to the server owner. The server OWNER is never punished, neither is this
bot. Everybody else is (admins included).

Bot needs: Administrator (or Manage Roles + Manage Channels + Ban/Kick + View Audit Log) and the
Server Members intent. Its role must be ABOVE the roles it has to manage.

.env options (all optional):
    ANTINUKE_LIMIT=3          how many deletes/bans/kicks are allowed inside the window (creations: 2x)
    ANTINUKE_SECONDS=15       the window, in seconds
    ANTINUKE_PUNISH=strip     strip = remove all his roles | kick | ban     (a bot is always kicked)
    ANTINUKE_WHITELIST=       ids (comma separated) of trusted members/bots that are never punished
    BACKUP_HOURS=24           automatic backup every N hours (0 = off)
    BACKUP_KEEP=7             how many backups to keep per server
"""
import asyncio
import json
import os
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK_PERMS = ("send_messages", "send_messages_in_threads", "create_public_threads",
              "create_private_threads", "add_reactions")


# ---------------------------------------------------------------- helpers
def _env(name, default=""):
    val = os.environ.get(name, "").strip()
    path = os.path.join(HERE, ".env")
    if not val and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(name + "="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
    return val or default


def _int(name, default):
    raw = _env(name, str(default))
    return int(raw) if raw.isdigit() else default


def _data_dir():
    return os.environ.get("DATA_DIR") or HERE


def _backup_dir(gid):
    d = os.path.join(_data_dir(), "backups", str(gid))
    os.makedirs(d, exist_ok=True)
    return d


def is_owner(guild, user):
    return user.id == guild.owner_id


async def _owner_only(inter):
    if inter.guild and is_owner(inter.guild, inter.user):
        return True
    await inter.response.send_message("Only the server owner can use this command.", ephemeral=True)
    return False


# ---------------------------------------------------------------- backup
def snapshot(guild):
    roles = []
    for r in sorted(guild.roles, key=lambda r: r.position):
        if r.is_default() or r.managed:
            continue
        roles.append({"id": r.id, "name": r.name, "color": r.color.value, "hoist": r.hoist,
                      "mentionable": r.mentionable, "permissions": r.permissions.value,
                      "position": r.position})
    chans = []
    for c in sorted(guild.channels, key=lambda c: (c.type != discord.ChannelType.category, c.position)):
        if isinstance(c, discord.Thread):
            continue
        ows = []
        for target, ow in c.overwrites.items():
            allow, deny = ow.pair()
            ows.append({"type": "role" if isinstance(target, discord.Role) else "member",
                        "id": target.id, "name": target.name, "allow": allow.value, "deny": deny.value})
        chans.append({"id": c.id, "name": c.name, "type": c.type.name, "position": c.position,
                      "category": c.category_id, "overwrites": ows,
                      "topic": getattr(c, "topic", None), "nsfw": bool(getattr(c, "nsfw", False)),
                      "slowmode": getattr(c, "slowmode_delay", 0) or 0,
                      "bitrate": getattr(c, "bitrate", None), "user_limit": getattr(c, "user_limit", 0) or 0})
    members = {}
    for m in guild.members:
        ids = [r.id for r in m.roles if not r.is_default() and not r.managed]
        if ids:
            members[str(m.id)] = ids
    return {"version": 1, "guild_id": guild.id, "guild_name": guild.name,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "everyone_permissions": guild.default_role.permissions.value,
            "roles": roles, "channels": chans, "members": members}


def list_backups(gid):
    d = _backup_dir(gid)
    return sorted((f for f in os.listdir(d) if f.endswith(".json")), reverse=True)


def save_backup(guild):
    data = snapshot(guild)
    name = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".json"
    d = _backup_dir(guild.id)
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        json.dump(data, f)
    for old in list_backups(guild.id)[max(1, _int("BACKUP_KEEP", 7)):]:
        try:
            os.remove(os.path.join(d, old))
        except OSError:
            pass
    return name, data


def load_backup(gid, name=None):
    files = list_backups(gid)
    if not files:
        return None, None
    name = name if name in files else files[0]
    with open(os.path.join(_backup_dir(gid), name), encoding="utf-8") as f:
        return name, json.load(f)


async def _chunk(guild):
    try:
        if not guild.chunked:
            await guild.chunk()
    except Exception:
        pass


def _overwrites(guild, lst, role_map, by_name):
    out = {}
    for o in lst:
        if o["type"] == "role":
            target = role_map.get(o["id"]) or by_name.get(o["name"])
        else:
            target = guild.get_member(o["id"])
        if target is None:
            continue
        out[target] = discord.PermissionOverwrite.from_pair(discord.Permissions(o["allow"]),
                                                            discord.Permissions(o["deny"]))
    return out


async def _make_channel(guild, cd, parent, ow):
    t, why = cd["type"], "backup restore"
    base = dict(name=cd["name"], overwrites=ow, reason=why)
    if t == "category":
        return await guild.create_category(**base)
    extra = dict(category=parent)
    if t in ("text", "news"):
        extra.update(topic=cd.get("topic") or None, nsfw=cd.get("nsfw", False), slowmode_delay=cd.get("slowmode", 0))
        if t == "news":
            try:
                return await guild.create_text_channel(**base, **extra, news=True)
            except Exception:
                pass
        return await guild.create_text_channel(**base, **extra)
    if t == "voice":
        bitrate = min(cd.get("bitrate") or 64000, guild.bitrate_limit)
        return await guild.create_voice_channel(**base, **extra, bitrate=bitrate, user_limit=cd.get("user_limit", 0))
    if t == "stage_voice":
        if cd.get("topic"):
            extra["topic"] = cd["topic"]
        return await guild.create_stage_channel(**base, **extra)
    if t == "forum":
        extra.update(topic=cd.get("topic") or None, nsfw=cd.get("nsfw", False), slowmode_delay=cd.get("slowmode", 0))
        return await guild.create_forum(**base, **extra)
    return None


async def restore(guild, data):
    stats = {"roles": 0, "channels": 0, "members": 0, "errors": []}
    me = guild.me
    role_map = {guild.id: guild.default_role}
    by_name = {}
    for r in guild.roles:
        by_name.setdefault(r.name, r)
    new_roles = {}

    for rd in data.get("roles", []):                                   # lowest position first
        r = guild.get_role(rd["id"])
        if r is None:
            r = by_name.get(rd["name"])
        if r is None:
            try:
                r = await guild.create_role(name=rd["name"], colour=discord.Colour(rd["color"]),
                                            hoist=rd["hoist"], mentionable=rd["mentionable"],
                                            permissions=discord.Permissions(rd["permissions"]),
                                            reason="backup restore")
                new_roles[rd["id"]] = r
                by_name[r.name] = r
                stats["roles"] += 1
            except discord.HTTPException as e:
                stats["errors"].append(f"role {rd['name']}: {e}")
                continue
        role_map[rd["id"]] = r

    top = me.top_role.position - 1
    for rd in data.get("roles", []):                                   # best effort: put new roles back in order
        r = new_roles.get(rd["id"])
        if r is not None:
            try:
                await r.edit(position=max(1, min(rd["position"], top)), reason="backup restore")
            except discord.HTTPException:
                pass

    chan_map = {}
    chans = data.get("channels", [])
    for cd in [c for c in chans if c["type"] == "category"] + [c for c in chans if c["type"] != "category"]:
        existing = guild.get_channel(cd["id"])
        parent = chan_map.get(cd["category"]) if cd.get("category") else None
        if existing is None:
            for c in guild.channels:                                   # same name + type (+ same category)
                if c.name == cd["name"] and c.type.name == cd["type"] and \
                        (cd["type"] == "category" or getattr(c, "category", None) == parent):
                    existing = c
                    break
        if existing is not None:
            chan_map[cd["id"]] = existing
            continue
        try:
            ow = _overwrites(guild, cd.get("overwrites", []), role_map, by_name)
            ch = await _make_channel(guild, cd, parent, ow)
            if ch is not None:
                chan_map[cd["id"]] = ch
                stats["channels"] += 1
        except discord.HTTPException as e:
            stats["errors"].append(f"channel {cd['name']}: {e}")

    if new_roles:                                                      # give the recreated roles back to the members
        sem = asyncio.Semaphore(4)

        async def give(uid, ids):
            m = guild.get_member(int(uid))
            roles = [new_roles[i] for i in ids if i in new_roles]
            if m is None or not roles:
                return
            async with sem:
                try:
                    await m.add_roles(*roles, reason="backup restore")
                    stats["members"] += 1
                except discord.HTTPException:
                    pass
        await asyncio.gather(*(give(u, ids) for u, ids in data.get("members", {}).items()))
    return stats


# ---------------------------------------------------------------- lockdown
def _lock_path(gid):
    return os.path.join(_data_dir(), f"lockdown_{gid}.json")


async def do_lockdown(guild, reason):
    everyone, state, failed = guild.default_role, {}, 0
    sem = asyncio.Semaphore(8)

    async def lock(ch):
        nonlocal failed
        ow = ch.overwrites_for(everyone)
        state[str(ch.id)] = {p: getattr(ow, p) for p in LOCK_PERMS}
        for p in LOCK_PERMS:
            setattr(ow, p, False)
        async with sem:
            try:
                await ch.set_permissions(everyone, overwrite=ow, reason=f"lockdown: {reason}")
            except discord.HTTPException:
                failed += 1
                state.pop(str(ch.id), None)
    await asyncio.gather(*(lock(c) for c in guild.text_channels))
    with open(_lock_path(guild.id), "w", encoding="utf-8") as f:
        json.dump(state, f)
    return len(state), failed


async def do_unlock(guild):
    with open(_lock_path(guild.id), encoding="utf-8") as f:
        state = json.load(f)
    everyone, done, failed = guild.default_role, 0, 0
    sem = asyncio.Semaphore(8)

    async def unlock(cid, saved):
        nonlocal done, failed
        ch = guild.get_channel(int(cid))
        if ch is None:
            return
        ow = ch.overwrites_for(everyone)
        for p, v in saved.items():
            setattr(ow, p, v)
        async with sem:
            try:
                await ch.set_permissions(everyone, overwrite=None if ow.is_empty() else ow, reason="unlock")
                done += 1
            except discord.HTTPException:
                failed += 1
    await asyncio.gather(*(unlock(c, s) for c, s in state.items()))
    os.remove(_lock_path(guild.id))
    return done, failed


# ---------------------------------------------------------------- anti-nuke
_hits = defaultdict(deque)
_punished = {}
_ACTIONS = {"channel_delete": discord.AuditLogAction.channel_delete,
            "channel_create": discord.AuditLogAction.channel_create,
            "role_delete": discord.AuditLogAction.role_delete,
            "role_create": discord.AuditLogAction.role_create,
            "ban": discord.AuditLogAction.ban,
            "kick": discord.AuditLogAction.kick}


def _enabled(bot, gid):
    cfg = bot.config.get(str(gid))
    return not (isinstance(cfg, dict) and (cfg.get("antinuke") or {}).get("enabled") is False)


def _exempt(bot, guild, user):
    white = {int(x) for x in _env("ANTINUKE_WHITELIST").replace(" ", "").split(",") if x.isdigit()}
    return user.id == guild.owner_id or (bot.user and user.id == bot.user.id) or user.id in white


async def _executor(guild, kind, target_id):
    await asyncio.sleep(1.2)                                           # let Discord write the audit-log entry
    try:
        async for e in guild.audit_logs(limit=8, action=_ACTIONS[kind]):
            if (datetime.now(timezone.utc) - e.created_at).total_seconds() > 20:
                break
            if getattr(e.target, "id", None) == target_id:
                return e.user
    except discord.HTTPException:
        pass
    return None


async def _punish(bot, guild, user, kind, count):
    mode = _env("ANTINUKE_PUNISH", "strip").lower()
    member = guild.get_member(user.id)
    result = ""
    try:
        if user.bot or mode == "kick":
            await guild.kick(user, reason=f"anti-nuke: {count}x {kind}")
            result = "kicked"
        elif mode == "ban":
            await guild.ban(user, reason=f"anti-nuke: {count}x {kind}", delete_message_seconds=0)
            result = "banned"
        else:
            roles = [r for r in (member.roles if member else []) if not r.is_default() and not r.managed
                     and r < guild.me.top_role]
            if roles:
                await member.remove_roles(*roles, reason=f"anti-nuke: {count}x {kind}")
            result = f"all his roles were removed ({len(roles)})"
    except discord.HTTPException as e:
        result = f"FAILED ({e}); move my role above his role and give me Administrator"
    bot.log(f"[antinuke] {guild.name}: {user} did {count}x {kind} -> {result}")
    try:
        owner = guild.owner or await guild.fetch_member(guild.owner_id)
        emb = discord.Embed(title="🛡️ Anti-nuke triggered", color=0xE74C3C,
                            description=f"**{user}** (`{user.id}`) did **{count}x {kind.replace('_', ' ')}** "
                                        f"in a few seconds in **{guild.name}**.\nAction: {result}.")
        emb.set_footer(text="Use /restore to bring back what was deleted.")
        await owner.send(embed=emb)
    except Exception:
        pass


async def _record(bot, guild, kind, target_id):
    if not _enabled(bot, guild.id):
        return
    user = await _executor(guild, kind, target_id)
    if user is None or _exempt(bot, guild, user):
        return
    now, window = time.monotonic(), _int("ANTINUKE_SECONDS", 15)
    dq = _hits[(guild.id, user.id, kind)]
    dq.append(now)
    while dq and now - dq[0] > window:
        dq.popleft()
    limit = _int("ANTINUKE_LIMIT", 3) * (2 if kind.endswith("create") else 1)
    key = (guild.id, user.id)
    if len(dq) >= limit and now - _punished.get(key, -999) > 120:
        _punished[key] = now
        await _punish(bot, guild, user, kind, len(dq))


FOUNDER_RE = re.compile(r"(owner|founder|fonder|مؤسس|اونر|أونر)", re.I)


def role_remover_allowed(bot, guild, user):
    """Who may take a protected role away: the owner, the bot itself (= the owner via GOGO), GOGO_ADMIN_IDS
    and members that hold an Owner / Founder role."""
    if user is None:
        return False
    if user.id == guild.owner_id or (bot.user and user.id == bot.user.id):
        return True
    try:
        import owner_ai
        if owner_ai.is_owner(guild, user):
            return True
    except Exception:
        pass
    member = guild.get_member(user.id)
    return bool(member and any(FOUNDER_RE.search(r.name) for r in member.roles if not r.is_default()))


async def _role_executor(guild, member, removed_ids):
    for delay in (1.2, 3.0):                                           # the audit-log entry can be late
        await asyncio.sleep(delay)
        try:
            async for e in guild.audit_logs(limit=10, action=discord.AuditLogAction.member_role_update):
                if (datetime.now(timezone.utc) - e.created_at).total_seconds() > 30:
                    break
                if getattr(e.target, "id", None) != member.id:
                    continue
                gone = {r.id for r in (getattr(e.before, "roles", None) or [])}
                if not gone or gone & removed_ids:
                    return e.user
        except discord.HTTPException:
            return None
    return None


def _hook(bot, name, fn):
    orig = getattr(bot, name, None)

    async def wrapper(*args):
        try:
            if orig:
                await orig(*args)
        finally:
            asyncio.create_task(fn(*args))
    setattr(bot, name, wrapper)


# ---------------------------------------------------------------- register
def register(bot):
    """Adds the commands, the anti-nuke listeners and the daily backup (call once, before bot.run)."""

    def _cmd(name, **kw):
        """Like bot.tree.command, but if the bot already has a command with this name it is left alone
        (our version is skipped and a line is written in the log)."""
        if bot.tree.get_command(name) is not None:
            bot.log(f"[protection] /{name} already exists in the bot: the one from protection.py was skipped")
            return lambda fn: fn
        return bot.tree.command(name=name, **kw)

    async def _backup_autocomplete(inter: discord.Interaction, current: str):
        out = []
        for f in list_backups(inter.guild_id):
            try:
                label = datetime.strptime(f[:-5], "%Y%m%d-%H%M%S").strftime("%Y-%m-%d %H:%M UTC")
            except ValueError:
                label = f
            if current.lower() in label.lower():
                out.append(app_commands.Choice(name=label, value=f))
        return out[:25]

    @_cmd("backup", description="Owner only: save roles, channels and permissions of this server")
    @app_commands.guild_only()
    async def backup_cmd(inter: discord.Interaction):
        if not await _owner_only(inter):
            return
        await inter.response.defer(ephemeral=True)
        await _chunk(inter.guild)
        name, data = await asyncio.to_thread(save_backup, inter.guild)
        cfg = bot.config.setdefault(str(inter.guild_id), {})
        cfg["backup"] = {"auto": True}
        bot.persist()
        await inter.followup.send(f"✅ Backup saved: **{len(data['roles'])}** roles, **{len(data['channels'])}** "
                                  f"channels, **{len(data['members'])}** members with roles.\n"
                                  "A new one is saved automatically every day. Use /restore if something gets deleted.",
                                  ephemeral=True)

    @_cmd("restore", description="Owner only: bring back deleted roles and channels from a backup")
    @app_commands.describe(backup="Which backup (empty = the newest)")
    @app_commands.autocomplete(backup=_backup_autocomplete)
    @app_commands.guild_only()
    async def restore_cmd(inter: discord.Interaction, backup: Optional[str] = None):
        if not await _owner_only(inter):
            return
        await inter.response.defer(ephemeral=True)
        name, data = await asyncio.to_thread(load_backup, inter.guild_id, backup)
        if data is None:
            await inter.followup.send("There is no backup yet. Use /backup first.", ephemeral=True)
            return
        await _chunk(inter.guild)
        st = await restore(inter.guild, data)
        msg = (f"✅ Restore finished (backup of {data.get('created', name)}): **{st['roles']}** roles and "
               f"**{st['channels']}** channels were recreated, roles given back to **{st['members']}** members.\n"
               "What still existed was not touched.")
        if st["errors"]:
            msg += "\n⚠️ Problems:\n" + "\n".join(st["errors"][:8]) + "\n(Give the bot Administrator and put its role on top.)"
        await inter.followup.send(msg, ephemeral=True)

    @_cmd("antinuke", description="Owner only: turn the anti-nuke protection on or off")
    @app_commands.describe(mode="on / off / status")
    @app_commands.choices(mode=[app_commands.Choice(name="status", value="status"),
                                app_commands.Choice(name="on", value="on"),
                                app_commands.Choice(name="off", value="off")])
    @app_commands.guild_only()
    async def antinuke_cmd(inter: discord.Interaction, mode: str = "status"):
        if not await _owner_only(inter):
            return
        if mode in ("on", "off"):
            cfg = bot.config.setdefault(str(inter.guild_id), {})
            cfg["antinuke"] = {"enabled": mode == "on"}
            bot.persist()
        state = "ON 🟢" if _enabled(bot, inter.guild_id) else "OFF 🔴"
        await inter.response.send_message(
            f"Anti-nuke is **{state}**. Limit: {_int('ANTINUKE_LIMIT', 3)} actions in "
            f"{_int('ANTINUKE_SECONDS', 15)} s, punishment: {_env('ANTINUKE_PUNISH', 'strip')}. "
            "The owner is never punished.", ephemeral=True)

    @_cmd("serverlock", description="Owner only: nobody can write in the text channels")
    @app_commands.describe(reason="Why (optional)")
    @app_commands.guild_only()
    async def lockdown_cmd(inter: discord.Interaction, reason: Optional[str] = "lockdown"):
        if not await _owner_only(inter):
            return
        if os.path.exists(_lock_path(inter.guild_id)):
            await inter.response.send_message("The server is already locked. Use /serverunlock.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        done, failed = await do_lockdown(inter.guild, reason or "lockdown")
        await inter.followup.send(f"🔒 Lockdown ON: **{done}** channels locked"
                                  + (f", {failed} failed (check my permissions)" if failed else "")
                                  + ". Use /serverunlock to open them again.", ephemeral=True)

    @_cmd("serverunlock", description="Owner only: end the lockdown")
    @app_commands.guild_only()
    async def unlock_cmd(inter: discord.Interaction):
        if not await _owner_only(inter):
            return
        if not os.path.exists(_lock_path(inter.guild_id)):
            await inter.response.send_message("The server is not locked.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        done, failed = await do_unlock(inter.guild)
        await inter.followup.send(f"🔓 Lockdown OFF: **{done}** channels back to normal"
                                  + (f", {failed} failed" if failed else "") + ".", ephemeral=True)

    # --- anti-nuke listeners --------------------------------------------------------
    async def ch_delete(ch):
        await _record(bot, ch.guild, "channel_delete", ch.id)

    async def ch_create(ch):
        await _record(bot, ch.guild, "channel_create", ch.id)

    async def role_delete(role):
        await _record(bot, role.guild, "role_delete", role.id)

    async def role_create(role):
        await _record(bot, role.guild, "role_create", role.id)

    async def member_ban(guild, user):
        await _record(bot, guild, "ban", user.id)

    async def member_remove(member):
        await _record(bot, member.guild, "kick", member.id)           # only counts if the audit log says "kick"

    async def role_guard(before, after):
        guild = after.guild
        cfg = bot.config.get(str(guild.id))
        prot = set((cfg or {}).get("protected_roles") or []) if isinstance(cfg, dict) else set()
        if not prot:
            return
        removed = [r for r in before.roles if r.id in prot and r not in after.roles]
        if not removed:
            return
        who = await _role_executor(guild, after, {r.id for r in removed})
        if who is None or role_remover_allowed(bot, guild, who):
            return                                                    # unknown executor: do not guess
        try:
            await after.add_roles(*removed, reason=f"protected role: {who} is not allowed to remove it")
            result = "I put it back."
        except discord.HTTPException as e:
            result = f"I could not put it back ({e}); move my role above it."
        bot.log(f"[roleguard] {who} tried to remove {', '.join(r.name for r in removed)} from {after}: {result}")
        try:
            await bot.event_log(guild, "mod", "🔒 Protected role",
                                f"{who.mention} tried to remove **{', '.join(r.name for r in removed)}** from "
                                f"{after.mention}. {result}", 0xE74C3C)
        except Exception:
            pass

    for name, fn in (("on_member_update", role_guard), ("on_guild_channel_delete", ch_delete), ("on_guild_channel_create", ch_create),
                     ("on_guild_role_delete", role_delete), ("on_guild_role_create", role_create),
                     ("on_member_ban", member_ban), ("on_member_remove", member_remove)):
        _hook(bot, name, fn)

    # --- daily automatic backup ---------------------------------------------------
    async def backup_loop():
        await bot.wait_until_ready()
        hours = _int("BACKUP_HOURS", 24)
        if not hours:
            return
        while not bot.is_closed():
            await asyncio.sleep(hours * 3600)
            for gid, cfg in list(bot.config.items()):
                try:
                    if isinstance(cfg, dict) and (cfg.get("backup") or {}).get("auto") and gid.isdigit():
                        guild = bot.get_guild(int(gid))
                        if guild:
                            await _chunk(guild)
                            await asyncio.to_thread(save_backup, guild)
                            bot.log(f"[protection] auto backup saved for {guild.name}")
                except Exception as e:
                    bot.log(f"[protection] backup error: {e}")

    started = {"v": False}
    original_on_ready = bot.on_ready

    async def on_ready():
        await original_on_ready()
        if not started["v"]:
            started["v"] = True
            asyncio.create_task(backup_loop())

    bot.on_ready = on_ready