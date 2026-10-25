"""
SpamShield - حماية ضد سبام الإنفايتات والرسائل لـ GOGO.

القاعدة: أي عضو كيسبام = يتعاقب. معفى غير:
  - Owner الحقيقي ديال السيرفر (guild.owner_id - بحال owner_ai.py)
  - البوتات (message.author.bot)

التصرفات اللي كيعاقب:
  * invite ديال سيرفر آخر          -> عقاب فوري
  * >max_messages رسالة فـ burst     -> عقاب
  * رسائل فـ >max_channels قنوات     -> عقاب

العقاب (فـ هاد الترتيب):
  1. مسح جميع رسائلو (bulk delete, آخر delete_back_hours)
  2. نحي جميع الرولات          -> مايبقاش عندو حتى تصريح
  3. Timeout 28 يوم            -> may9dar yktab f ta blasa
  4. Kick + DM توضيح
  5. تسجيل فـ logs

شغالة أوتوماتيك من start ديال البوت (enabled=True افتراضياً).

الإعدادات (guild_config.json -> ["spamshield"]):
  /spamshield on | off
  /spamshield status
  /spamshield action kick | mute
  /spamshield scope everyone | newmembers
  /spamshield set window_minutes max_messages max_channels

Integration فـ bot.py (3 زيادات):
  1.  import antispam_guard            (مع باقي الـ imports)
  2.  فـ __init__ (جنب extras.register(self)):
          antispam_guard.register(self)
  3.  فـ on_message، مباشرة من بعد:
          if await self.check_antispam(message):
              return
      زيد:
          if await antispam_guard.on_message(self, message):
              return
"""

import datetime
import re
import time
from collections import defaultdict, deque

import discord
from discord import app_commands

# ---------------- config ----------------

DEFAULTS = {
    "enabled": True,             # شغالة من أول start - ماعندك علاش تدير on
    "new_members_only": False,   # False = كولشي يتعاقب (غير Owner معفى)
    "window_minutes": 60,        # إلا خليتي scope=newmembers: شحال يعتبر "جدید"
    "max_messages": 6,           # أكثر من هاد العدد...
    "burst_seconds": 12,         # ...فـ هاد المدة = spam
    "max_channels": 3,           # رسائل فـ أكثر من هاد العدد...
    "channel_window_seconds": 60,  # ...من القنوات فـ هاد المدة = spam
    "instant_on_invite": True,   # invite ديال سيرفر آخر = عقاب فوري
    "action": "kick",            # "kick" ولا "mute" (mute = رولات محيودة + timeout)
    "delete_back_hours": 2,      # كيمسح رسائلو ديال آخر شحال ساعة
    "mute_minutes": 43200,       # 28 يوم (فـ حالة action="mute")
}

INVITE_RE = re.compile(r"(?:discord\.gg/|discord(?:app)?\.com/invite/)(\w+)", re.I)

# (guild_id, user_id) -> {"msgs": deque(timestamps), "chans": deque((ts, channel_id))}
_tracker = defaultdict(lambda: {"msgs": deque(), "chans": deque()})
_invite_cache = {}          # code -> (timestamp, is_own_guild_invite_bool)


def get_cfg(client, guild):
    cfg = client.gcfg(guild).get("spamshield") or {}
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in cfg.items() if k in DEFAULTS})
    return merged


def _save(client, guild, patch):
    cfg = client.gcfg(guild)
    cfg["spamshield"] = {**DEFAULTS, **(cfg.get("spamshield") or {}), **patch}
    client.config[str(guild.id)] = cfg
    import bot  # نفس الفولدر - فيه save_config
    bot.save_config(client.config)


# ---------------- invite check ----------------

async def _is_own_invite(client, code, guild):
    """True إلا كان الinvite ديال نفس السيرفر (ولا ما قدرناش نتحققو -> نعتبرو خارجي)."""
    now = time.time()
    hit = _invite_cache.get(code)
    if hit and now - hit[0] < 600:
        return hit[1]
    own = False
    try:
        inv = await client.fetch_invite(code)
        own = bool(inv.guild) and inv.guild.id == guild.id
    except discord.HTTPException:
        own = False
    _invite_cache[code] = (now, own)
    if len(_invite_cache) > 500:
        for k in [k for k, v in _invite_cache.items() if now - v[0] > 600]:
            _invite_cache.pop(k, None)
    return own


# ---------------- punishment ----------------

async def _purge_messages(member, hours):
    """كيمسح رسائل العضو فـ جميع قنوات السيرفر (الحديثة داخل 14 يوم)."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    deleted = 0
    for channel in member.guild.text_channels:
        try:
            msgs = []
            async for m in channel.history(limit=200, oldest_first=False):
                if m.author.id != member.id:
                    continue
                if m.created_at < cutoff:
                    break
                msgs.append(m)
                if len(msgs) >= 100:
                    break
            for chunk in (msgs[i:i + 100] for i in range(0, len(msgs), 100)):
                await channel.delete_messages(chunk)
                deleted += len(chunk)
        except (discord.HTTPException, discord.Forbidden, discord.NotFound):
            continue
    return deleted


async def punish(client, member, reason):
    """العقاب الكامل: مسح + نحي الرولات + timeout + kick. غير Owner ما كيتعاقب."""
    if member.id == member.guild.owner_id:
        return  # Owner معفى - ديما

    cfg = get_cfg(client, member.guild)
    guild = member.guild

    try:
        member = await guild.fetch_member(member.id)
    except (discord.NotFound, discord.HTTPException):
        return  # خرج من السيرفر بوحدو

    deleted = await _purge_messages(member, cfg["delete_back_hours"])

    # نحي جميع الرولات -> مايبقاش عندو حتى تصريح فـ حتى قناة
    try:
        await member.edit(roles=[], reason=f"SpamShield: {reason}")
    except discord.HTTPException:
        pass

    # DM توضيح
    try:
        await member.send(
            f"⛔ You were removed from **{guild.name}** automatically by SpamShield.\n"
            f"Reason: **{reason}**.\n"
            f"If this is a mistake, contact the server owner.")
    except discord.HTTPException:
        pass

    # timeout + kick (ولا mute غير)
    if cfg["action"] == "mute":
        try:
            await member.timeout(
                datetime.timedelta(minutes=cfg["mute_minutes"]),
                reason=f"SpamShield: {reason}")
        except discord.HTTPException:
            pass
        log_msg = (f"🛡️ SpamShield **{reason}** (mute): {member.mention} "
                   f"(`{member}`) - all roles removed, {deleted} messages purged.")
    else:
        try:
            await member.kick(reason=f"SpamShield: {reason}")
        except discord.HTTPException:
            pass
        log_msg = (f"🛡️ SpamShield **{reason}** (kick): {member.mention} "
                   f"(`{member}`) - all roles removed, {deleted} messages purged.")

    await client.guild_log(guild, log_msg)

    # ping staff فـ mod logs إلا كانت مختلفة على اللوق العام
    logs = client.gcfg(guild).get("logs") or {}
    mod_ch = guild.get_channel((logs.get("channels") or {}).get("mod") or 0)
    if mod_ch and mod_ch.id != (logs.get("channel_id") or 0):
        try:
            await mod_ch.send(log_msg)
        except discord.HTTPException:
            pass

    _tracker.pop((guild.id, member.id), None)


# ---------------- main check (called from bot.py on_message) ----------------

async def on_message(client, message):
    if message.author.bot or not message.guild or not isinstance(message.author, discord.Member):
        return False
    guild = message.guild
    member = message.author

    # ⛔ غير Owner الحقيقي معفى - كولشي آخر (حتى admin/manage_messages) يتعاقب
    if member.id == guild.owner_id:
        return False

    if getattr(client, "building", None) and guild.id in client.building:
        return False  # أثناء بناء السيرفر بالـ builder
    cfg = get_cfg(client, guild)
    if not cfg["enabled"]:
        return False
    if client.external(guild, "spamshield"):
        return False

    # واش غير الجداد؟ (افتراضياً: كولشي)
    if cfg["new_members_only"]:
        if not member.joined_at:
            return False
        age = (datetime.datetime.now(datetime.timezone.utc) - member.joined_at).total_seconds()
        if age > cfg["window_minutes"] * 60:
            _tracker.pop((guild.id, member.id), None)
            return False

    reason = None

    # 1) invite ديال سيرفر آخر -> عقاب فوري
    if cfg["instant_on_invite"]:
        for code in INVITE_RE.findall(message.content):
            if not await _is_own_invite(client, code, guild):
                reason = "invite link"
                break

    # 2) بزاف ديال الرسائل فـ وقت قصير
    now = time.time()
    tr = _tracker[(guild.id, member.id)]
    if not reason:
        q = tr["msgs"]
        q.append(now)
        while q and now - q[0] > cfg["burst_seconds"]:
            q.popleft()
        if len(q) > cfg["max_messages"]:
            reason = "message burst"
            q.clear()

    # 3) سبام فـ بزاف ديال القنوات
    if not reason:
        cq = tr["chans"]
        cq.append((now, message.channel.id))
        while cq and now - cq[0][0] > cfg["channel_window_seconds"]:
            cq.popleft()
        if len({cid for _, cid in cq}) > cfg["max_channels"]:
            reason = "spam across channels"
            cq.clear()

    if not reason:
        return False

    # كيمسح الرسالة اللي كانت سباب
    try:
        await message.delete()
    except discord.HTTPException:
        pass
    try:
        await message.channel.send(
            f"{member.mention} removed automatically ({reason}).",
            delete_after=6)
    except discord.HTTPException:
        pass

    await punish(client, member, reason)
    return True


# ---------------- slash commands ----------------

def register(bot):
    group = app_commands.Group(name="spamshield", description="Anti invite/link/flood protection")

    @group.command(name="on", description="Turn SpamShield ON")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def shield_on(inter: discord.Interaction):
        _save(bot, inter.guild, {"enabled": True})
        await inter.response.send_message("✅ SpamShield is now **ON**.", ephemeral=True)
        await bot.guild_log(inter.guild, f"🛡️ SpamShield enabled by {inter.user}")

    @group.command(name="off", description="Turn SpamShield OFF")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def shield_off(inter: discord.Interaction):
        _save(bot, inter.guild, {"enabled": False})
        await inter.response.send_message("⛔ SpamShield is now **OFF**.", ephemeral=True)
        await bot.guild_log(inter.guild, f"🛡️ SpamShield disabled by {inter.user}")

    @group.command(name="status", description="Show SpamShield settings")
    async def shield_status(inter: discord.Interaction):
        cfg = get_cfg(bot, inter.guild)
        emb = discord.Embed(title="🛡️ SpamShield status", color=0x7C3AED)
        emb.add_field(name="Enabled", value="✅ ON" if cfg["enabled"] else "⛔ OFF")
        emb.add_field(name="Scope", value="new members only" if cfg["new_members_only"] else "everyone")
        emb.add_field(name="New-member window", value=f"{cfg['window_minutes']} min")
        emb.add_field(name="Message burst", value=f">{cfg['max_messages']} msgs / {cfg['burst_seconds']}s")
        emb.add_field(name="Cross-channel", value=f">{cfg['max_channels']} channels / {cfg['channel_window_seconds']}s")
        emb.add_field(name="Instant on invite", value="✅" if cfg["instant_on_invite"] else "❌")
        emb.add_field(name="Action", value=cfg["action"].upper())
        emb.add_field(name="Exempt", value="server owner only")
        emb.add_field(name="Purge depth", value=f"last {cfg['delete_back_hours']}h")
        await inter.response.send_message(embed=emb, ephemeral=True)

    @group.command(name="action", description="Set punishment: kick or mute")
    @app_commands.describe(mode="kick = remove+kick | mute = strip roles + 28d timeout")
    @app_commands.choices(mode=[
        app_commands.Choice(name="kick (remove + kick)", value="kick"),
        app_commands.Choice(name="mute (strip roles + 28d timeout)", value="mute")])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def shield_action(inter: discord.Interaction, mode: app_commands.Choice[str]):
        _save(bot, inter.guild, {"action": mode.value})
        await inter.response.send_message(
            f"✅ SpamShield action set to **{mode.value.upper()}**.", ephemeral=True)

    @group.command(name="scope", description="Punish everyone, or new members only")
    @app_commands.describe(mode="everyone = any member | newmembers = recent joins only")
    @app_commands.choices(mode=[
        app_commands.Choice(name="everyone (any member)", value="everyone"),
        app_commands.Choice(name="newmembers (recent joins only)", value="newmembers")])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def shield_scope(inter: discord.Interaction, mode: app_commands.Choice[str]):
        _save(bot, inter.guild, {"new_members_only": mode.value == "newmembers"})
        label = "new members only" if mode.value == "newmembers" else "EVERYONE"
        await inter.response.send_message(f"✅ SpamShield scope: **{label}**.", ephemeral=True)

    @group.command(name="set", description="Tune SpamShield thresholds")
    @app_commands.describe(
        window_minutes="Count as 'new' for this long (default 60)",
        max_messages="Messages allowed in the burst window (default 6)",
        max_channels="Channels allowed in the cross-channel window (default 3)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def shield_set(inter: discord.Interaction,
                         window_minutes: app_commands.Range[int, 5, 10080] = None,
                         max_messages: app_commands.Range[int, 2, 50] = None,
                         max_channels: app_commands.Range[int, 2, 20] = None):
        patch = {}
        if window_minutes is not None:
            patch["window_minutes"] = window_minutes
        if max_messages is not None:
            patch["max_messages"] = max_messages
        if max_channels is not None:
            patch["max_channels"] = max_channels
        if not patch:
            await inter.response.send_message("⚠️ Give at least one value to change.", ephemeral=True)
            return
        _save(bot, inter.guild, patch)
        cfg = get_cfg(bot, inter.guild)
        await inter.response.send_message(
            f"✅ SpamShield updated: window **{cfg['window_minutes']}m**, "
            f"burst **{cfg['max_messages']} msgs**, "
            f"channels **{cfg['max_channels']}**.", ephemeral=True)

    bot.tree.add_command(group)
