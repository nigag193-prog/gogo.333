"""Weather menu for Europe (+ Morocco) - a pretty panel in a weather channel.

  /weather setup #channel   (Manage Server) posts the panel: a menu "region -> city". The panel also shows the big
                            cities live and refreshes itself every 30 minutes.
  /weather off              removes the panel from the settings
  /weather city name        weather of ANY city in the world (private answer, or public with public:True)

Cards: weather_card.py draws iOS-style images (needs Pillow, already in requirements). Without it the old embed is used.
Data: Open-Meteo (https://open-meteo.com) - free for non-commercial use, no key. Attribution is shown in every card.
Saved per server in guild_config.json -> "<server id>": {"weather": {"channel_id":..., "message_id":..., "units":"c"}}

.env (optional):  WEATHER_MINUTES=30   (how often the panel refreshes, minimum 15)
"""
import asyncio
import datetime
import io
import os
import time
from typing import Optional

import discord
from discord import app_commands

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.open-meteo.com/v1/forecast"
GEO = "https://geocoding-api.open-meteo.com/v1/search"
CACHE_SECONDS = 600
_cache = {}
_share_last = {}

# region key -> (label, emoji, [(city, flag, lat, lon), ...])   (max 25 cities per region)
REGIONS = {
    "west": ("Western Europe", "🥐", [
        ("Paris", "🇫🇷", 48.8566, 2.3522), ("Lyon", "🇫🇷", 45.7640, 4.8357), ("Marseille", "🇫🇷", 43.2965, 5.3698),
        ("Nice", "🇫🇷", 43.7102, 7.2620), ("Toulouse", "🇫🇷", 43.6047, 1.4442), ("Bordeaux", "🇫🇷", 44.8378, -0.5792),
        ("Brussels", "🇧🇪", 50.8503, 4.3517), ("Antwerp", "🇧🇪", 51.2194, 4.4025), ("Amsterdam", "🇳🇱", 52.3676, 4.9041),
        ("Rotterdam", "🇳🇱", 51.9244, 4.4777), ("Luxembourg", "🇱🇺", 49.6116, 6.1319)]),
    "uk": ("UK & Ireland", "☔", [
        ("London", "🇬🇧", 51.5074, -0.1278), ("Manchester", "🇬🇧", 53.4808, -2.2426), ("Birmingham", "🇬🇧", 52.4862, -1.8904),
        ("Liverpool", "🇬🇧", 53.4084, -2.9916), ("Edinburgh", "🇬🇧", 55.9533, -3.1883), ("Glasgow", "🇬🇧", 55.8642, -4.2518),
        ("Cardiff", "🇬🇧", 51.4816, -3.1791), ("Belfast", "🇬🇧", 54.5973, -5.9301), ("Dublin", "🇮🇪", 53.3498, -6.2603)]),
    "south": ("Southern Europe", "☀️", [
        ("Madrid", "🇪🇸", 40.4168, -3.7038), ("Barcelona", "🇪🇸", 41.3874, 2.1686), ("Valencia", "🇪🇸", 39.4699, -0.3763),
        ("Seville", "🇪🇸", 37.3891, -5.9845), ("Málaga", "🇪🇸", 36.7213, -4.4214), ("Lisbon", "🇵🇹", 38.7223, -9.1393),
        ("Porto", "🇵🇹", 41.1579, -8.6291), ("Rome", "🇮🇹", 41.9028, 12.4964), ("Milan", "🇮🇹", 45.4642, 9.1900),
        ("Naples", "🇮🇹", 40.8518, 14.2681), ("Athens", "🇬🇷", 37.9838, 23.7275), ("Valletta", "🇲🇹", 35.8989, 14.5146)]),
    "central": ("Central Europe", "🏔️", [
        ("Berlin", "🇩🇪", 52.5200, 13.4050), ("Munich", "🇩🇪", 48.1351, 11.5820), ("Hamburg", "🇩🇪", 53.5511, 9.9937),
        ("Frankfurt", "🇩🇪", 50.1109, 8.6821), ("Cologne", "🇩🇪", 50.9375, 6.9603), ("Vienna", "🇦🇹", 48.2082, 16.3738),
        ("Zurich", "🇨🇭", 47.3769, 8.5417), ("Geneva", "🇨🇭", 46.2044, 6.1432), ("Prague", "🇨🇿", 50.0755, 14.4378),
        ("Warsaw", "🇵🇱", 52.2297, 21.0122), ("Kraków", "🇵🇱", 50.0647, 19.9450), ("Budapest", "🇭🇺", 47.4979, 19.0402),
        ("Bratislava", "🇸🇰", 48.1486, 17.1077)]),
    "north": ("Nordic & Baltic", "❄️", [
        ("Stockholm", "🇸🇪", 59.3293, 18.0686), ("Gothenburg", "🇸🇪", 57.7089, 11.9746), ("Oslo", "🇳🇴", 59.9139, 10.7522),
        ("Copenhagen", "🇩🇰", 55.6761, 12.5683), ("Helsinki", "🇫🇮", 60.1699, 24.9384), ("Reykjavík", "🇮🇸", 64.1466, -21.9426),
        ("Tallinn", "🇪🇪", 59.4370, 24.7536), ("Riga", "🇱🇻", 56.9496, 24.1052), ("Vilnius", "🇱🇹", 54.6872, 25.2797)]),
    "east": ("Balkans & East", "🏛️", [
        ("Bucharest", "🇷🇴", 44.4268, 26.1025), ("Sofia", "🇧🇬", 42.6977, 23.3219), ("Belgrade", "🇷🇸", 44.7866, 20.4489),
        ("Zagreb", "🇭🇷", 45.8150, 15.9819), ("Ljubljana", "🇸🇮", 46.0569, 14.5058), ("Sarajevo", "🇧🇦", 43.8563, 18.4131),
        ("Tirana", "🇦🇱", 41.3275, 19.8187), ("Istanbul", "🇹🇷", 41.0082, 28.9784), ("Kyiv", "🇺🇦", 50.4501, 30.5234)]),
    "ma": ("Maroc", "🇲🇦", [
        ("Casablanca", "🇲🇦", 33.5731, -7.5898), ("Rabat", "🇲🇦", 34.0209, -6.8416), ("Marrakech", "🇲🇦", 31.6295, -7.9811),
        ("Tanger", "🇲🇦", 35.7595, -5.8340), ("Fès", "🇲🇦", 34.0181, -5.0078), ("Agadir", "🇲🇦", 30.4278, -9.5981),
        ("Oujda", "🇲🇦", 34.6814, -1.9086), ("Tétouan", "🇲🇦", 35.5785, -5.3684)]),
}
FEATURED = ["Paris", "London", "Madrid", "Barcelona", "Berlin", "Rome", "Brussels", "Amsterdam"]

# WMO weather code -> (text, day emoji, night emoji)
CODES = {0: ("Clear sky", "☀️", "🌙"), 1: ("Mostly clear", "🌤️", "🌙"), 2: ("Partly cloudy", "⛅", "☁️"),
         3: ("Overcast", "☁️", "☁️"), 45: ("Fog", "🌫️", "🌫️"), 48: ("Freezing fog", "🌫️", "🌫️"),
         51: ("Light drizzle", "🌦️", "🌧️"), 53: ("Drizzle", "🌦️", "🌧️"), 55: ("Heavy drizzle", "🌧️", "🌧️"),
         56: ("Freezing drizzle", "🌧️", "🌧️"), 57: ("Freezing drizzle", "🌧️", "🌧️"),
         61: ("Light rain", "🌦️", "🌧️"), 63: ("Rain", "🌧️", "🌧️"), 65: ("Heavy rain", "🌧️", "🌧️"),
         66: ("Freezing rain", "🌧️", "🌧️"), 67: ("Freezing rain", "🌧️", "🌧️"),
         71: ("Light snow", "🌨️", "🌨️"), 73: ("Snow", "❄️", "❄️"), 75: ("Heavy snow", "❄️", "❄️"), 77: ("Snow grains", "❄️", "❄️"),
         80: ("Rain showers", "🌦️", "🌧️"), 81: ("Rain showers", "🌧️", "🌧️"), 82: ("Violent showers", "⛈️", "⛈️"),
         85: ("Snow showers", "🌨️", "🌨️"), 86: ("Heavy snow showers", "❄️", "❄️"),
         95: ("Thunderstorm", "⛈️", "⛈️"), 96: ("Thunderstorm, hail", "⛈️", "⛈️"), 99: ("Thunderstorm, hail", "⛈️", "⛈️")}


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


# ---------------------------------------------------------------- data
def describe(code, is_day=1):
    text, day, night = CODES.get(int(code), ("Unknown", "🌡️", "🌡️"))
    return text, (day if is_day else night)


def flag_of(country_code):
    cc = (country_code or "").upper()
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in cc) if len(cc) == 2 and cc.isalpha() else "🌍"


def find_city(name):
    key = name.casefold()
    for _, _, cities in REGIONS.values():
        for c in cities:
            if c[0].casefold() == key:
                return {"name": c[0], "flag": c[1], "lat": c[2], "lon": c[3]}
    return None


def fetch(lat, lon, units="c"):
    """Current weather + 5 days. Cached for 10 minutes. Raises on a network problem."""
    import requests
    key = (round(lat, 2), round(lon, 2), units)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    params = {"latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 7,
              "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,"
                         "wind_speed_10m,wind_direction_10m,wind_gusts_10m,pressure_msl,cloud_cover,is_day",
              "hourly": "temperature_2m,weather_code,precipitation_probability,is_day,visibility",
              "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                       "precipitation_sum,sunrise,sunset,uv_index_max"}
    if units == "f":
        params.update(temperature_unit="fahrenheit", wind_speed_unit="mph")
    r = requests.get(API, params=params, headers={"User-Agent": "gogo-discord-bot"}, timeout=15)
    r.raise_for_status()
    data = r.json()
    if len(_cache) > 400:
        _cache.clear()
    _cache[key] = (time.time(), data)
    return data


def fetch_board(cities, units="c"):
    """[(city dict, temp, emoji)] for the featured cities, in ONE request. Missing ones are skipped."""
    import requests
    r = requests.get(API, headers={"User-Agent": "gogo-discord-bot"}, timeout=15, params={
        "latitude": ",".join(str(c["lat"]) for c in cities), "longitude": ",".join(str(c["lon"]) for c in cities),
        "current": "temperature_2m,weather_code,is_day", "timezone": "auto",
        **({"temperature_unit": "fahrenheit"} if units == "f" else {})})
    r.raise_for_status()
    data = r.json()
    data = data if isinstance(data, list) else [data]
    out = []
    for c, d in zip(cities, data):
        cur = d.get("current") or {}
        if "temperature_2m" in cur:
            out.append((c, cur["temperature_2m"], describe(cur.get("weather_code", 0), cur.get("is_day", 1))[1]))
    return out


def geocode(name):
    import requests
    r = requests.get(GEO, params={"name": name, "count": 1, "language": "en", "format": "json"},
                     headers={"User-Agent": "gogo-discord-bot"}, timeout=15)
    r.raise_for_status()
    res = (r.json().get("results") or [])
    if not res:
        return None
    g = res[0]
    label = g["name"] + (f", {g['admin1']}" if g.get("admin1") and g.get("admin1") != g["name"] else "")
    return {"name": label, "flag": flag_of(g.get("country_code")), "lat": g["latitude"], "lon": g["longitude"]}


# ---------------------------------------------------------------- the pretty part
def _t(v, units):
    return f"{round(v)}°{'F' if units == 'f' else 'C'}"


def _bar(pct):
    n = max(0, min(10, round((pct or 0) / 10)))
    return "▰" * n + "▱" * (10 - n)


def _compass(deg):
    names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    arrows = ["⬇️", "↙️", "⬅️", "↖️", "⬆️", "↗️", "➡️", "↘️"]          # the wind blows TOWARDS the arrow
    i = round((deg or 0) / 45) % 8
    return names[i], arrows[i]


def _color(temp_c):
    return (0x74B9FF if temp_c <= 0 else 0x00CEC9 if temp_c < 10 else 0x55EFC4 if temp_c < 20
            else 0xFDCB6E if temp_c < 28 else 0xFF7675)


def _uv(v):
    v = round(v or 0)
    return f"{v} · " + ("low" if v < 3 else "moderate" if v < 6 else "high" if v < 8 else "very high")


def _hm(iso):
    return str(iso)[11:16] if iso else "--:--"


def tips(cur, today_rain, uv, units):
    feels = cur.get("apparent_temperature", cur.get("temperature_2m", 15))
    feels_c = (feels - 32) * 5 / 9 if units == "f" else feels
    wind_kmh = cur.get("wind_speed_10m", 0) * (1.609 if units == "f" else 1)
    out = []
    if (today_rain or 0) >= 50 or (cur.get("precipitation") or 0) > 0.2:
        out.append("☂️ Take an umbrella")
    if feels_c <= 0:
        out.append("🧤 Freezing: dress very warm")
    elif feels_c < 10:
        out.append("🧥 Wear a jacket")
    if feels_c >= 30:
        out.append("🥵 Hot: drink water")
    if (uv or 0) >= 6:
        out.append("🕶️ Sunglasses + sunscreen")
    if wind_kmh >= 40:
        out.append("🌬️ Very windy")
    return " · ".join(out[:3]) or "😎 Nice weather, enjoy!"


def weather_embed(place, data, units="c"):
    cur, daily = data["current"], data["daily"]
    text, emoji = describe(cur.get("weather_code", 0), cur.get("is_day", 1))
    t = cur["temperature_2m"]
    feels = cur.get("apparent_temperature", t)
    t_c = (t - 32) * 5 / 9 if units == "f" else t
    pop = (daily.get("precipitation_probability_max") or [0])[0] or 0
    uv = (daily.get("uv_index_max") or [0])[0] or 0
    wind_unit = "mph" if units == "f" else "km/h"
    name, arrow = _compass(cur.get("wind_direction_10m"))
    emb = discord.Embed(title=f"{emoji}  {place['name']}  {place['flag']}",
                        description=f"# {_t(t, units)}\n**{text}** · feels like **{_t(feels, units)}**",
                        color=_color(t_c))
    emb.add_field(name="🌡️ Today", value=f"↑ **{_t(daily['temperature_2m_max'][0], units)}**   ↓ **{_t(daily['temperature_2m_min'][0], units)}**", inline=True)
    emb.add_field(name="🌧️ Rain chance", value=f"`{_bar(pop)}` {round(pop)}%", inline=True)
    emb.add_field(name="💧 Humidity", value=f"`{_bar(cur.get('relative_humidity_2m', 0))}` {round(cur.get('relative_humidity_2m', 0))}%", inline=True)
    emb.add_field(name="💨 Wind", value=f"{arrow} **{round(cur.get('wind_speed_10m', 0))}** {wind_unit} {name}", inline=True)
    emb.add_field(name="🌅 Sun", value=f"🌅 {_hm(daily['sunrise'][0])}   🌇 {_hm(daily['sunset'][0])}", inline=True)
    emb.add_field(name="🕶️ UV index", value=_uv(uv), inline=True)
    rows = []
    for i in range(min(5, len(daily["time"]))):
        day = datetime.date.fromisoformat(daily["time"][i])
        label = "Today" if i == 0 else day.strftime("%a %d")
        rows.append(f"{label:<7} {describe(daily['weather_code'][i])[1]}  {_t(daily['temperature_2m_max'][i], units):>5} / "
                    f"{_t(daily['temperature_2m_min'][i], units):<5}  ☔{round(daily['precipitation_probability_max'][i] or 0):>3}%")
    emb.add_field(name="📅 Next days", value="```\n" + "\n".join(rows) + "\n```", inline=False)
    emb.add_field(name="💡 Tip", value=tips(cur, pop, uv, units), inline=False)
    emb.set_footer(text=f"Local time {_hm(cur.get('time'))} • Weather data by Open-Meteo.com")
    return emb


def panel_embed(board, units="c"):
    emb = discord.Embed(title="🌦️  Weather in Europe",
                        description="Pick a **region** in the menu below, then your **city**.\n"
                                    "You get the weather now, the next 5 days and a tip for today.",
                        color=0x3498DB)
    if board:
        lines = [f"{e}  **{c['name']}** {c['flag']}  ·  {_t(t, units)}" for c, t, e in board]
        half = (len(lines) + 1) // 2
        emb.add_field(name="🔴 Right now", value="\n".join(lines[:half]), inline=True)
        if lines[half:]:
            emb.add_field(name="\u200b", value="\n".join(lines[half:]), inline=True)
    emb.set_footer(text="Updated every 30 min • Weather data by Open-Meteo.com")
    emb.timestamp = datetime.datetime.now(datetime.timezone.utc)
    return emb


# ---------------------------------------------------------------- menus
async def build_message(place, units, mode="now"):
    """-> (embed, file). The card image is drawn in a thread; if Pillow is missing the old embed is used."""
    data = await asyncio.to_thread(fetch, place["lat"], place["lon"], units)
    try:
        import weather_card
        png = await asyncio.to_thread(weather_card.render_details if mode == "details" else weather_card.render_now,
                                      place, data, units)
        name = f"weather_{mode}.png"
        emb = discord.Embed(color=0x2C3E50)
        emb.set_image(url=f"attachment://{name}")
        emb.set_footer(text=f"{place['name']} {place['flag']} • Weather data by Open-Meteo.com")
        return emb, discord.File(io.BytesIO(png), filename=name)
    except Exception as e:
        print(f"[weather] card not drawn ({type(e).__name__}: {e}), using the text card")
        return weather_embed(place, data, units), None


async def _send_weather(interaction, place, units, edit=True, mode="now"):
    try:
        emb, file = await build_message(place, units, mode)
    except Exception:
        msg = "❌ The weather service did not answer. Try again in a minute."
        if edit:
            await interaction.edit_original_response(content=msg, embed=None, attachments=[], view=None)
        else:
            await interaction.followup.send(msg, ephemeral=True)
        return
    view = ResultView(place, units, mode)
    if edit:
        await interaction.edit_original_response(content=None, embed=emb, attachments=[file] if file else [], view=view)
    else:
        await interaction.followup.send(embed=emb, view=view, ephemeral=True, **({"file": file} if file else {}))


class RegionSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(custom_id="gogo:wx:region", placeholder="🌍 Choose a region", min_values=1, max_values=1,
                         options=[discord.SelectOption(label=label, value=key, emoji=emoji, description=f"{len(cities)} cities")
                                  for key, (label, emoji, cities) in REGIONS.items()])

    async def callback(self, interaction):
        key = self.values[0]
        await interaction.response.send_message(f"{REGIONS[key][1]} **{REGIONS[key][0]}**: choose a city",
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
                                  for i, c in enumerate(REGIONS[key][2][:25])])

    async def callback(self, interaction):
        c = REGIONS[self.key][2][int(self.values[0])]
        await interaction.response.edit_message(content="⏳ Loading the weather...", view=None)
        await _send_weather(interaction, {"name": c[0], "flag": c[1], "lat": c[2], "lon": c[3]}, "c")


class CityView(discord.ui.View):
    def __init__(self, key):
        super().__init__(timeout=300)
        self.add_item(CitySelect(key))


class ResultView(discord.ui.View):
    def __init__(self, place, units, mode="now"):
        super().__init__(timeout=600)
        self.place, self.units, self.mode = place, units, mode
        other = "details" if mode == "now" else "now"
        buttons = [("Refresh", "🔄", discord.ButtonStyle.secondary, self.on_refresh),
                   ("Details" if mode == "now" else "Forecast", "📊" if mode == "now" else "🌤️", discord.ButtonStyle.secondary, self.on_switch),
                   ("Share", "📢", discord.ButtonStyle.primary, self.on_share),
                   ("°F" if units == "c" else "°C", "🌡️", discord.ButtonStyle.secondary, self.on_toggle)]
        for label, emoji, style, cb in buttons:
            b = discord.ui.Button(label=label, emoji=emoji, style=style)
            b.callback = cb
            self.add_item(b)

    async def _loading(self, interaction):
        await interaction.response.edit_message(content="⏳ Drawing the weather card...", view=None)

    async def on_refresh(self, interaction):
        _cache.clear()
        await self._loading(interaction)
        await _send_weather(interaction, self.place, self.units, mode=self.mode)

    async def on_switch(self, interaction):
        await self._loading(interaction)
        await _send_weather(interaction, self.place, self.units, mode="details" if self.mode == "now" else "now")

    async def on_toggle(self, interaction):
        await self._loading(interaction)
        await _send_weather(interaction, self.place, "f" if self.units == "c" else "c", mode=self.mode)

    async def on_share(self, interaction):
        key = (interaction.guild_id, interaction.user.id)
        if time.time() - _share_last.get(key, 0) < 60:
            await interaction.response.send_message("Please wait a minute before sharing again.", ephemeral=True)
            return
        _share_last[key] = time.time()
        await interaction.response.defer()
        try:
            emb, file = await build_message(self.place, self.units, self.mode)
        except Exception:
            await interaction.followup.send("❌ The weather service did not answer.", ephemeral=True)
            return
        await interaction.followup.send(content=f"🌍 {interaction.user.mention} shared:", embed=emb,
                                        allowed_mentions=discord.AllowedMentions.none(), **({"file": file} if file else {}))


# ---------------------------------------------------------------- panel refresh
async def refresh_boards(bot):
    cities = [c for c in (find_city(n) for n in FEATURED) if c]
    boards = {}
    for gid, cfg in list(bot.config.items()):
        w = cfg.get("weather") if isinstance(cfg, dict) else None
        if not w or not w.get("message_id"):
            continue
        units = w.get("units", "c")
        if units not in boards:
            try:
                boards[units] = await asyncio.to_thread(fetch_board, cities, units)
            except Exception as e:
                bot.log(f"[weather] board: {type(e).__name__}: {e}")
                continue
        guild = bot.get_guild(int(gid))
        ch = guild.get_channel(int(w["channel_id"])) if guild else None
        if ch is None:
            continue
        try:
            msg = await ch.fetch_message(int(w["message_id"]))
            await msg.edit(embed=panel_embed(boards[units], units))
        except discord.HTTPException as e:
            bot.log(f"[weather] cannot update the panel in {guild.name}: {e} (run /weather setup again)")


async def loop(bot):
    await bot.wait_until_ready()
    raw = _env("WEATHER_MINUTES", "30")
    minutes = max(15, int(raw)) if raw.isdigit() else 30
    bot.log(f"[weather] panel refresh every {minutes} min")
    while not bot.is_closed():
        try:
            await refresh_boards(bot)
        except Exception as e:
            bot.log(f"[weather] error: {e}")
        await asyncio.sleep(minutes * 60)


# ---------------------------------------------------------------- commands
async def install(bot, guild, channel, units="c"):
    """Post the weather panel in `channel` and remember it (used by the automatic setup)."""
    cities = [c for c in (find_city(n) for n in FEATURED) if c]
    try:
        board = await asyncio.to_thread(fetch_board, cities, units)
    except Exception:
        board = []
    msg = await channel.send(embed=panel_embed(board, units), view=PanelView())
    bot.config.setdefault(str(guild.id), {})["weather"] = {"channel_id": channel.id, "message_id": msg.id, "units": units}
    bot.persist()
    return msg


def register(bot):
    group = app_commands.Group(name="weather", description="Weather menu", guild_only=True)

    @group.command(name="setup", description="(Admin) Post the weather menu in a channel")
    @app_commands.describe(channel="The weather channel", fahrenheit="Show °F instead of °C")
    async def setup(inter: discord.Interaction, channel: discord.TextChannel, fahrenheit: bool = False):
        if not inter.user.guild_permissions.manage_guild:
            await inter.response.send_message("You need the Manage Server permission.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        units = "f" if fahrenheit else "c"
        cities = [c for c in (find_city(n) for n in FEATURED) if c]
        try:
            board = await asyncio.to_thread(fetch_board, cities, units)
        except Exception:
            board = []
        try:
            msg = await channel.send(embed=panel_embed(board, units), view=PanelView())
        except discord.HTTPException as e:
            await inter.followup.send(f"❌ I could not post in {channel.mention}: {e}", ephemeral=True)
            return
        bot.config.setdefault(str(inter.guild_id), {})["weather"] = {
            "channel_id": channel.id, "message_id": msg.id, "units": units}
        bot.persist()
        await inter.followup.send(f"✅ The weather menu is live in {channel.mention}.", ephemeral=True)

    @group.command(name="off", description="(Admin) Stop updating the weather panel")
    async def off(inter: discord.Interaction):
        if not inter.user.guild_permissions.manage_guild:
            await inter.response.send_message("You need the Manage Server permission.", ephemeral=True)
            return
        (bot.config.get(str(inter.guild_id)) or {}).pop("weather", None)
        bot.persist()
        await inter.response.send_message("The panel will not be updated any more. You can delete the message.", ephemeral=True)

    @group.command(name="city", description="Weather of any city in the world")
    @app_commands.describe(name="City name, e.g. Lyon or Casablanca", public="Show the answer to everyone")
    async def city(inter: discord.Interaction, name: str, public: bool = False):
        await inter.response.defer(ephemeral=not public)
        try:
            place = await asyncio.to_thread(geocode, name.strip()[:80])
        except Exception:
            await inter.followup.send("❌ The search service did not answer. Try again in a minute.")
            return
        if place is None:
            await inter.followup.send(f"I could not find a city called **{name[:60]}**.")
            return
        try:
            emb, file = await build_message(place, "c")
        except Exception:
            await inter.followup.send("❌ The weather service did not answer. Try again in a minute.")
            return
        await inter.followup.send(embed=emb, view=ResultView(place, "c"), **({"file": file} if file else {}))

    bot.tree.add_command(group)

    started = {"v": False}
    original_on_ready = bot.on_ready

    async def on_ready():
        await original_on_ready()
        if not started["v"]:
            started["v"] = True
            bot.add_view(PanelView())                       # keeps the menu alive after a restart
            asyncio.create_task(loop(bot))

    bot.on_ready = on_ready
