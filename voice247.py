"""Stay-in-voice: the bot joins a voice channel and STAYS there (24/7).

  /stay [channel]   OWNER ONLY - the bot joins that voice channel (empty = the one you are in now)
                    and never leaves: if someone disconnects it, kicks it or drags it to another
                    channel, it comes back by itself. It also rejoins after a bot restart.
  /unstay           OWNER ONLY - the bot leaves and stops staying.

"Owner" = the server owner (+ the ids in GOGO_ADMIN_IDS, same rule as owner_ai.py).
Needs:  PyNaCl + davey in requirements-bot.txt, and the bot needs View Channel + Connect on that channel.
Saved per server in guild_config.json -> "<server id>": {"stay_voice": {"channel_id": 123}}
"""
import asyncio
from typing import Optional, Union

import discord
from discord import app_commands

try:
    import owner_ai
except ImportError:                       # owner_ai.py is optional
    owner_ai = None

CHECK_SECONDS = 30                        # watchdog: how often it checks that the bot is still in place
_locks = {}
PAUSED = set()                      # guild ids where the bot must NOT be pulled back (voice announcement running)
_last_err = {}


def can_use(guild, user):
    if owner_ai is not None:
        try:
            return owner_ai.is_owner(guild, user)
        except Exception:
            pass
    return user.id == guild.owner_id


def _target(bot, gid):
    cfg = bot.config.get(str(gid))
    st = cfg.get("stay_voice") if isinstance(cfg, dict) else None
    if not st or not st.get("channel_id"):
        return None, None
    guild = bot.get_guild(int(gid))
    ch = guild.get_channel(int(st["channel_id"])) if guild else None
    if not isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
        return guild, None
    return guild, ch


async def ensure(bot, gid):
    """Make sure the bot sits in the saved voice channel. Returns None if OK, else an error text."""
    if int(gid) in PAUSED:
        return None
    guild, ch = _target(bot, gid)
    if guild is None or ch is None:
        return None
    lock = _locks.setdefault(str(gid), asyncio.Lock())
    if lock.locked():
        return None
    async with lock:
        try:
            vc = guild.voice_client
            if vc and vc.is_connected():
                if vc.channel and vc.channel.id != ch.id:
                    await vc.move_to(ch)                      # someone moved the bot: go back
                _last_err.pop(str(gid), None)
                return None
            if vc:                                            # stale connection: clean it first
                await vc.disconnect(force=True)
            perms = ch.permissions_for(guild.me)
            if not (perms.view_channel and perms.connect):
                raise RuntimeError("I need View Channel + Connect on that voice channel")
            await ch.connect(self_deaf=True, timeout=30)
            _last_err.pop(str(gid), None)
            bot.log(f"[voice247] joined {ch.name} in {guild.name}")
            return None
        except Exception as e:
            msg = str(e) or e.__class__.__name__
            if _last_err.get(str(gid)) != msg:                # do not repeat the same error every 30 s
                _last_err[str(gid)] = msg
                bot.log(f"[voice247] cannot stay in voice in {guild.name}: {msg}")
            return msg


async def watchdog(bot):
    await bot.wait_until_ready()
    while not bot.is_closed():
        for gid in list(bot.config.keys()):
            try:
                await ensure(bot, gid)
            except Exception as e:
                bot.log(f"[voice247] error: {e}")
        await asyncio.sleep(CHECK_SECONDS)


async def _rejoin_soon(bot, gid):
    await asyncio.sleep(3)
    await ensure(bot, gid)


def register(bot):
    """Adds /stay and /unstay and starts the watchdog (call once, before bot.run)."""

    @bot.tree.command(name="stay", description="Owner only: the bot joins a voice channel and stays there forever")
    @app_commands.describe(channel="Voice channel (leave empty = the one you are in now)")
    @app_commands.guild_only()
    async def stay_cmd(inter: discord.Interaction,
                       channel: Optional[Union[discord.VoiceChannel, discord.StageChannel]] = None):
        if not can_use(inter.guild, inter.user):
            await inter.response.send_message("Only the server owner can use this command.", ephemeral=True)
            return
        if channel is None:
            vs = getattr(inter.user, "voice", None)
            channel = vs.channel if vs else None
        if channel is None:
            await inter.response.send_message("Join a voice channel first, or choose one in the command.",
                                              ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        cfg = bot.config.setdefault(str(inter.guild_id), {})
        cfg["stay_voice"] = {"channel_id": channel.id}
        bot.persist()
        err = await ensure(bot, inter.guild_id)
        if err:
            await inter.followup.send(f"❌ I could not join {channel.mention}: {err}\n"
                                      "Voice needs `PyNaCl` and `davey` in requirements-bot.txt.", ephemeral=True)
        else:
            await inter.followup.send(f"🔊 I joined {channel.mention} and I will stay there. "
                                      "Use /unstay to make me leave.", ephemeral=True)

    @bot.tree.command(name="unstay", description="Owner only: the bot leaves the voice channel and stops staying")
    @app_commands.guild_only()
    async def unstay_cmd(inter: discord.Interaction):
        if not can_use(inter.guild, inter.user):
            await inter.response.send_message("Only the server owner can use this command.", ephemeral=True)
            return
        cfg = bot.config.setdefault(str(inter.guild_id), {})
        cfg.pop("stay_voice", None)
        bot.persist()
        vc = inter.guild.voice_client
        if vc:
            await vc.disconnect(force=True)
        await inter.response.send_message("👋 I left the voice channel and I will not come back.", ephemeral=True)

    # --- come back immediately when the bot is disconnected / moved ---------------------
    original_vsu = getattr(bot, "on_voice_state_update", None)

    async def on_voice_state_update(member, before, after):
        try:
            if original_vsu:
                await original_vsu(member, before, after)
        finally:
            if bot.user and member.id == bot.user.id and before.channel != after.channel:
                asyncio.create_task(_rejoin_soon(bot, member.guild.id))

    bot.on_voice_state_update = on_voice_state_update

    # --- start the watchdog when the bot is ready (also rejoins after a restart) --------
    started = {"v": False}
    original_on_ready = bot.on_ready

    async def on_ready():
        await original_on_ready()
        if not started["v"]:
            started["v"] = True
            asyncio.create_task(watchdog(bot))

    bot.on_ready = on_ready