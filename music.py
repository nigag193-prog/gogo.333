"""GOGO Music - YouTube music player with a control panel.

Fits the GOGO architecture: the main bot is a plain discord.Client (no commands.Bot,
no cogs), so this module exposes register(bot) which wires the /music slash command
into the existing CommandTree - same pattern as welcomecard / rolecard.

Install:
  1)  pip install yt-dlp          (ffmpeg is already required by quran.py)
  2)  put this file next to bot.py and add in ServerBot.__init__ (under the
      other register calls):
          import music
          music.register(self)
  3)  restart - slash commands are re-synced at startup by on_ready.

YouTube anti-bot support:
  - Optional cookies.txt (or YOUTUBE_COOKIES_FILE) is passed to yt-dlp.
  - Optional bgutil PO-token provider plugin can generate fresh per-video tokens.
    The provider service must be running at YOUTUBE_POT_BASE_URL (default localhost:4416).
  - These may help, but cannot guarantee access from a blocked datacenter IP.
"""
import asyncio
import os
from pathlib import Path

import discord
import yt_dlp
from dataclasses import dataclass, field
from urllib.parse import urlparse

# Optional YouTube cookies. Keep this file private; it contains session credentials.
_MODULE_DIR = Path(__file__).resolve().parent
_cookie_setting = os.getenv("YOUTUBE_COOKIES_FILE", "cookies.txt").strip()
_cookie_path = Path(_cookie_setting).expanduser()
if not _cookie_path.is_absolute():
    _cookie_path = _MODULE_DIR / _cookie_path

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    # mweb is the recommended client when using a PO-token provider.
    "extractor_args": {
        "youtube": {"player_client": ["mweb", "tv", "web_safari"]},
    },
}

if _cookie_path.is_file():
    YDL_OPTIONS["cookiefile"] = str(_cookie_path)
    print(f"[music] YouTube cookies enabled: {_cookie_path.name}")
else:
    print("[music] No YouTube cookies file found; continuing without cookies")

# Optional bgutil provider HTTP endpoint. The provider plugin uses localhost:4416
# by default, so this is only needed when it runs at another address/port.
_pot_url = os.getenv("YOUTUBE_POT_BASE_URL", "").strip()
if _pot_url:
    YDL_OPTIONS["extractor_args"].setdefault("youtubepot-bgutilhttp", {})["base_url"] = [_pot_url]

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

IDLE_DISCONNECT_SECONDS = 60     # leave the voice channel 60 s after the queue ends


@dataclass
class Track:
    title: str
    url: str
    stream: str
    duration: int
    thumbnail: str = ""


@dataclass
class GuildPlayer:
    voice: object = None
    queue: list = field(default_factory=list)
    current: object = None
    volume: float = 0.7
    message: object = None
    channel: object = None
    lock: object = field(default_factory=asyncio.Lock)
    idle_task: object = None


class Music:
    """Plain helper class (not a commands.Cog - the bot is a discord.Client)."""

    def __init__(self, bot):
        self.bot = bot
        self.players = {}

    def get_player(self, guild_id):
        if guild_id not in self.players:
            self.players[guild_id] = GuildPlayer()
        return self.players[guild_id]

    @staticmethod
    def valid_url(url):
        host = urlparse(url).hostname or ""
        return host.lower() in (
            "youtube.com", "www.youtube.com", "m.youtube.com",
            "youtu.be", "www.youtu.be", "music.youtube.com",
        )

    @staticmethod
    def extract_track(url):
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            info = ydl.extract_info(url, download=False)
        if "entries" in info:
            info = next(iter(info["entries"]), None)
        if not info:
            raise ValueError("No track found")
        return Track(
            title=info.get("title", "Unknown"),
            url=info.get("webpage_url", url),
            stream=info["url"],
            duration=info.get("duration") or 0,
            thumbnail=info.get("thumbnail", ""),
        )

    @staticmethod
    def format_time(seconds):
        minutes, seconds = divmod(int(seconds), 60)
        return f"{minutes}:{seconds:02d}"

    def create_embed(self, player):
        embed = discord.Embed(title="🎵 GOGO MUSIC",
                              color=discord.Color.from_rgb(35, 190, 125))
        if player.current:
            track = player.current
            embed.add_field(name="🎧 NOW PLAYING",
                            value=f"**[{track.title}]({track.url})**", inline=False)
            embed.add_field(name="⏱️ Duration", value=self.format_time(track.duration),
                            inline=True)
            embed.add_field(name="🔊 Volume", value=f"{int(player.volume * 100)}%",
                            inline=True)
            if track.thumbnail:
                embed.set_thumbnail(url=track.thumbnail)
        else:
            embed.description = ("🎶 No music playing.\n"
                                 "Press **Add Song** to start listening!")
        if player.queue:
            songs = "\n".join(f"`{i}.` {t.title[:65]}"
                               for i, t in enumerate(player.queue[:5], 1))
            embed.add_field(name=f"📜 UP NEXT ({len(player.queue)})",
                            value=songs, inline=False)
        embed.set_footer(text="GOGO • Music Experience")
        return embed

    async def update_panel(self, player):
        if player.message:
            try:
                await player.message.edit(embed=self.create_embed(player),
                                          view=MusicView(self, player))
            except discord.NotFound:
                player.message = None
            except discord.HTTPException:
                pass

    def _cancel_idle(self, player):
        if player.idle_task and not player.idle_task.done():
            player.idle_task.cancel()
        player.idle_task = None

    async def _idle_disconnect(self, player):
        """Leave the voice channel after the queue has been empty for a while."""
        try:
            await asyncio.sleep(IDLE_DISCONNECT_SECONDS)
            if player.voice and player.voice.is_connected() \
                    and not player.voice.is_playing() and not player.voice.is_paused() \
                    and player.current is None:
                await player.voice.disconnect()
                player.voice = None
                await self.update_panel(player)
        except asyncio.CancelledError:
            pass

    async def enqueue(self, player, url):
        if not self.valid_url(url):
            raise ValueError("Please provide a valid YouTube URL.")
        # guard: the bot may have been stopped / disconnected since the panel opened
        if player.voice is None or not player.voice.is_connected():
            raise ValueError("The music session is not connected. Open /music again.")
        track = await asyncio.to_thread(self.extract_track, url)
        self._cancel_idle(player)
        player.queue.append(track)
        if not player.voice.is_playing() and not player.voice.is_paused():
            await self.play_next(player)
        await self.update_panel(player)
        return track

    async def play_next(self, player):
        async with player.lock:
            if not player.voice or not player.voice.is_connected():
                player.current = None
                await self.update_panel(player)
                return
            if not player.queue:
                player.current = None
                await self.update_panel(player)
                self._cancel_idle(player)
                player.idle_task = asyncio.create_task(self._idle_disconnect(player))
                return
            track = player.queue.pop(0)
            player.current = track
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(track.stream, **FFMPEG_OPTIONS),
                volume=player.volume)

            def after(error):
                if error:
                    print(f"GOGO Music error: {error}")
                asyncio.run_coroutine_threadsafe(self.play_next(player), self.bot.loop)

            player.voice.play(source, after=after)
            await self.update_panel(player)

    async def open_panel(self, interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                "❌ This command only works inside a server.", ephemeral=True)
        if not interaction.user.voice:
            return await interaction.response.send_message(
                "❌ Join a Voice Channel first!", ephemeral=True)

        await interaction.response.defer()
        player = self.get_player(interaction.guild.id)
        voice_channel = interaction.user.voice.channel
        try:
            if player.voice and player.voice.is_connected():
                if player.voice.channel != voice_channel:
                    await player.voice.move_to(voice_channel)
            else:
                player.voice = await voice_channel.connect()
            player.channel = interaction.channel
            await interaction.followup.send(embed=self.create_embed(player),
                                            view=MusicView(self, player))
            player.message = await interaction.original_response()
        except Exception as error:
            await interaction.followup.send(f"❌ Music error: {error}", ephemeral=True)


class AddSongModal(discord.ui.Modal, title="🎵 Add YouTube Song"):
    url = discord.ui.TextInput(
        label="YouTube URL",
        placeholder="https://www.youtube.com/watch?v=...",
        required=True, max_length=300)

    def __init__(self, music, player):
        super().__init__()
        self.music = music
        self.player = player

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            track = await self.music.enqueue(self.player, str(self.url))
            await interaction.followup.send(
                f"✅ Added **{track.title}** to GOGO Music!", ephemeral=True)
        except Exception as error:
            await interaction.followup.send(
                f"❌ Could not play this song: {error}", ephemeral=True)


class MusicView(discord.ui.View):
    def __init__(self, music, player):
        super().__init__(timeout=1800)   # buttons live for 30 min after the last panel
        self.music = music
        self.player = player

    async def check_voice(self, interaction):
        if not interaction.user.voice:
            await interaction.response.send_message(
                "❌ Join the bot's Voice Channel first!", ephemeral=True)
            return False
        if (not self.player.voice
                or interaction.user.voice.channel.id != self.player.voice.channel.id):
            await interaction.response.send_message(
                "❌ You must be in the same Voice Channel!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Add Song", emoji="➕",
                       style=discord.ButtonStyle.success, row=0)
    async def add_song(self, interaction, button):
        if not await self.check_voice(interaction):
            return
        await interaction.response.send_modal(AddSongModal(self.music, self.player))

    @discord.ui.button(label="Pause / Resume", emoji="⏯️",
                       style=discord.ButtonStyle.primary, row=0)
    async def pause_resume(self, interaction, button):
        if not await self.check_voice(interaction):
            return
        voice = self.player.voice
        if voice.is_playing():
            voice.pause()
            status = "⏸️ Music paused."
        elif voice.is_paused():
            voice.resume()
            status = "▶️ Music resumed."
        else:
            status = "❌ Nothing is playing."
        await interaction.response.send_message(status, ephemeral=True)
        await self.music.update_panel(self.player)

    @discord.ui.button(label="Skip", emoji="⏭️",
                       style=discord.ButtonStyle.primary, row=0)
    async def skip(self, interaction, button):
        if not await self.check_voice(interaction):
            return
        if not self.player.current:
            return await interaction.response.send_message("❌ No song to skip.",
                                                           ephemeral=True)
        self.player.voice.stop()
        await interaction.response.send_message("⏭️ Skipped!", ephemeral=True)

    @discord.ui.button(label="Stop", emoji="⏹️",
                       style=discord.ButtonStyle.danger, row=0)
    async def stop(self, interaction, button):
        if not await self.check_voice(interaction):
            return
        self.music._cancel_idle(self.player)
        self.player.queue.clear()
        self.player.current = None
        if self.player.voice:
            self.player.voice.stop()
            await self.player.voice.disconnect()
        self.player.voice = None
        await interaction.response.send_message("⏹️ Music stopped and disconnected.",
                                                ephemeral=True)
        await self.music.update_panel(self.player)

    @discord.ui.button(label="Volume -", emoji="🔉",
                       style=discord.ButtonStyle.secondary, row=1)
    async def volume_down(self, interaction, button):
        if not await self.check_voice(interaction):
            return
        self.player.volume = max(0.0, self.player.volume - 0.1)
        voice = self.player.voice
        if voice.source and hasattr(voice.source, "volume"):
            voice.source.volume = self.player.volume
        await interaction.response.send_message(
            f"🔉 Volume: {int(self.player.volume * 100)}%", ephemeral=True)
        await self.music.update_panel(self.player)

    @discord.ui.button(label="Volume +", emoji="🔊",
                       style=discord.ButtonStyle.secondary, row=1)
    async def volume_up(self, interaction, button):
        if not await self.check_voice(interaction):
            return
        self.player.volume = min(1.0, self.player.volume + 0.1)
        voice = self.player.voice
        if voice.source and hasattr(voice.source, "volume"):
            voice.source.volume = self.player.volume
        await interaction.response.send_message(
            f"🔊 Volume: {int(self.player.volume * 100)}%", ephemeral=True)
        await self.music.update_panel(self.player)


def register(bot):
    """Wire /music into the existing CommandTree (called once in __init__)."""
    music = Music(bot)
    bot._music = music   # keep a reference (e.g. for later owner commands)

    @bot.tree.command(name="music", description="Open GOGO Music Player")
    async def music_cmd(interaction: discord.Interaction):
        await music.open_panel(interaction)

    if hasattr(bot, "log"):
        bot.log("[music] registered /music")
