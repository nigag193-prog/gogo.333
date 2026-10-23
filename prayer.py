"""Prayer times menu (cities of Europe + Morocco) with an animated mosque card.

  /prayer setup #channel    (Manage Server) posts the panel: menu "region -> city"
  /prayer city name         prayer times of ANY city (private answer, or public with public:True)
  /prayer off               forget the panel

Every answer is an animated GIF (twinkling stars, crescent moon, mosque) with the 5 prayers, the NEXT prayer
highlighted and a countdown. Buttons: refresh, calculation method, share.
Data: AlAdhan (https://aladhan.com) - free, no key. Needs weather.py (same city list) and Pillow.
Default method: Morocco (Habous) for Moroccan cities, UOIF for France, Diyanet for Turkey, Muslim World League elsewhere.
"""
import asyncio
import datetime
import io
import time
from typing import Optional

import discord
from discord import app_commands

import weather as wx

API = "https://api.aladhan.com/v1/timings"
METHODS = {3: "Muslim World League", 21: "Morocco (Habous)", 12: "France (UOIF)", 13: "Turkey (Diyanet)", 4: "Umm al-Qura"}
ORDER = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]
_cache = {}
_share_last = {}


def default_method(place):
    return {"🇲🇦": 21, "🇫🇷": 12, "🇹🇷": 13}.get(place.get("flag"), 3)


def fetch(lat, lon, method):
    """AlAdhan timings of today. Cached for 3 hours."""
    import requests
    key = (round(lat, 2), round(lon, 2), method, datetime.date.today().isoformat())
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 10800:
        return hit[1]
    r = requests.get(f"{API}/{datetime.date.today():%d-%m-%Y}", headers={"User-Agent": "gogo-discord-bot"}, timeout=15,
                     params={"latitude": lat, "longitude": lon, "method": method, "school": 0})
    r.raise_for_status()
    data = r.json()["data"]
    if len(_cache) > 300:
        _cache.clear()
    _cache[key] = (time.time(), data)
    return data


def _hm(s):
    return str(s)[:5]


def local_now(data):
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo(data["meta"]["timezone"]))
    except Exception:
        return datetime.datetime.now()


def next_prayer(data):
    """-> (name, minutes until it). After Isha it is tomorrow's Fajr (about)."""
    now = local_now(data).replace(tzinfo=None)
    t = data["timings"]
    for name in ORDER:
        h, m = map(int, _hm(t[name]).split(":"))
        when = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if when > now:
            return name, int((when - now).total_seconds() // 60) + 1
    h, m = map(int, _hm(t["Fajr"]).split(":"))
    when = now.replace(hour=h, minute=m, second=0, microsecond=0) + datetime.timedelta(days=1)
    return "Fajr", int((when - now).total_seconds() // 60)


def _dur(mins):
    h, m = divmod(max(mins, 0), 60)
    return f"{h}h {m:02d}m" if h else f"{m} min"


def hijri_line(data):
    try:
        h = data["date"]["hijri"]
        return f"{int(h['day'])} {h['month']['en']} {h['year']}"
    except Exception:
        return ""


def build(place, data, method):
    """-> (gif bytes, embed text lines)"""
    import prayer_card
    nxt, mins = next_prayer(data)
    t = data["timings"]
    times = [(n, _hm(t[n])) for n in ORDER]
    now = local_now(data)
    sub = f"{now:%A %d %B %Y}" + (f"  •  {hijri_line(data)}" if hijri_line(data) else "")
    gif, _ = prayer_card.render_gif(place["name"].split(",")[0], sub, times, nxt, f"{nxt} in {_dur(mins)}",
                                    f"Method: {METHODS.get(method, method)}  •  Sunrise {_hm(t['Sunrise'])}")
    return gif, nxt, mins


async def build_message(place, method):
    data = await asyncio.to_thread(fetch, place["lat"], place["lon"], method)
    try:
        gif, nxt, mins = await asyncio.to_thread(build, place, data, method)
    except Exception as e:                                   # no Pillow: a plain embed
        print(f"[prayer] card not drawn ({type(e).__name__}: {e}), using the text card")
        nxt, mins = next_prayer(data)
        t = data["timings"]
        emb = discord.Embed(title=f"🕌 Prayer times: {place['name']}", color=0x1B2A5C,
                            description="\n".join(f"{'➡️' if n == nxt else '▫️'} **{n}**  {_hm(t[n])}" for n in ORDER)
                            + f"\n\nNext: **{nxt}** in **{_dur(mins)}**")
        emb.set_footer(text=f"Method: {METHODS.get(method, method)} • Times by AlAdhan.com")
        return emb, None
    emb = discord.Embed(color=0x1B2A5C)
    emb.set_image(url="attachment://prayer.gif")
    emb.set_footer(text=f"{place['name']} • Next: {nxt} in {_dur(mins)} • Times by AlAdhan.com")
    return emb, discord.File(io.BytesIO(gif), filename="prayer.gif")


async def _send(interaction, place, method, edit=True):
    try:
        emb, file = await build_message(place, method)
    except Exception:
        msg = "❌ The prayer-times service did not answer. Try again in a minute."
        if edit:
            await interaction.edit_original_response(content=msg, embed=None, attachments=[], view=None)
        else:
            await interaction.followup.send(msg, ephemeral=True)
        return
    view = ResultView(place, method)
    if edit:
        await interaction.edit_original_response(content=None, embed=emb, attachments=[file] if file else [], view=view)
    else:
        await interaction.followup.send(embed=emb, view=view, ephemeral=True, **({"file": file} if file else {}))


# ---------------------------------------------------------------- menus
class RegionSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(custom_id="gogo:pr:region", placeholder="🌍 Choose a region", min_values=1, max_values=1,
                         options=[discord.SelectOption(label=label, value=key, emoji=emoji, description=f"{len(cities)} cities")
                                  for key, (label, emoji, cities) in wx.REGIONS.items()])

    async def callback(self, interaction):
        key = self.values[0]
        await interaction.response.send_message(f"{wx.REGIONS[key][1]} **{wx.REGIONS[key][0]}**: choose a city",
                                                view=CityView(key), ephemeral=True)


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(RegionSelect())


class CitySelect(discord.ui.Select):
    def __init__(self, key):
        self.key = key
        super().__init__(placeholder="🏙️ Choose a city", min_values=1, max_values=1,
                         options=[discord.SelectOption(label=c[0], value=str(i), emoji=c[1])
                                  for i, c in enumerate(wx.REGIONS[key][2][:25])])

    async def callback(self, interaction):
        c = wx.REGIONS[self.key][2][int(self.values[0])]
        place = {"name": c[0], "flag": c[1], "lat": c[2], "lon": c[3]}
        await interaction.response.edit_message(content="🕌 Preparing the prayer times...", view=None)
        await _send(interaction, place, default_method(place))


class CityView(discord.ui.View):
    def __init__(self, key):
        super().__init__(timeout=300)
        self.add_item(CitySelect(key))


class ResultView(discord.ui.View):
    def __init__(self, place, method):
        super().__init__(timeout=900)
        self.place, self.method = place, method
        for label, emoji, style, cb in (("Refresh", "🔄", discord.ButtonStyle.secondary, self.on_refresh),
                                        (METHODS.get(method, str(method))[:30], "⚙️", discord.ButtonStyle.secondary, self.on_method),
                                        ("Share", "📢", discord.ButtonStyle.primary, self.on_share)):
            b = discord.ui.Button(label=label, emoji=emoji, style=style)
            b.callback = cb
            self.add_item(b)

    async def on_refresh(self, interaction):
        _cache.clear()
        await interaction.response.edit_message(content="🕌 Preparing the prayer times...", view=None)
        await _send(interaction, self.place, self.method)

    async def on_method(self, interaction):
        cycle = [default_method(self.place)] + [m for m in (3, 21, 12, 13, 4) if m != default_method(self.place)]
        nxt = cycle[(cycle.index(self.method) + 1) % len(cycle)] if self.method in cycle else cycle[0]
        await interaction.response.edit_message(content="🕌 Preparing the prayer times...", view=None)
        await _send(interaction, self.place, nxt)

    async def on_share(self, interaction):
        key = (interaction.guild_id, interaction.user.id)
        if time.time() - _share_last.get(key, 0) < 60:
            await interaction.response.send_message("Please wait a minute before sharing again.", ephemeral=True)
            return
        _share_last[key] = time.time()
        await interaction.response.defer()
        try:
            emb, file = await build_message(self.place, self.method)
        except Exception:
            await interaction.followup.send("❌ The prayer-times service did not answer.", ephemeral=True)
            return
        await interaction.followup.send(content=f"🕌 {interaction.user.mention} shared:", embed=emb,
                                        allowed_mentions=discord.AllowedMentions.none(), **({"file": file} if file else {}))


# ---------------------------------------------------------------- commands
def register(bot):
    group = app_commands.Group(name="prayer", description="Prayer times", guild_only=True)

    @group.command(name="setup", description="(Admin) Post the prayer-times menu in a channel")
    @app_commands.describe(channel="The prayer-times channel")
    async def setup(inter: discord.Interaction, channel: discord.TextChannel):
        if not inter.user.guild_permissions.manage_guild:
            await inter.response.send_message("You need the Manage Server permission.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        emb = discord.Embed(title="🕌  Prayer times", color=0x1B2A5C,
                            description="Pick a **region** in the menu below, then your **city**.\n"
                                        "You get the 5 prayers, the **next prayer** with a countdown, and the method used.")
        file = None
        try:
            import prayer_card
            gif, _ = await asyncio.to_thread(prayer_card.render_gif, "Prayer Times", "Pick your city below", [], None, "", "")
            file = discord.File(io.BytesIO(gif), filename="banner.gif")
            emb.set_image(url="attachment://banner.gif")
        except Exception as e:
            print(f"[prayer] banner not drawn: {e}")
        emb.set_footer(text="Times by AlAdhan.com")
        try:
            msg = await channel.send(embed=emb, view=PanelView(), **({"file": file} if file else {}))
        except discord.HTTPException as e:
            await inter.followup.send(f"❌ I could not post in {channel.mention}: {e}", ephemeral=True)
            return
        bot.config.setdefault(str(inter.guild_id), {})["prayer"] = {"channel_id": channel.id, "message_id": msg.id}
        bot.persist()
        await inter.followup.send(f"✅ The prayer-times menu is live in {channel.mention}.", ephemeral=True)

    @group.command(name="off", description="(Admin) Forget the prayer-times panel")
    async def off(inter: discord.Interaction):
        if not inter.user.guild_permissions.manage_guild:
            await inter.response.send_message("You need the Manage Server permission.", ephemeral=True)
            return
        (bot.config.get(str(inter.guild_id)) or {}).pop("prayer", None)
        bot.persist()
        await inter.response.send_message("Done. You can delete the panel message.", ephemeral=True)

    @group.command(name="city", description="Prayer times of any city in the world")
    @app_commands.describe(name="City name, e.g. Lyon or Casablanca", public="Show the answer to everyone")
    async def city(inter: discord.Interaction, name: str, public: bool = False):
        await inter.response.defer(ephemeral=not public)
        try:
            place = wx.find_city(name.strip()) or await asyncio.to_thread(wx.geocode, name.strip()[:80])
        except Exception:
            await inter.followup.send("❌ The search service did not answer. Try again in a minute.")
            return
        if place is None:
            await inter.followup.send(f"I could not find a city called **{name[:60]}**.")
            return
        method = default_method(place)
        try:
            emb, file = await build_message(place, method)
        except Exception:
            await inter.followup.send("❌ The prayer-times service did not answer. Try again in a minute.")
            return
        await inter.followup.send(embed=emb, view=ResultView(place, method), **({"file": file} if file else {}))

    bot.tree.add_command(group)
    started = {"v": False}
    original_on_ready = bot.on_ready

    async def on_ready():
        await original_on_ready()
        if not started["v"]:
            started["v"] = True
            bot.add_view(PanelView())                         # the menu keeps working after a restart

    bot.on_ready = on_ready
