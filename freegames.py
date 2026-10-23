"""Free-games alerts for the bot (like FreeStuff): posts every NEW free game / giveaway
into a channel. Data: GamerPower (https://www.gamerpower.com) - free API, attribution required
(it is shown in every post's footer).

Per server it only needs  guild_config.json -> "<server id>": {"freegames": {"channel_id": 123}}
Set it with the slash command  /freegames #channel  (needs Manage Server), or in the server JSON:
    "freegames": {"channel": "🎁・free-games"}

.env options (all optional):
    FREEGAMES_MINUTES=30          how often to check (minimum 10)
    FREEGAMES_PLATFORMS=any       default: pc,steam,epic,gog,ubisoft,itch   (use 'any' for everything)
    FREEGAMES_TYPES=game,dlc      default: game   (game, dlc, early access, loot; 'any' = everything)

/freecheck [post_now]  (Manage Server)  shows what the feed returns right now, how many match your filters and how
many are new for this server; post_now=True posts the ones this server has not seen yet.
"""
import asyncio
import json
import os
import time
from typing import Optional

import discord
from discord import app_commands

FEED = "https://www.gamerpower.com/api/giveaways"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PLATFORMS = "pc,steam,epic,gog,ubisoft,itch"
INITIAL_POSTS = 5          # first time in a server: show the newest N, mark the rest as seen
MAX_PER_CYCLE = 10


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


def _seen_path():
    return os.path.join(os.environ.get("DATA_DIR") or HERE, "freegames_seen.json")


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


_last_status = None
STATS = {"time": 0, "status": "not checked yet", "fetched": 0, "matching": 0, "posted": 0}


def fetch_feed_info():
    """-> (items, status text). Items = every live giveaway (all types); the filters are applied later."""
    import requests
    try:
        r = requests.get(FEED, params={"sort-by": "date"}, headers={"User-Agent": "gogo-discord-bot"}, timeout=20)
    except Exception as e:
        return [], f"error: {type(e).__name__}"
    if r.status_code != 200:
        return [], f"GamerPower answered HTTP {r.status_code}"
    try:
        data = r.json()
    except ValueError:
        return [], "GamerPower sent something that is not JSON"
    if not isinstance(data, list):                      # "no active giveaways" comes back as an object
        return [], "GamerPower says there are no active giveaways"
    return data, "ok"


def fetch_feed():
    return fetch_feed_info()[0]


def wanted(item):
    if str(item.get("status", "Active")).lower() != "active":
        return False
    types = _env("FREEGAMES_TYPES", "game").lower()
    if types != "any" and str(item.get("type", "")).lower() not in [t.strip() for t in types.split(",")]:
        return False
    flt = _env("FREEGAMES_PLATFORMS", DEFAULT_PLATFORMS).lower()
    if flt == "any":
        return True
    plat = str(item.get("platforms", "")).lower()
    return any(p.strip() and p.strip() in plat for p in flt.split(","))


def make_embed(item):
    worth = str(item.get("worth") or "").strip()
    price = f"~~{worth}~~ " if worth and worth.upper() != "N/A" else ""
    end = str(item.get("end_date") or "")[:10]
    desc = f"{price}**FREE**" + (f" until **{end}**" if end and end.upper() != "N/A" else " for a limited time")
    emb = discord.Embed(title=str(item.get("title", "Free game"))[:250], description=desc, color=0x5865F2,
                        url=item.get("open_giveaway_url") or item.get("gamerpower_url") or None)
    if item.get("image"):
        emb.set_image(url=item["image"])
    if item.get("platforms"):
        emb.add_field(name="Platforms", value=str(item["platforms"])[:200], inline=True)
    if item.get("type"):
        emb.add_field(name="Type", value=str(item["type"])[:50], inline=True)
    emb.set_footer(text="Data: GamerPower (gamerpower.com)")
    return emb


def make_view(item):
    url = item.get("open_giveaway_url") or item.get("gamerpower_url")
    if not url or len(url) > 500:
        return None
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Claim for free", url=url))
    return view


async def post_new(bot, only_guild=None):
    """Post the giveaways each configured server has not seen yet. Returns how many were posted."""
    global _last_status
    raw, status = await asyncio.to_thread(fetch_feed_info)
    items = [i for i in raw if wanted(i) and "id" in i]
    STATS.update(time=time.time(), status=status, fetched=len(raw), matching=len(items), posted=0)
    if status != "ok" and status != _last_status:        # tell the owner in the Console, once per problem
        bot.log(f"[freegames] problem with the feed: {status}")
    _last_status = status
    if not items:
        return 0
    items.sort(key=lambda i: str(i.get("published_date", "")))      # oldest first
    seen, total = load_seen(), 0
    for gid, cfg in list(bot.config.items()):
        fg = cfg.get("freegames") if isinstance(cfg, dict) else None
        if not fg or not fg.get("channel_id"):
            continue
        if only_guild is not None and str(only_guild) != gid:
            continue
        guild = bot.get_guild(int(gid))
        ch = guild.get_channel(int(fg["channel_id"])) if guild else None
        if ch is None:
            continue
        known = list(seen.get(gid, []))
        fresh = [i for i in items if str(i["id"]) not in known]
        if gid not in seen:                                         # first time in this server
            send = fresh[-INITIAL_POSTS:]
        else:
            send = fresh[:MAX_PER_CYCLE]
        done = [str(i["id"]) for i in fresh if i not in send] if gid not in seen else []
        for item in send:
            try:
                ping = f"<@&{fg['ping_role_id']}>" if fg.get("ping_role_id") else None
                await ch.send(content=ping, embed=make_embed(item), view=make_view(item))
                done.append(str(item["id"]))
                total += 1
                await asyncio.sleep(1)
            except discord.HTTPException as e:
                bot.log(f"[freegames] cannot post in {guild.name}: {e} (needs Send Messages + Embed Links)")
                break
        seen[gid] = (known + done)[-300:]
    save_seen(seen)
    STATS["posted"] = total
    return total


async def loop(bot):
    await bot.wait_until_ready()
    raw = _env("FREEGAMES_MINUTES", "30")
    minutes = max(10, int(raw)) if raw.isdigit() else 30
    bot.log(f"[freegames] checking every {minutes} min")
    while not bot.is_closed():
        try:
            n = await post_new(bot)
            if n:
                bot.log(f"[freegames] posted {n} new")
        except Exception as e:                  # never kill the loop
            bot.log(f"[freegames] error: {e}")
        await asyncio.sleep(minutes * 60)


def register(bot):
    """Adds /freegames and starts the background loop (call once, before bot.run)."""

    @bot.tree.command(name="freegames", description="Set the channel for free-game alerts (empty = turn off)")
    @app_commands.describe(channel="Channel where new free games are posted (leave empty to turn off)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def freegames_cmd(inter: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        if not inter.user.guild_permissions.manage_guild:
            await inter.response.send_message("You need the Manage Server permission.", ephemeral=True)
            return
        cfg = bot.config.setdefault(str(inter.guild_id), {})
        if channel is None:
            cfg.pop("freegames", None)
            bot.persist()
            await inter.response.send_message("Free-game alerts are off.", ephemeral=True)
            return
        cfg["freegames"] = {**(cfg.get("freegames") or {}), "channel_id": channel.id}
        bot.persist()
        await inter.response.send_message(f"Done: new free games will be posted in {channel.mention}.", ephemeral=True)
        asyncio.create_task(post_new(bot, only_guild=inter.guild_id))

    @bot.tree.command(name="freecheck", description="Free games: what the feed returns right now (Manage Server)")
    @app_commands.describe(post_now="Also post the giveaways this server has not seen yet")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def freecheck_cmd(inter: discord.Interaction, post_now: bool = False):
        if not inter.user.guild_permissions.manage_guild:
            await inter.response.send_message("You need the Manage Server permission.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        raw, status = await asyncio.to_thread(fetch_feed_info)
        items = [i for i in raw if wanted(i) and "id" in i]
        gid = str(inter.guild_id)
        fg = (bot.config.get(gid) or {}).get("freegames") or {}
        seen = load_seen().get(gid, [])
        new = [i for i in items if str(i["id"]) not in seen]
        lines = [f"Feed: **{status}**, {len(raw)} live giveaways, **{len(items)}** match the filters "
                 f"(types: `{_env('FREEGAMES_TYPES', 'game')}`, platforms: `{_env('FREEGAMES_PLATFORMS', DEFAULT_PLATFORMS)}`).",
                 "Channel: " + (f"<#{fg['channel_id']}>" if fg.get("channel_id") else "**not set**, use `/freegames #channel`"),
                 f"Not posted yet in this server: **{len(new)}**"]
        for i in sorted(items, key=lambda i: str(i.get("published_date", "")), reverse=True)[:3]:
            lines.append(f"• {str(i.get('title', ''))[:60]} ({i.get('type')}, {i.get('platforms')}), "
                         f"published {str(i.get('published_date', ''))[:16]}")
        if post_now:
            lines.append(f"Posted now: **{await post_new(bot, only_guild=inter.guild_id)}**")
        await inter.followup.send("\n".join(lines)[:1900], ephemeral=True)

    started = {"v": False}
    original_on_ready = bot.on_ready

    async def on_ready():
        await original_on_ready()
        if not started["v"]:
            started["v"] = True
            asyncio.create_task(loop(bot))

    bot.on_ready = on_ready