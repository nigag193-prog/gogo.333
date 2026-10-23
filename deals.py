"""Game-deal alerts: posts big PC game discounts (default 80%+) into a channel.
Data: CheapShark (https://www.cheapshark.com) - free API, attribution shown in the footer.

Per server:  /deals #channel   (needs Manage Server)   or in the server JSON:  "deals": {"channel": "💸・deals"}

.env options (all optional):
    DEALS_MIN_DISCOUNT=80     minimum discount in percent
    DEALS_MIN_RATING=70       skip games whose Steam rating is known and lower than this (0 = no filter)
    DEALS_MAX_PRICE=0         skip games whose sale price is above this (0 = no limit)
    DEALS_STORES=1,7,25       CheapShark store ids (1 Steam, 7 GOG, 25 Epic)
    DEALS_MINUTES=60          how often to check (minimum 15)
"""
import asyncio
import json
import os
from typing import Optional

import discord
from discord import app_commands

API = "https://www.cheapshark.com/api/1.0"
HERE = os.path.dirname(os.path.abspath(__file__))
INITIAL_POSTS = 5
MAX_PER_CYCLE = 10
_store_names = {}


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


def _num(name, default):
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return float(default)


def _seen_path():
    return os.path.join(os.environ.get("DATA_DIR") or HERE, "deals_seen.json")


def load_seen():
    try:
        with open(_seen_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_seen(d):
    with open(_seen_path(), "w", encoding="utf-8") as f:
        json.dump(d, f)


def fetch_deals():
    import requests
    h = {"User-Agent": "gogo-discord-bot"}
    try:
        if not _store_names:
            for s in requests.get(f"{API}/stores", headers=h, timeout=20).json():
                _store_names[str(s.get("storeID"))] = s.get("storeName", "")
        r = requests.get(f"{API}/deals", headers=h, timeout=20,
                         params={"storeID": _env("DEALS_STORES", "1,7,25"), "onSale": 1,
                                 "sortBy": "Recent", "pageSize": 60})
        data = r.json() if r.status_code == 200 else []
    except Exception:
        return []
    return data if isinstance(data, list) else []


def wanted(d):
    try:
        if float(d.get("savings", 0)) < _num("DEALS_MIN_DISCOUNT", 80):
            return False
        rating, min_rating = int(d.get("steamRatingPercent") or 0), _num("DEALS_MIN_RATING", 70)
        if min_rating and rating and rating < min_rating:
            return False
        max_price = _num("DEALS_MAX_PRICE", 0)
        if max_price and float(d.get("salePrice", 0)) > max_price:
            return False
        return True
    except (TypeError, ValueError):
        return False


def key_of(d):
    return f"{d.get('gameID')}:{d.get('salePrice')}:{d.get('storeID')}"


def make_embed(d):
    store = _store_names.get(str(d.get("storeID")), "")
    pct = int(float(d.get("savings", 0)))
    emb = discord.Embed(title=str(d.get("title", "Deal"))[:250], color=0x2ECC71,
                        description=f"~~${d.get('normalPrice')}~~ → **${d.get('salePrice')}**  (**-{pct}%**)",
                        url=f"https://www.cheapshark.com/redirect?dealID={d.get('dealID')}")
    if store:
        emb.add_field(name="Store", value=store, inline=True)
    if d.get("steamRatingText") and d.get("steamRatingPercent"):
        emb.add_field(name="Steam rating", value=f"{d['steamRatingText']} ({d['steamRatingPercent']}%)", inline=True)
    elif d.get("metacriticScore") and str(d["metacriticScore"]) != "0":
        emb.add_field(name="Metacritic", value=str(d["metacriticScore"]), inline=True)
    if d.get("steamAppID") and str(d.get("storeID")) == "1":
        emb.set_image(url=f"https://cdn.cloudflare.steamstatic.com/steam/apps/{d['steamAppID']}/header.jpg")
    elif d.get("thumb"):
        emb.set_thumbnail(url=d["thumb"])
    emb.set_footer(text="Data: CheapShark (cheapshark.com)")
    return emb


def make_view(d):
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Get the deal",
                                    url=f"https://www.cheapshark.com/redirect?dealID={d.get('dealID')}"))
    return view


async def post_new(bot, only_guild=None):
    items = [d for d in await asyncio.to_thread(fetch_deals) if wanted(d) and d.get("dealID")]
    if not items:
        return 0
    items.sort(key=lambda d: int(d.get("lastChange") or 0))          # oldest first
    seen, total = load_seen(), 0
    for gid, cfg in list(bot.config.items()):
        dl = cfg.get("deals") if isinstance(cfg, dict) else None
        if not dl or not dl.get("channel_id"):
            continue
        if only_guild is not None and str(only_guild) != gid:
            continue
        guild = bot.get_guild(int(gid))
        ch = guild.get_channel(int(dl["channel_id"])) if guild else None
        if ch is None:
            continue
        known = list(seen.get(gid, []))
        fresh = [d for d in items if key_of(d) not in known]
        first = gid not in seen
        send = fresh[-INITIAL_POSTS:] if first else fresh[:MAX_PER_CYCLE]
        done = [key_of(d) for d in fresh if d not in send] if first else []
        for d in send:
            try:
                ping = f"<@&{dl['ping_role_id']}>" if dl.get("ping_role_id") else None
                await ch.send(content=ping, embed=make_embed(d), view=make_view(d))
                done.append(key_of(d))
                total += 1
                await asyncio.sleep(1)
            except discord.HTTPException as e:
                bot.log(f"[deals] cannot post in {guild.name}: {e} (needs Send Messages + Embed Links)")
                break
        seen[gid] = (known + done)[-400:]
    save_seen(seen)
    return total


async def loop(bot):
    await bot.wait_until_ready()
    raw = _env("DEALS_MINUTES", "60")
    minutes = max(15, int(raw)) if raw.isdigit() else 60
    bot.log(f"[deals] checking every {minutes} min")
    while not bot.is_closed():
        try:
            n = await post_new(bot)
            if n:
                bot.log(f"[deals] posted {n} new")
        except Exception as e:
            bot.log(f"[deals] error: {e}")
        await asyncio.sleep(minutes * 60)


def register(bot):
    @bot.tree.command(name="deals", description="Set the channel for big game discounts (empty = turn off)")
    @app_commands.describe(channel="Channel for game deals (leave empty to turn off)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def deals_cmd(inter: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        if not inter.user.guild_permissions.manage_guild:
            await inter.response.send_message("You need the Manage Server permission.", ephemeral=True)
            return
        cfg = bot.config.setdefault(str(inter.guild_id), {})
        if channel is None:
            cfg.pop("deals", None)
            bot.persist()
            await inter.response.send_message("Deal alerts are off.", ephemeral=True)
            return
        cfg["deals"] = {**(cfg.get("deals") or {}), "channel_id": channel.id}
        bot.persist()
        await inter.response.send_message(f"Done: big discounts will be posted in {channel.mention}.", ephemeral=True)
        asyncio.create_task(post_new(bot, only_guild=inter.guild_id))

    started = {"v": False}
    original_on_ready = bot.on_ready

    async def on_ready():
        await original_on_ready()
        if not started["v"]:
            started["v"] = True
            asyncio.create_task(loop(bot))

    bot.on_ready = on_ready
