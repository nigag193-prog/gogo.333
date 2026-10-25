"""Quran Radio: a voice channel where GOGO plays a live Quran radio 24/7, a control panel, and an ayah of the day.

  /quran setup [text] [voice]   (Manage Server) creates (or uses) the text channel + the voice channel, posts the panel and starts the radio
  /quran off                    stops the radio, leaves the voice channel and forgets the panel
  /quran daily on|off           an ayah of the day, once a day, in the text channel

The panel (in the text channel): a menu of Quran radio stations/reciters (pages), pause/resume, volume, restart.
Only people sitting in the radio's voice channel (or members with Manage Server) can control it.
Needs on the host: ffmpeg (or imageio-ffmpeg) and PyNaCl + davey in requirements (same as /stay and voice announcements).

Data: Mp3Quran (radio stations, https://mp3quran.net) and Al-Quran Cloud (the ayah of the day, https://alquran.cloud): free, no key.
.env (optional):  QURAN_MAX_STREAMS=3  (radios playing at the same time on this bot)   QURAN_TRANSLATION=en.sahih  (fr.hamidullah ...)
                  QURAN_DAILY_HOUR=8   TIMEZONE=Africa/Casablanca
Settings are saved in guild_config.json -> "<server id>": {"quran": {...}}
"""
import asyncio
import datetime
import os
import random
import shutil
import time

import discord
from discord import app_commands

HERE = os.path.dirname(os.path.abspath(__file__))
RADIOS_URL = "https://www.mp3quran.net/api/v3/radios?language=eng"
AYAH_URL = "https://api.alquran.cloud/v1/ayah/{n}/editions/quran-uthmani,{tr}"
PAGE = 25
FFMPEG_BEFORE = "-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
_radios = {"time": 0, "list": []}
_playing = set()                      # guild ids whose radio is playing right now
_locks, _last_note, _last_ctl = {}, {}, {}


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


def tz():
    name = _env("TIMEZONE", "Africa/Casablanca")
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return datetime.timezone(datetime.timedelta(hours=1), name)


def ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


# ---------------------------------------------------------------- data
def get_radios():
    """[{'name','url'}] from Mp3Quran, sorted by name, cached 12 hours (the old list is kept if the service is down)."""
    import requests
    if _radios["list"] and time.time() - _radios["time"] < 43200:
        return _radios["list"]
    try:
        r = requests.get(RADIOS_URL, headers={"User-Agent": "gogo-discord-bot"}, timeout=20)
        r.raise_for_status()
        out, seen = [], set()
        for it in r.json().get("radios", []):
            url, name = str(it.get("url") or "").strip(), str(it.get("name") or "").strip()
            if url.startswith("http") and name and url not in seen:
                seen.add(url)
                out.append({"name": name[:100], "url": url})
        if out:
            _radios.update(time=time.time(), list=sorted(out, key=lambda x: x["name"].lower()))
    except Exception:
        if not _radios["list"]:
            raise
    return _radios["list"]


def default_radio(radios):
    for key in ("mishari", "alafasy", "afasy", "quran radio", "holy quran"):
        for r in radios:
            if key in r["name"].lower():
                return r
    return radios[0]


def ayah_of_the_day(day=None):
    """-> (arabic, translation, 'Surah name 2:255') for today, the same for everybody"""
    import requests
    day = day or datetime.datetime.now(tz()).date()
    n = random.Random(day.toordinal() * 7919).randint(1, 6236)
    tr = _env("QURAN_TRANSLATION", "en.sahih")
    r = requests.get(AYAH_URL.format(n=n, tr=tr), headers={"User-Agent": "gogo-discord-bot"}, timeout=20)
    r.raise_for_status()
    ar, en = r.json()["data"][0], r.json()["data"][1]
    ref = f"{ar['surah']['englishName']} ({ar['surah']['number']}:{ar['numberInSurah']})"
    return ar["text"], en["text"], ref


# ---------------------------------------------------------------- settings
def qcfg(bot, guild):
    return (bot.config.get(str(guild.id)) or {}).get("quran")


def max_streams():
    raw = _env("QURAN_MAX_STREAMS", "3")
    return int(raw) if raw.isdigit() else 3


def status_text(bot, guild):
    c = qcfg(bot, guild) or {}
    if c.get("paused"):
        return "⏸️ Paused"
    return "🔴 **LIVE**" if guild.id in _playing else "⚠️ Reconnecting..."


def panel_embed(bot, guild):
    c = qcfg(bot, guild) or {}
    vol = round(float(c.get("volume", 0.6)) * 100)
    bar = "█" * (vol // 10) + "░" * (10 - vol // 10)
    emb = discord.Embed(title="📻  Quran Radio", color=0x0B6E4F,
                        description="Join the voice channel to listen. Choose a station in the menu below.")
    emb.add_field(name="Now playing", value=f"**{c.get('radio_name', '—')}**", inline=False)
    emb.add_field(name="Status", value=status_text(bot, guild), inline=True)
    emb.add_field(name="Volume", value=f"`{bar}` {vol}%", inline=True)
    if c.get("voice_id"):
        emb.add_field(name="Listen in", value=f"<#{c['voice_id']}>", inline=True)
    emb.set_footer(text="Stations by Mp3Quran.net • only listeners in the voice channel can control it")
    return emb


# ---------------------------------------------------------------- playback
async def ensure_playing(bot, guild):
    """Makes sure the bot sits in the radio channel and plays. Returns None if all is well, else a short reason."""
    c = qcfg(bot, guild)
    if not c or not c.get("enabled", True):
        return None
    vch = guild.get_channel(int(c.get("voice_id") or 0))
    if not isinstance(vch, (discord.VoiceChannel, discord.StageChannel)):
        return "no voice channel"
    stay = (bot.config.get(str(guild.id)) or {}).get("stay_voice")
    if stay and int(stay.get("channel_id") or 0) != vch.id:
        return "stay elsewhere"                                     # /stay keeps the bot in another channel: do not fight it
    try:
        import voice247
        if guild.id in voice247.PAUSED:
            return None                                              # a voice announcement is running
    except ImportError:
        pass
    lock = _locks.setdefault(guild.id, asyncio.Lock())
    if lock.locked():
        return None
    async with lock:
        vc = guild.voice_client
        if c.get("paused"):
            if vc and vc.is_playing():
                vc.stop()
            _playing.discard(guild.id)
            return None
        try:
            if vc and vc.is_connected():
                if vc.channel.id != vch.id:
                    await vc.move_to(vch)
            else:
                if len(_playing - {guild.id}) >= max_streams():
                    return "busy"
                if vc:
                    await vc.disconnect(force=True)
                perms = vch.permissions_for(guild.me)
                if not (perms.view_channel and perms.connect):
                    return "I need View Channel + Connect on the radio channel"
                vc = await vch.connect(self_deaf=True, timeout=30)
            if vc.is_playing() or vc.is_paused():
                _playing.add(guild.id)
                return None
            exe = ffmpeg()
            if not exe:
                return "ffmpeg is missing (add imageio-ffmpeg to requirements)"
            src = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(c["radio_url"], executable=exe, before_options=FFMPEG_BEFORE, options="-vn"),
                volume=float(c.get("volume", 0.6)))
            loop = asyncio.get_running_loop()

            def after(err):
                _playing.discard(guild.id)
                asyncio.run_coroutine_threadsafe(_restart_later(bot, guild.id), loop)

            vc.play(src, after=after)
            _playing.add(guild.id)
            return None
        except Exception as e:
            return f"{type(e).__name__}: {e}"


async def _restart_later(bot, gid, delay=5):
    await asyncio.sleep(delay)
    guild = bot.get_guild(gid)
    if guild is not None:
        await _note(bot, guild, await ensure_playing(bot, guild))


async def _note(bot, guild, reason):
    """Log a problem once, not every 20 seconds."""
    if reason and _last_note.get(guild.id) != reason:
        _last_note[guild.id] = reason
        bot.log(f"[quran] {guild.name}: {reason}")
    elif not reason:
        _last_note.pop(guild.id, None)


async def refresh_panel(bot, guild):
    c = qcfg(bot, guild) or {}
    ch = guild.get_channel(int(c.get("text_id") or 0))
    if ch is None or not c.get("message_id"):
        return
    try:
        await asyncio.to_thread(get_radios)                              # warm the cache so the menu keeps its stations
    except Exception:
        return
    try:
        msg = await ch.fetch_message(int(c["message_id"]))
        await msg.edit(embed=panel_embed(bot, guild), view=PanelView(bot, int(c.get("page", 0))))
    except discord.HTTPException:
        pass


async def watchdog(bot):
    await bot.wait_until_ready()
    tick = 0
    while not bot.is_closed():
        for gid, cfg in list(bot.config.items()):
            if not isinstance(cfg, dict) or not cfg.get("quran"):
                continue
            guild = bot.get_guild(int(gid))
            if guild is None:
                continue
            try:
                await _note(bot, guild, await ensure_playing(bot, guild))
                if tick % 15 == 0:                                   # about every 5 minutes
                    await refresh_panel(bot, guild)
            except Exception as e:
                bot.log(f"[quran] {type(e).__name__}: {e}")
        tick += 1
        await asyncio.sleep(20)


async def daily_loop(bot):
    await bot.wait_until_ready()
    raw = _env("QURAN_DAILY_HOUR", "8")
    hour = int(raw) if raw.isdigit() and 0 <= int(raw) <= 23 else 8
    while not bot.is_closed():
        now = datetime.datetime.now(tz())
        for gid, cfg in list(bot.config.items()):
            c = cfg.get("quran") if isinstance(cfg, dict) else None
            if not c or not c.get("daily") or c.get("last_daily") == now.date().isoformat() or now.hour < hour:
                continue
            guild = bot.get_guild(int(gid))
            ch = guild.get_channel(int(c.get("text_id") or 0)) if guild else None
            if ch is None:
                continue
            try:
                ar, tr, ref = await asyncio.to_thread(ayah_of_the_day, now.date())
                emb = discord.Embed(title="📖  Ayah of the day", color=0x0B6E4F, description=f"## {ar}\n\n*{tr}*")
                emb.set_footer(text=f"{ref} • Al-Quran Cloud")
                await ch.send(embed=emb)
                c["last_daily"] = now.date().isoformat()
                bot.persist()
            except Exception as e:
                bot.log(f"[quran] daily ayah: {type(e).__name__}: {e}")
        await asyncio.sleep(300)


# ---------------------------------------------------------------- panel
def can_control(interaction, c):
    u = interaction.user
    if getattr(u.guild_permissions, "manage_guild", False):
        return True
    vs = getattr(u, "voice", None)
    return bool(vs and vs.channel and vs.channel.id == int(c.get("voice_id") or 0))


async def _guard(bot, interaction):
    c = qcfg(bot, interaction.guild)
    if not c:
        await interaction.response.send_message("The radio is not set up here.", ephemeral=True)
        return None
    if not can_control(interaction, c):
        await interaction.response.send_message("Join the **Quran Radio** voice channel to control it.", ephemeral=True)
        return None
    if time.time() - _last_ctl.get(interaction.guild_id, 0) < 3:
        await interaction.response.send_message("One moment please...", ephemeral=True)
        return None
    _last_ctl[interaction.guild_id] = time.time()
    return c


class RadioSelect(discord.ui.Select):
    def __init__(self, bot, page=0):
        self.bot = bot
        radios = _radios["list"][page * PAGE:(page + 1) * PAGE]          # from the cache: never wait for the network here
        opts = [discord.SelectOption(label=r["name"], value=str(page * PAGE + i)) for i, r in enumerate(radios)] or \
               [discord.SelectOption(label="(stations)", value="0")]
        super().__init__(custom_id="gogo:qr:pick", placeholder="📻 Choose a station / reciter", min_values=1, max_values=1, options=opts)

    async def callback(self, interaction):
        c = await _guard(self.bot, interaction)
        if c is None:
            return
        try:
            radios = await asyncio.to_thread(get_radios)
            radio = radios[int(self.values[0])]
        except Exception:
            await interaction.response.send_message("Could not load the station list. Try again in a minute.", ephemeral=True)
            return
        c.update(radio_name=radio["name"], radio_url=radio["url"], paused=False)
        self.bot.persist()
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
        await interaction.response.send_message(f"📻 Switching to **{radio['name']}**...", ephemeral=True)
        await asyncio.sleep(1)
        await _note(self.bot, interaction.guild, await ensure_playing(self.bot, interaction.guild))
        await refresh_panel(self.bot, interaction.guild)


class PanelView(discord.ui.View):
    def __init__(self, bot, page=0):
        super().__init__(timeout=None)
        self.bot = bot
        self.add_item(RadioSelect(bot, page))
        for label, emoji, cid, cb in (("", "◀", "gogo:qr:prev", self.on_prev), ("", "▶", "gogo:qr:next", self.on_next),
                                      ("Pause / Resume", "⏯", "gogo:qr:pause", self.on_pause), ("", "🔉", "gogo:qr:vdown", self.on_vdown),
                                      ("", "🔊", "gogo:qr:vup", self.on_vup), ("Restart", "🔄", "gogo:qr:restart", self.on_restart)):
            b = discord.ui.Button(label=label or None, emoji=emoji, style=discord.ButtonStyle.secondary, custom_id=cid)
            b.callback = cb
            self.add_item(b)

    async def _page(self, interaction, delta):
        c = await _guard(self.bot, interaction)
        if c is None:
            return
        try:
            total = len(await asyncio.to_thread(get_radios))
        except Exception:
            total = PAGE
        pages = max(1, (total + PAGE - 1) // PAGE)
        c["page"] = (int(c.get("page", 0)) + delta) % pages
        self.bot.persist()
        await interaction.response.edit_message(embed=panel_embed(self.bot, interaction.guild), view=PanelView(self.bot, c["page"]))

    async def on_prev(self, interaction):
        await self._page(interaction, -1)

    async def on_next(self, interaction):
        await self._page(interaction, +1)

    async def on_pause(self, interaction):
        c = await _guard(self.bot, interaction)
        if c is None:
            return
        c["paused"] = not c.get("paused", False)
        self.bot.persist()
        vc = interaction.guild.voice_client
        if c["paused"] and vc and vc.is_playing():
            vc.stop()
        await interaction.response.defer()
        if not c["paused"]:
            await _note(self.bot, interaction.guild, await ensure_playing(self.bot, interaction.guild))
        await refresh_panel(self.bot, interaction.guild)

    async def _volume(self, interaction, delta):
        c = await _guard(self.bot, interaction)
        if c is None:
            return
        c["volume"] = round(min(1.0, max(0.1, float(c.get("volume", 0.6)) + delta)), 2)
        self.bot.persist()
        vc = interaction.guild.voice_client
        if vc and isinstance(getattr(vc, "source", None), discord.PCMVolumeTransformer):
            vc.source.volume = c["volume"]
        await interaction.response.edit_message(embed=panel_embed(self.bot, interaction.guild))

    async def on_vdown(self, interaction):
        await self._volume(interaction, -0.1)

    async def on_vup(self, interaction):
        await self._volume(interaction, +0.1)

    async def on_restart(self, interaction):
        c = await _guard(self.bot, interaction)
        if c is None:
            return
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
        await interaction.response.defer()
        await asyncio.sleep(1)
        await _note(self.bot, interaction.guild, await ensure_playing(self.bot, interaction.guild))
        await refresh_panel(self.bot, interaction.guild)


# ---------------------------------------------------------------- install / commands
async def install(bot, guild, text_ch, voice_ch):
    """Save the settings, post the panel and start the radio. Returns a short note for the report."""
    radios = await asyncio.to_thread(get_radios)
    first = default_radio(radios)
    cfg = bot.config.setdefault(str(guild.id), {})
    old = cfg.get("quran") or {}
    cfg["quran"] = {**old, "text_id": text_ch.id, "voice_id": voice_ch.id, "radio_name": old.get("radio_name") or first["name"],
                    "radio_url": old.get("radio_url") or first["url"], "volume": old.get("volume", 0.6), "paused": False,
                    "page": 0, "daily": old.get("daily", True), "enabled": True}
    if not (cfg.get("stay_voice") or {}).get("channel_id"):          # the radio channel becomes the bot's home channel
        cfg["stay_voice"] = {"channel_id": voice_ch.id}
    msg = await text_ch.send(embed=panel_embed(bot, guild), view=PanelView(bot, 0))
    cfg["quran"]["message_id"] = msg.id
    bot.persist()
    reason = await ensure_playing(bot, guild)
    await _note(bot, guild, reason)
    await refresh_panel(bot, guild)
    return None if reason is None else reason


def register(bot):
    group = app_commands.Group(name="quran", description="Quran radio", guild_only=True)

    @group.command(name="setup", description="(Admin) Create the Quran radio: voice channel, panel and ayah of the day")
    @app_commands.describe(text="Text channel for the panel (empty = I create one)", voice="Voice channel for the radio (empty = I create one)")
    async def setup(inter: discord.Interaction, text: discord.TextChannel = None, voice: discord.VoiceChannel = None):
        if not inter.user.guild_permissions.manage_guild:
            await inter.response.send_message("You need the Manage Server permission.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        g = inter.guild
        try:
            if text is None or voice is None:
                cat = discord.utils.get(g.categories, name="📖 QURAN RADIO") or await g.create_category("📖 QURAN RADIO", reason="GOGO Quran radio")
                text = text or await g.create_text_channel("📖・quran", category=cat, reason="GOGO Quran radio")
                voice = voice or await g.create_voice_channel("🕋・Quran Radio", category=cat, reason="GOGO Quran radio")
            note = await install(bot, g, text, voice)
        except discord.HTTPException as e:
            await inter.followup.send(f"❌ Discord refused: {e}. Give me Manage Channels / Connect.", ephemeral=True)
            return
        except Exception as e:
            await inter.followup.send(f"❌ Could not start the radio ({type(e).__name__}). Is Mp3Quran reachable from the host?", ephemeral=True)
            return
        await inter.followup.send(f"✅ Quran radio ready: {voice.mention} · panel in {text.mention}"
                                  + (f"\n⚠️ The radio could not start yet: {note}" if note else ""), ephemeral=True)

    @group.command(name="off", description="(Admin) Stop the Quran radio and leave the voice channel")
    async def off(inter: discord.Interaction):
        if not inter.user.guild_permissions.manage_guild:
            await inter.response.send_message("You need the Manage Server permission.", ephemeral=True)
            return
        cfg = bot.config.get(str(inter.guild_id)) or {}
        c = cfg.pop("quran", None)
        if c and (cfg.get("stay_voice") or {}).get("channel_id") == c.get("voice_id"):
            cfg.pop("stay_voice", None)
        bot.persist()
        vc = inter.guild.voice_client
        if vc:
            await vc.disconnect(force=True)
        _playing.discard(inter.guild_id)
        await inter.response.send_message("📻 The radio is off. You can delete the channels.", ephemeral=True)

    @group.command(name="daily", description="(Admin) Turn the ayah of the day on or off")
    @app_commands.describe(enabled="True = post an ayah every morning")
    async def daily(inter: discord.Interaction, enabled: bool):
        if not inter.user.guild_permissions.manage_guild:
            await inter.response.send_message("You need the Manage Server permission.", ephemeral=True)
            return
        c = qcfg(bot, inter.guild)
        if not c:
            await inter.response.send_message("Run `/quran setup` first.", ephemeral=True)
            return
        c["daily"] = bool(enabled)
        bot.persist()
        await inter.response.send_message("✅ Ayah of the day: " + ("on" if enabled else "off"), ephemeral=True)

    bot.tree.add_command(group)
    started = {"v": False}
    original_on_ready = bot.on_ready

    async def on_ready():
        await original_on_ready()
        if not started["v"]:
            started["v"] = True
            bot.add_view(PanelView(bot, 0))
            asyncio.create_task(watchdog(bot))
            asyncio.create_task(daily_loop(bot))
            try:                                           # a voice announcement must stop the radio first, it comes back by itself
                import automation
                original = automation.announce

                async def announce(bot_, g, channel, *a, **k):
                    vc = g.voice_client
                    if vc and vc.is_playing():
                        vc.stop()
                    return await original(bot_, g, channel, *a, **k)

                automation.announce = announce
            except ImportError:
                pass

    bot.on_ready = on_ready
