"""Football matches menu: today / tomorrow / live, by competition.

  /football setup #channel   (Manage Server) posts the panel: menu of competitions + Today / Tomorrow / Live buttons.
                             The panel itself shows today's matches and refreshes every 10 minutes.
  /football today [competition]    /football tomorrow [competition]    /football off

Needs a FREE key from https://www.football-data.org/client/register  ->  .env:  FOOTBALL_DATA_KEY=your_key
Competitions of the free plan (as far as I know): Premier League, La Liga, Serie A, Bundesliga, Ligue 1, Champions League,
Euro, World Cup, Eredivisie, Primeira Liga, Championship, Brasileirao. The Moroccan league is NOT in that list.
Times are shown in your time zone:  TIMEZONE=Africa/Casablanca  (default).  The free plan has delayed scores.
"""
import asyncio
import datetime
import os
import time
from typing import Optional

import discord
from discord import app_commands

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.football-data.org/v4/matches"
COMPS = [("ALL", "All competitions", "🌍"), ("PL", "Premier League", "🏴"), ("PD", "La Liga", "🇪🇸"), ("SA", "Serie A", "🇮🇹"),
         ("BL1", "Bundesliga", "🇩🇪"), ("FL1", "Ligue 1", "🇫🇷"), ("CL", "Champions League", "🇪🇺"), ("EC", "Euro", "🇪🇺"),
         ("WC", "World Cup", "🌍"), ("DED", "Eredivisie", "🇳🇱"), ("PPL", "Primeira Liga", "🇵🇹"), ("ELC", "Championship", "🏴"),
         ("BSA", "Brasileirão", "🇧🇷")]
EMOJI = {c[0]: c[2] for c in COMPS}
LIVE = {"IN_PLAY", "PAUSED", "LIVE"}
DONE = {"FINISHED", "AWARDED"}
OFF = {"POSTPONED", "SUSPENDED", "CANCELLED"}
_cache = {}
_share_last = {}


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


def today():
    return datetime.datetime.now(tz()).date()


class FootballError(Exception):
    pass


def fetch(day, comp="ALL"):
    """Matches whose LOCAL date is `day` -> list of dicts. Cached 5 minutes (the free plan allows 10 requests/minute)."""
    import requests
    key = _env("FOOTBALL_DATA_KEY")
    if not key:
        raise FootballError("no_key")
    ck = (day.isoformat(), comp)
    hit = _cache.get(ck)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    params = {"dateFrom": (day - datetime.timedelta(days=1)).isoformat(), "dateTo": (day + datetime.timedelta(days=2)).isoformat()}
    if comp != "ALL":
        params["competitions"] = comp
    r = requests.get(API, params=params, headers={"X-Auth-Token": key, "User-Agent": "gogo-discord-bot"}, timeout=20)
    if r.status_code in (401, 403):
        raise FootballError("bad_key")
    if r.status_code == 429:
        raise FootballError("busy")
    r.raise_for_status()
    out = []
    for m in r.json().get("matches", []):
        try:
            when = datetime.datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).astimezone(tz())
        except (KeyError, ValueError):
            continue
        if when.date() != day:
            continue
        ft = (m.get("score") or {}).get("fullTime") or {}
        out.append({"when": when, "status": m.get("status", ""), "comp": (m.get("competition") or {}).get("name", "?"),
                    "code": (m.get("competition") or {}).get("code", ""),
                    "home": ((m.get("homeTeam") or {}).get("shortName") or (m.get("homeTeam") or {}).get("name") or "?")[:18],
                    "away": ((m.get("awayTeam") or {}).get("shortName") or (m.get("awayTeam") or {}).get("name") or "?")[:18],
                    "hs": ft.get("home"), "as": ft.get("away")})
    out.sort(key=lambda m: (m["comp"], m["when"]))
    if len(_cache) > 60:
        _cache.clear()
    _cache[ck] = (time.time(), out)
    return out


def line(m):
    s, hs, as_ = m["status"], m["hs"], m["as"]
    score = f"{hs}-{as_}" if hs is not None and as_ is not None else "?-?"
    if s in LIVE:
        return f"{'⏸' if s == 'PAUSED' else '🔴'} {m['home']} **{score}** {m['away']}"
    if s in DONE:
        return f"✅ {m['home']} **{score}** {m['away']}"
    if s in OFF:
        return f"❌ {m['home']} vs {m['away']} *({s.lower()})*"
    return f"`{m['when']:%H:%M}` {m['home']} vs {m['away']}"


def matches_embed(matches, day, label, comp_name="All competitions", live_only=False):
    if live_only:
        matches = [m for m in matches if m["status"] in LIVE]
    title = f"⚽ {label} · {day:%A %d %B}"
    emb = discord.Embed(title=title, color=0x27AE60)
    if not matches:
        emb.description = "No live matches right now." if live_only else f"No matches in **{comp_name}** that day."
    else:
        groups = {}
        for m in matches:
            groups.setdefault(m["comp"], []).append(line(m))
        for name, lines in list(groups.items())[:24]:
            chunk, size = [], 0
            for ln in lines:
                if size + len(ln) > 950:
                    break
                chunk.append(ln)
                size += len(ln) + 1
            emb.add_field(name=f"{EMOJI.get(next((m['code'] for m in matches if m['comp'] == name), ''), '⚽')} {name}",
                          value="\n".join(chunk), inline=False)
        live_n = sum(1 for m in matches if m["status"] in LIVE)
        emb.description = f"**{len(matches)}** match(es)" + (f" · 🔴 **{live_n}** live" if live_n else "")
    emb.set_footer(text=f"Times: {_env('TIMEZONE', 'Africa/Casablanca')} • Data: football-data.org (delayed scores)")
    return emb


NO_KEY = ("⚽ The football menu needs a **free API key**:\n1. Register at football-data.org/client/register\n"
          "2. Add `FOOTBALL_DATA_KEY=your_key` to the bot's `.env`\n3. Restart the bot.")


def error_text(e):
    code = str(e)
    return {"no_key": NO_KEY, "bad_key": "❌ The football key was refused. Check `FOOTBALL_DATA_KEY` in `.env`.",
            "busy": "⏳ The football service is busy (free plan limit). Try again in a minute."}.get(code, "❌ The football service did not answer.")


def comp_name(code):
    return next((c[1] for c in COMPS if c[0] == code), code)


# ---------------------------------------------------------------- menus
async def show(interaction, comp, offset, live_only=False, edit=False, public=False):
    day = today() + datetime.timedelta(days=offset)
    try:
        matches = await asyncio.to_thread(fetch, day, comp)
    except FootballError as e:
        msg = error_text(e)
        (await interaction.response.send_message(msg, ephemeral=True)) if not edit else await interaction.response.edit_message(content=msg, embed=None, view=None)
        return
    except Exception:
        await interaction.response.send_message("❌ The football service did not answer.", ephemeral=True)
        return
    label = "Live now" if live_only else "Today" if offset == 0 else "Tomorrow"
    emb = matches_embed(matches, day, label, comp_name(comp), live_only)
    view = ResultView(comp, offset, live_only)
    if edit:
        await interaction.response.edit_message(content=None, embed=emb, view=view)
    else:
        await interaction.response.send_message(embed=emb, view=view, ephemeral=not public)


class CompSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(custom_id="gogo:fb:comp", placeholder="🏆 Choose a competition", min_values=1, max_values=1,
                         options=[discord.SelectOption(label=n, value=c, emoji=e) for c, n, e in COMPS])

    async def callback(self, interaction):
        await show(interaction, self.values[0], 0)


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CompSelect())
        for label, emoji, cid, off, live in (("Today", "📅", "gogo:fb:today", 0, False), ("Tomorrow", "📆", "gogo:fb:tomorrow", 1, False),
                                             ("Live", "🔴", "gogo:fb:live", 0, True)):
            b = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, custom_id=cid)
            b.callback = (lambda o, lv: (lambda interaction: show(interaction, "ALL", o, lv)))(off, live)
            self.add_item(b)


class ResultView(discord.ui.View):
    def __init__(self, comp, offset, live_only=False):
        super().__init__(timeout=600)
        self.comp, self.offset, self.live_only = comp, offset, live_only
        for label, emoji, style, cb in (("Today", "📅", discord.ButtonStyle.secondary, lambda i: show(i, self.comp, 0, edit=True)),
                                        ("Tomorrow", "📆", discord.ButtonStyle.secondary, lambda i: show(i, self.comp, 1, edit=True)),
                                        ("Refresh", "🔄", discord.ButtonStyle.secondary, self.on_refresh),
                                        ("Share", "📢", discord.ButtonStyle.primary, self.on_share)):
            b = discord.ui.Button(label=label, emoji=emoji, style=style)
            b.callback = cb
            self.add_item(b)

    async def on_refresh(self, interaction):
        _cache.clear()
        await show(interaction, self.comp, self.offset, self.live_only, edit=True)

    async def on_share(self, interaction):
        key = (interaction.guild_id, interaction.user.id)
        if time.time() - _share_last.get(key, 0) < 60:
            await interaction.response.send_message("Please wait a minute before sharing again.", ephemeral=True)
            return
        _share_last[key] = time.time()
        await show(interaction, self.comp, self.offset, self.live_only, public=True)


# ---------------------------------------------------------------- panel
def panel_embed(matches, day):
    emb = discord.Embed(title="⚽  Football", color=0x27AE60,
                        description="Pick a **competition** in the menu, or press **Today / Tomorrow / Live**.")
    if matches is None:
        emb.add_field(name="Setup needed", value=NO_KEY[:1000], inline=False)
    else:
        shown = matches[:14]
        txt = "\n".join(f"{EMOJI.get(m['code'], '⚽')} {line(m)}" for m in shown) or "No matches today."
        if len(matches) > len(shown):
            txt += f"\n… and {len(matches) - len(shown)} more"
        emb.add_field(name=f"📅 Today · {day:%a %d %b}", value=txt[:1024], inline=False)
    emb.set_footer(text="Data: football-data.org • updated every 10 min")
    emb.timestamp = datetime.datetime.now(datetime.timezone.utc)
    return emb


async def refresh_panels(bot):
    day, matches = today(), None
    try:
        matches = await asyncio.to_thread(fetch, day, "ALL")
    except FootballError as e:
        if str(e) != "no_key":
            bot.log(f"[football] {e}")
    except Exception as e:
        bot.log(f"[football] {type(e).__name__}: {e}")
        return
    for gid, cfg in list(bot.config.items()):
        c = cfg.get("football") if isinstance(cfg, dict) else None
        guild = bot.get_guild(int(gid)) if c else None
        ch = guild.get_channel(int(c["channel_id"])) if guild else None
        if ch is None:
            continue
        try:
            msg = await ch.fetch_message(int(c["message_id"]))
            await msg.edit(embed=panel_embed(matches, day))
        except discord.HTTPException as e:
            bot.log(f"[football] cannot update the panel in {guild.name}: {e} (run /football setup again)")


async def loop(bot):
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await refresh_panels(bot)
        except Exception as e:
            bot.log(f"[football] error: {e}")
        await asyncio.sleep(600)


async def install(bot, guild, channel):
    """Post the football panel in `channel` and remember it (used by the automatic setup)."""
    day, matches = today(), None
    try:
        matches = await asyncio.to_thread(fetch, day, "ALL")
    except Exception:
        pass
    msg = await channel.send(embed=panel_embed(matches, day), view=PanelView())
    bot.config.setdefault(str(guild.id), {})["football"] = {"channel_id": channel.id, "message_id": msg.id}
    bot.persist()
    return msg


def register(bot):
    group = app_commands.Group(name="football", description="Football matches", guild_only=True)
    choices = [app_commands.Choice(name=n, value=c) for c, n, e in COMPS]

    @group.command(name="setup", description="(Admin) Post the football menu in a channel")
    @app_commands.describe(channel="The football channel")
    async def setup(inter: discord.Interaction, channel: discord.TextChannel):
        if not inter.user.guild_permissions.manage_guild:
            await inter.response.send_message("You need the Manage Server permission.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        day, matches = today(), None
        try:
            matches = await asyncio.to_thread(fetch, day, "ALL")
        except Exception:
            pass
        try:
            msg = await channel.send(embed=panel_embed(matches, day), view=PanelView())
        except discord.HTTPException as e:
            await inter.followup.send(f"❌ I could not post in {channel.mention}: {e}", ephemeral=True)
            return
        bot.config.setdefault(str(inter.guild_id), {})["football"] = {"channel_id": channel.id, "message_id": msg.id}
        bot.persist()
        warn = "" if _env("FOOTBALL_DATA_KEY") else "\n⚠️ `FOOTBALL_DATA_KEY` is not set yet (free key at football-data.org), the menu will not show matches until you add it."
        await inter.followup.send(f"✅ The football menu is live in {channel.mention}.{warn}", ephemeral=True)

    @group.command(name="off", description="(Admin) Stop updating the football panel")
    async def off(inter: discord.Interaction):
        if not inter.user.guild_permissions.manage_guild:
            await inter.response.send_message("You need the Manage Server permission.", ephemeral=True)
            return
        (bot.config.get(str(inter.guild_id)) or {}).pop("football", None)
        bot.persist()
        await inter.response.send_message("Done. You can delete the panel message.", ephemeral=True)

    @group.command(name="today", description="Today's matches")
    @app_commands.describe(competition="Competition (default: all)", public="Show everybody")
    @app_commands.choices(competition=choices)
    async def today_cmd(inter: discord.Interaction, competition: Optional[app_commands.Choice[str]] = None, public: bool = False):
        await show(inter, competition.value if competition else "ALL", 0, public=public)

    @group.command(name="tomorrow", description="Tomorrow's matches")
    @app_commands.describe(competition="Competition (default: all)", public="Show everybody")
    @app_commands.choices(competition=choices)
    async def tomorrow_cmd(inter: discord.Interaction, competition: Optional[app_commands.Choice[str]] = None, public: bool = False):
        await show(inter, competition.value if competition else "ALL", 1, public=public)

    bot.tree.add_command(group)
    started = {"v": False}
    original_on_ready = bot.on_ready

    async def on_ready():
        await original_on_ready()
        if not started["v"]:
            started["v"] = True
            bot.add_view(PanelView())
            asyncio.create_task(loop(bot))

    bot.on_ready = on_ready
