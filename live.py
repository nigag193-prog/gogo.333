"""Live-stream alerts. Members add their OWN channel with  /live add  and the bot posts when they go live.

  /live setup #channel [ping_role]   (Manage Server)  where alerts are posted
  /live add platform name            any member (Twitch or Kick; name or link)
  /live remove platform name         the owner of the entry, or Manage Server
  /live list
  /golive platform link [title]      manual alert for Facebook / YouTube / TikTok / Instagram
                                     (these platforms cannot be detected automatically)

Automatic detection needs free developer keys in .env:
    TWITCH_CLIENT_ID=...   TWITCH_CLIENT_SECRET=...      (dev.twitch.tv/console)
    KICK_CLIENT_ID=...     KICK_CLIENT_SECRET=...        (kick.com/settings/developer)
Optional:  LIVE_MINUTES=3   LIVE_MAX_PER_USER=0 (0 = unlimited)   LIVE_MAX_PER_SERVER=0 (0 = unlimited)   GOLIVE_COOLDOWN_MINUTES=60
"""
import asyncio
import json
import os
import re
import time
from typing import Optional
from urllib.parse import urlparse

import discord
from discord import app_commands

HERE = os.path.dirname(os.path.abspath(__file__))
NAME_RE = re.compile(r"^[a-z0-9_\-]{2,40}$")
AUTO = {"twitch": ("Twitch", 0x9146FF, "https://twitch.tv/"), "kick": ("Kick", 0x53FC18, "https://kick.com/")}
MANUAL = {"facebook": ("Facebook", 0x1877F2, ("facebook.com", "fb.watch", "fb.com")),
          "youtube": ("YouTube", 0xFF0000, ("youtube.com", "youtu.be")),
          "tiktok": ("TikTok", 0x25F4EE, ("tiktok.com",)),
          "instagram": ("Instagram", 0xE1306C, ("instagram.com",))}
_golive_last = {}


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


# ---------------------------------------------------------------- storage
def _state_path():
    return os.path.join(os.environ.get("DATA_DIR") or HERE, "live_streamers.json")


def load_state():
    try:
        with open(_state_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(d):
    with open(_state_path(), "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)


def parse_name(text):
    """'https://twitch.tv/Name', '@name' or 'name'  ->  'name' (or None if it looks wrong)."""
    t = (text or "").strip()
    if "://" in t or t.lower().startswith(("twitch.tv", "kick.com", "www.")):
        p = urlparse(t if "://" in t else "https://" + t)
        parts = [x for x in p.path.split("/") if x]
        t = parts[0] if parts else ""
    t = t.lstrip("@").lower()
    return t if NAME_RE.match(t) else None


# ---------------------------------------------------------------- platform clients (blocking: run in a thread)
class Twitch:
    def __init__(self):
        self.token, self.exp = None, 0

    def enabled(self):
        return bool(_env("TWITCH_CLIENT_ID") and _env("TWITCH_CLIENT_SECRET"))

    def _headers(self, force=False):
        import requests
        if force or not self.token or time.time() > self.exp - 60:
            r = requests.post("https://id.twitch.tv/oauth2/token", timeout=20, data={
                "client_id": _env("TWITCH_CLIENT_ID"), "client_secret": _env("TWITCH_CLIENT_SECRET"),
                "grant_type": "client_credentials"})
            r.raise_for_status()
            j = r.json()
            self.token, self.exp = j["access_token"], time.time() + int(j.get("expires_in", 3600))
        return {"Client-Id": _env("TWITCH_CLIENT_ID"), "Authorization": f"Bearer {self.token}"}

    def _get(self, path, params):
        import requests
        for attempt in (0, 1):
            r = requests.get("https://api.twitch.tv/helix/" + path, params=params,
                             headers=self._headers(force=attempt == 1), timeout=20)
            if r.status_code != 401:
                break
        r.raise_for_status()
        return r.json().get("data", [])

    def exists(self, login):
        """True / False / None (could not check)"""
        try:
            return bool(self._get("users", [("login", login)]))
        except Exception:
            return None

    def live(self, logins):
        """{login: info} for the live ones; None if the API failed."""
        out = {}
        try:
            for i in range(0, len(logins), 100):
                chunk = logins[i:i + 100]
                for s in self._get("streams", [("user_login", x) for x in chunk] + [("first", 100)]):
                    thumb = (s.get("thumbnail_url") or "").replace("{width}", "1280").replace("{height}", "720")
                    out[s["user_login"].lower()] = {
                        "title": s.get("title") or "Live now", "game": s.get("game_name"),
                        "viewers": s.get("viewer_count"), "thumb": f"{thumb}?t={int(time.time())}" if thumb else None,
                        "url": "https://twitch.tv/" + s["user_login"], "name": s.get("user_name") or s["user_login"]}
        except Exception:
            return None
        return out


class Kick:
    def __init__(self):
        self.token, self.exp = None, 0

    def enabled(self):
        return bool(_env("KICK_CLIENT_ID") and _env("KICK_CLIENT_SECRET"))

    def _headers(self, force=False):
        import requests
        if force or not self.token or time.time() > self.exp - 60:
            r = requests.post("https://id.kick.com/oauth/token", timeout=20, data={
                "client_id": _env("KICK_CLIENT_ID"), "client_secret": _env("KICK_CLIENT_SECRET"),
                "grant_type": "client_credentials"})
            r.raise_for_status()
            j = r.json()
            self.token, self.exp = j["access_token"], time.time() + int(j.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self.token}"}

    def _channel(self, slug):
        import requests
        for attempt in (0, 1):
            r = requests.get("https://api.kick.com/public/v1/channels", params={"slug": slug},
                             headers=self._headers(force=attempt == 1), timeout=20)
            if r.status_code != 401:
                break
        r.raise_for_status()
        data = r.json().get("data")
        data = data if isinstance(data, list) else ([data] if data else [])
        return data[0] if data else None

    def exists(self, slug):
        try:
            return self._channel(slug) is not None
        except Exception:
            return None

    def status(self, slug):
        """info dict if live, False if offline, None if the API failed."""
        try:
            ch = self._channel(slug)
        except Exception:
            return None
        if ch is None:
            return False
        stream = ch.get("stream") or {}
        if not stream.get("is_live"):
            return False
        thumb = stream.get("thumbnail")
        thumb = thumb.get("url") if isinstance(thumb, dict) else thumb
        cat = ch.get("category") or {}
        return {"title": ch.get("stream_title") or "Live now", "game": cat.get("name"),
                "viewers": stream.get("viewer_count"), "thumb": thumb,
                "url": "https://kick.com/" + slug, "name": ch.get("slug") or slug}


class Apis:
    def __init__(self):
        self.twitch, self.kick = Twitch(), Kick()


# ---------------------------------------------------------------- embeds
def make_embed(platform, info, color):
    emb = discord.Embed(title=str(info.get("title") or "Live now")[:250], url=info.get("url"), color=color,
                        description=(f"Playing **{info['game']}**" if info.get("game") else None))
    if info.get("viewers") is not None:
        emb.add_field(name="Viewers", value=str(info["viewers"]), inline=True)
    emb.add_field(name="Platform", value=platform, inline=True)
    if info.get("thumb"):
        emb.set_image(url=info["thumb"])
    return emb


def watch_view(url, label="Watch live"):
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label=label, url=url))
    return view


# ---------------------------------------------------------------- polling
async def poll(bot, apis):
    state = load_state()
    if not state:
        return 0
    want = {"twitch": sorted({e["name"] for es in state.values() for e in es if e["platform"] == "twitch"}),
            "kick": sorted({e["name"] for es in state.values() for e in es if e["platform"] == "kick"})}
    res = {}                                    # (platform, name) -> info | False | None(unknown)
    if want["twitch"] and apis.twitch.enabled():
        live = await asyncio.to_thread(apis.twitch.live, want["twitch"])
        for n in want["twitch"]:
            res[("twitch", n)] = None if live is None else (live.get(n) or False)
    if want["kick"] and apis.kick.enabled():
        for n in want["kick"]:
            res[("kick", n)] = await asyncio.to_thread(apis.kick.status, n)
            await asyncio.sleep(0.3)
    posted, changed = 0, False
    for gid, entries in state.items():
        cfg = bot.config.get(gid)
        lv = cfg.get("live") if isinstance(cfg, dict) else None
        guild = bot.get_guild(int(gid))
        ch = guild.get_channel(int(lv["channel_id"])) if (guild and lv and lv.get("channel_id")) else None
        for e in entries:
            r = res.get((e["platform"], e["name"]))
            if r is None:
                continue
            if r:                                                   # live
                e["off"] = 0
                if not e.get("live"):
                    e["live"], changed = True, True
                    if ch is not None:
                        label, color, _ = AUTO[e["platform"]]
                        ping = f" <@&{lv['ping_role_id']}>" if lv.get("ping_role_id") else ""
                        try:
                            await ch.send(content=f"🔴 <@{e['user_id']}> is **LIVE** on {label}!{ping}",
                                          embed=make_embed(label, r, color), view=watch_view(r["url"]))
                            posted += 1
                        except discord.HTTPException as ex:
                            bot.log(f"[live] cannot post in {guild.name}: {ex} (needs Send Messages + Embed Links)")
            elif e.get("live"):                                     # offline: wait 2 checks (stream hiccups)
                e["off"], changed = e.get("off", 0) + 1, True
                if e["off"] >= 2:
                    e["live"] = False
    if changed:
        save_state(state)
    return posted


async def loop(bot, apis):
    await bot.wait_until_ready()
    minutes = max(1, _int("LIVE_MINUTES", 3))
    on = [n for n, a in (("Twitch", apis.twitch), ("Kick", apis.kick)) if a.enabled()]
    bot.log(f"[live] checking every {minutes} min | enabled: {', '.join(on) or 'none (add the keys to .env)'}")
    while not bot.is_closed():
        try:
            n = await poll(bot, apis)
            if n:
                bot.log(f"[live] posted {n} live alert(s)")
        except Exception as e:
            bot.log(f"[live] error: {e}")
        await asyncio.sleep(minutes * 60)


# ---------------------------------------------------------------- commands
def register(bot, apis=None):
    apis = apis or Apis()
    live = app_commands.Group(name="live", description="Live stream alerts", guild_only=True)
    plat_choices = [app_commands.Choice(name="Twitch", value="twitch"), app_commands.Choice(name="Kick", value="kick")]
    manual_choices = [app_commands.Choice(name=v[0], value=k) for k, v in MANUAL.items()]

    def live_cfg(guild_id):
        return (bot.config.get(str(guild_id)) or {}).get("live") or {}

    @live.command(name="setup", description="(Admin) Set the channel for live alerts. Leave empty to turn off.")
    @app_commands.describe(channel="Where live alerts are posted", ping_role="Role to ping (optional)")
    async def setup(inter: discord.Interaction, channel: Optional[discord.TextChannel] = None,
                    ping_role: Optional[discord.Role] = None):
        if not inter.user.guild_permissions.manage_guild:
            await inter.response.send_message("You need the Manage Server permission.", ephemeral=True)
            return
        cfg = bot.config.setdefault(str(inter.guild_id), {})
        if channel is None:
            cfg.pop("live", None)
            bot.persist()
            await inter.response.send_message("Live alerts are off.", ephemeral=True)
            return
        cfg["live"] = {"channel_id": channel.id, **({"ping_role_id": ping_role.id} if ping_role else {})}
        bot.persist()
        await inter.response.send_message(
            f"Done: live alerts go to {channel.mention}. Members can now use `/live add`.", ephemeral=True)

    @live.command(name="add", description="Add YOUR channel: you get an alert here when you go live")
    @app_commands.describe(platform="Twitch or Kick", name="Your channel name or link")
    @app_commands.choices(platform=plat_choices)
    async def add(inter: discord.Interaction, platform: app_commands.Choice[str], name: str):
        p = platform.value
        if not live_cfg(inter.guild_id).get("channel_id"):
            await inter.response.send_message("Live alerts are not set up here yet. Ask an admin to run `/live setup`.",
                                              ephemeral=True)
            return
        api = getattr(apis, p)
        if not api.enabled():
            await inter.response.send_message(f"{AUTO[p][0]} alerts are not enabled on this bot.", ephemeral=True)
            return
        n = parse_name(name)
        if not n:
            await inter.response.send_message("That does not look like a channel name or link.", ephemeral=True)
            return
        state = load_state()
        entries = state.setdefault(str(inter.guild_id), [])
        if any(e["platform"] == p and e["name"] == n for e in entries):
            await inter.response.send_message("That channel is already on the list.", ephemeral=True)
            return
        per_user = _int("LIVE_MAX_PER_USER", 0)          # 0 = no limit
        if per_user and sum(1 for e in entries if e["user_id"] == inter.user.id) >= per_user:
            await inter.response.send_message("You reached the limit of channels per member. Remove one first.", ephemeral=True)
            return
        per_server = _int("LIVE_MAX_PER_SERVER", 0)      # 0 = no limit
        if per_server and len(entries) >= per_server:
            await inter.response.send_message("This server reached its limit of tracked channels.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        ok = await asyncio.to_thread(api.exists, n)
        if ok is False:
            await inter.followup.send(f"I could not find **{n}** on {AUTO[p][0]}. Check the name.", ephemeral=True)
            return
        entries.append({"user_id": inter.user.id, "platform": p, "name": n, "live": False, "off": 0})
        save_state(state)
        note = "" if ok else " (I could not verify it right now, but it was saved)"
        await inter.followup.send(f"Done: I will announce you when **{n}** goes live on {AUTO[p][0]}.{note}",
                                  ephemeral=True)

    @live.command(name="remove", description="Remove a channel from the live alerts")
    @app_commands.describe(platform="Twitch or Kick", name="Channel name or link")
    @app_commands.choices(platform=plat_choices)
    async def remove(inter: discord.Interaction, platform: app_commands.Choice[str], name: str):
        n = parse_name(name)
        state = load_state()
        entries = state.get(str(inter.guild_id), [])
        found = next((e for e in entries if n and e["platform"] == platform.value and e["name"] == n), None)
        if not found:
            await inter.response.send_message("Not found on the list.", ephemeral=True)
            return
        if found["user_id"] != inter.user.id and not inter.user.guild_permissions.manage_guild:
            await inter.response.send_message("Only the member who added it (or an admin) can remove it.", ephemeral=True)
            return
        entries.remove(found)
        save_state(state)
        await inter.response.send_message("Removed.", ephemeral=True)

    @live.command(name="list", description="Show the channels tracked in this server")
    async def list_cmd(inter: discord.Interaction):
        entries = load_state().get(str(inter.guild_id), [])
        if not entries:
            await inter.response.send_message("Nobody added a channel yet. Use `/live add`.", ephemeral=True)
            return
        lines = [f"• {AUTO[e['platform']][0]}: **{e['name']}** (<@{e['user_id']}>)" + (" 🔴" if e.get("live") else "")
                 for e in entries[:40]]
        await inter.response.send_message("\n".join(lines), ephemeral=True)

    bot.tree.add_command(live)

    @bot.tree.command(name="golive", description="Announce your live on Facebook, YouTube, TikTok or Instagram")
    @app_commands.describe(platform="Where you are live", link="Link to your live", title="What you are doing (optional)")
    @app_commands.choices(platform=manual_choices)
    @app_commands.guild_only()
    async def golive(inter: discord.Interaction, platform: app_commands.Choice[str], link: str,
                     title: Optional[str] = None):
        lv = live_cfg(inter.guild_id)
        ch = inter.guild.get_channel(int(lv["channel_id"])) if lv.get("channel_id") else None
        if ch is None:
            await inter.response.send_message("Live alerts are not set up here yet. Ask an admin to run `/live setup`.",
                                              ephemeral=True)
            return
        label, color, hosts = MANUAL[platform.value]
        host = (urlparse(link.strip()).hostname or "").lower()
        if not link.strip().startswith("https://") or not any(host == h or host.endswith("." + h) for h in hosts):
            await inter.response.send_message(f"Send a valid https link from {label}.", ephemeral=True)
            return
        wait = _int("GOLIVE_COOLDOWN_MINUTES", 60) * 60
        key = (inter.guild_id, inter.user.id)
        left = _golive_last.get(key, 0) + wait - time.time()
        if left > 0:
            await inter.response.send_message(f"Please wait {int(left // 60) + 1} min before announcing again.",
                                              ephemeral=True)
            return
        _golive_last[key] = time.time()
        info = {"title": (title or "Live now")[:250], "url": link.strip(), "viewers": None}
        ping = f" <@&{lv['ping_role_id']}>" if lv.get("ping_role_id") else ""
        await ch.send(content=f"🔴 {inter.user.mention} is **LIVE** on {label}!{ping}",
                      embed=make_embed(label, info, color), view=watch_view(link.strip()))
        await inter.response.send_message("Announced!", ephemeral=True)

    started = {"v": False}
    original_on_ready = bot.on_ready

    async def on_ready():
        await original_on_ready()
        if not started["v"]:
            started["v"] = True
            asyncio.create_task(loop(bot, apis))

    bot.on_ready = on_ready