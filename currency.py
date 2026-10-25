"""Currency converter menu (MAD, EUR, USD, GBP, CHF... any of ~160 currencies).

  /currency setup #channel    (Manage Server) posts the panel: live rates + a menu of popular conversions + a Convert button
  /currency convert amount from to   (public:True to show everybody)
  /currency off               forget the panel

The panel refreshes its rates every 3 hours. Data: ExchangeRate-API "open access" (https://www.exchangerate-api.com),
free, no key, updated once a day, so we ask it at most every 3 hours; the attribution is shown in every answer.
"""
import asyncio
import datetime
import os
import re
import time
from typing import Optional

import discord
from discord import app_commands

API = "https://open.er-api.com/v6/latest/EUR"
CACHE_SECONDS = 10800
_state = {"time": 0, "data": None}
_share_last = {}

FLAGS = {"EUR": "🇪🇺", "USD": "🇺🇸", "GBP": "🇬🇧", "MAD": "🇲🇦", "CHF": "🇨🇭", "SEK": "🇸🇪", "NOK": "🇳🇴", "DKK": "🇩🇰", "PLN": "🇵🇱",
         "CZK": "🇨🇿", "HUF": "🇭🇺", "RON": "🇷🇴", "BGN": "🇧🇬", "TRY": "🇹🇷", "AED": "🇦🇪", "SAR": "🇸🇦", "CAD": "🇨🇦", "AUD": "🇦🇺",
         "JPY": "🇯🇵", "CNY": "🇨🇳", "DZD": "🇩🇿", "TND": "🇹🇳", "EGP": "🇪🇬", "UAH": "🇺🇦", "ISK": "🇮🇸", "RUB": "🇷🇺", "INR": "🇮🇳",
         "BRL": "🇧🇷", "QAR": "🇶🇦", "KWD": "🇰🇼", "XOF": "🇸🇳", "MRU": "🇲🇷"}
NAMES = {"EUR": "Euro", "USD": "US dollar", "GBP": "British pound", "MAD": "Moroccan dirham", "CHF": "Swiss franc", "SEK": "Swedish krona",
         "NOK": "Norwegian krone", "DKK": "Danish krone", "PLN": "Polish zloty", "CZK": "Czech koruna", "HUF": "Hungarian forint",
         "RON": "Romanian leu", "BGN": "Bulgarian lev", "TRY": "Turkish lira", "AED": "UAE dirham", "SAR": "Saudi riyal", "CAD": "Canadian dollar",
         "AUD": "Australian dollar", "JPY": "Japanese yen", "CNY": "Chinese yuan", "DZD": "Algerian dinar", "TND": "Tunisian dinar",
         "EGP": "Egyptian pound", "UAH": "Ukrainian hryvnia", "ISK": "Icelandic krona"}
PAIRS = [("EUR", "MAD"), ("MAD", "EUR"), ("USD", "MAD"), ("MAD", "USD"), ("GBP", "MAD"), ("MAD", "GBP"), ("CHF", "MAD"), ("MAD", "CHF"),
         ("EUR", "USD"), ("EUR", "GBP"), ("USD", "EUR"), ("GBP", "EUR"), ("EUR", "TRY"), ("EUR", "DZD"), ("EUR", "TND")]
BOARD = [("EUR", "MAD"), ("USD", "MAD"), ("GBP", "MAD"), ("CHF", "MAD"), ("EUR", "USD"), ("EUR", "GBP")]


def flag(code):
    return FLAGS.get(code, "💱")


def get_rates():
    """{'rates': {...}, 'updated': 'Sat, 17 Oct 2026 00:02'}. Cached for 3 hours; if the service fails the old data is used."""
    import requests
    if _state["data"] and time.time() - _state["time"] < CACHE_SECONDS:
        return _state["data"]
    try:
        r = requests.get(API, headers={"User-Agent": "gogo-discord-bot"}, timeout=15)
        r.raise_for_status()
        j = r.json()
        if j.get("result") != "success" or "rates" not in j:
            raise RuntimeError("bad answer")
        _state["data"] = {"rates": j["rates"], "updated": str(j.get("time_last_update_utc", ""))[:22]}
        _state["time"] = time.time()
    except Exception:
        if not _state["data"]:
            raise
        _state["time"] = time.time() - CACHE_SECONDS + 600          # try again in 10 minutes
    return _state["data"]


def convert(amount, a, b, rates):
    return amount * rates[b] / rates[a]


def parse_amount(text):
    t = re.sub(r"[\s\u00a0']", "", str(text or ""))
    if not t or not re.fullmatch(r"[0-9.,]+", t):
        return None
    if "," in t and "." in t:                                    # the LAST separator is the decimal one
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        t = t.replace("." if dec == "," else ",", "").replace(dec, ".")
    else:
        sep = "," if "," in t else "." if "." in t else None
        if sep:
            head, _, tail = t.partition(sep)
            many = t.count(sep) > 1
            if many or (len(tail) == 3 and head not in ("", "0") and len(head) <= 3):   # 1.500 / 1,500 = one thousand five hundred
                t = t.replace(sep, "")
            else:
                t = t.replace(sep, ".")
    try:
        v = float(t)
    except ValueError:
        return None
    return v if 0 < v < 1e12 else None


def fmt(x):
    if abs(x) >= 1:
        return f"{x:,.2f}".replace(",", " ")
    return f"{x:.6f}".rstrip("0").rstrip(".") or "0"


def rate_text(a, b, rates):
    r = convert(1, a, b, rates)
    return f"1 {a} = {r:,.4f} {b}".replace(",", " ") if r >= 0.01 else f"1 {a} = {r:.6f} {b}"


def result_embed(amount, a, b, info):
    rates = info["rates"]
    out = convert(amount, a, b, rates)
    emb = discord.Embed(title=f"{flag(a)} {a}  →  {flag(b)} {b}", color=0x2ECC71,
                        description=f"## {fmt(amount)} {a}\n= **{fmt(out)} {b}**")
    emb.add_field(name="Rate", value=f"`{rate_text(a, b, rates)}`", inline=True)
    emb.add_field(name="Inverse", value=f"`{rate_text(b, a, rates)}`", inline=True)
    emb.set_footer(text=f"Rates by Exchange Rate API • updated {info['updated']} UTC")
    return emb


def panel_embed(info):
    emb = discord.Embed(title="💱  Currency converter", color=0x2ECC71,
                        description="Pick a **popular conversion** in the menu, or press **Convert** to type any amount and any "
                                    "currency (MAD, EUR, USD, GBP, CHF, TRY... about 160 supported).")
    if info:
        lines = [f"{flag(a)} **{a}** → {flag(b)} **{b}**   `{convert(1, a, b, info['rates']):,.4f}`".replace(",", " ")
                 for a, b in BOARD if a in info["rates"] and b in info["rates"]]
        half = (len(lines) + 1) // 2
        emb.add_field(name="📈 Live rates", value="\n".join(lines[:half]), inline=True)
        if lines[half:]:
            emb.add_field(name="\u200b", value="\n".join(lines[half:]), inline=True)
        emb.set_footer(text=f"Rates by Exchange Rate API • updated {info['updated']} UTC • refreshed every 3 h")
    emb.timestamp = datetime.datetime.now(datetime.timezone.utc)
    return emb


# ---------------------------------------------------------------- menus
async def show_result(interaction, amount, a, b, public=False, edit=False):
    try:
        info = await asyncio.to_thread(get_rates)
    except Exception:
        await interaction.response.send_message("❌ The rates service did not answer. Try again in a minute.", ephemeral=True)
        return
    if a not in info["rates"] or b not in info["rates"]:
        bad = a if a not in info["rates"] else b
        await interaction.response.send_message(f"❌ I do not know the currency **{bad}**. Use 3-letter codes like EUR or MAD.", ephemeral=True)
        return
    await interaction.response.send_message(embed=result_embed(amount, a, b, info), view=ResultView(amount, a, b), ephemeral=not public)


def make_modal(a="EUR", b="MAD", amount="100"):
    modal = discord.ui.Modal(title="💱 Convert")
    f_amount = discord.ui.TextInput(label="Amount", default=str(amount), max_length=20, required=True)
    f_from = discord.ui.TextInput(label="From (3-letter code)", default=a, min_length=3, max_length=3, required=True)
    f_to = discord.ui.TextInput(label="To (3-letter code)", default=b, min_length=3, max_length=3, required=True)
    for f in (f_amount, f_from, f_to):
        modal.add_item(f)

    async def on_submit(interaction):
        value = parse_amount(f_amount.value)
        if value is None:
            await interaction.response.send_message("❌ I could not read that amount. Example: 250 or 1 500,50", ephemeral=True)
            return
        await show_result(interaction, value, f_from.value.strip().upper(), f_to.value.strip().upper())

    modal.on_submit = on_submit
    return modal


class PairSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(custom_id="gogo:fx:pair", placeholder="⚡ Popular conversions", min_values=1, max_values=1,
                         options=[discord.SelectOption(label=f"{a} → {b}", value=f"{a}:{b}", emoji=flag(a),
                                                       description=f"{NAMES.get(a, a)} to {NAMES.get(b, b)}"[:100]) for a, b in PAIRS])

    async def callback(self, interaction):
        a, b = self.values[0].split(":")
        await interaction.response.send_modal(make_modal(a, b))


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(PairSelect())
        btn = discord.ui.Button(label="Convert any amount", emoji="💱", style=discord.ButtonStyle.success, custom_id="gogo:fx:convert")
        btn.callback = self.on_convert
        self.add_item(btn)

    async def on_convert(self, interaction):
        await interaction.response.send_modal(make_modal())


class ResultView(discord.ui.View):
    def __init__(self, amount, a, b):
        super().__init__(timeout=600)
        self.amount, self.a, self.b = amount, a, b
        for label, emoji, style, cb in (("Swap", "🔁", discord.ButtonStyle.secondary, self.on_swap),
                                        ("Other amount", "✏️", discord.ButtonStyle.secondary, self.on_other),
                                        ("Share", "📢", discord.ButtonStyle.primary, self.on_share)):
            b_ = discord.ui.Button(label=label, emoji=emoji, style=style)
            b_.callback = cb
            self.add_item(b_)

    async def on_swap(self, interaction):
        await show_result(interaction, self.amount, self.b, self.a)

    async def on_other(self, interaction):
        await interaction.response.send_modal(make_modal(self.a, self.b, f"{self.amount:.2f}".rstrip("0").rstrip(".")))

    async def on_share(self, interaction):
        key = (interaction.guild_id, interaction.user.id)
        if time.time() - _share_last.get(key, 0) < 30:
            await interaction.response.send_message("Please wait a little before sharing again.", ephemeral=True)
            return
        _share_last[key] = time.time()
        await show_result(interaction, self.amount, self.a, self.b, public=True)


# ---------------------------------------------------------------- panel refresh
async def refresh_panels(bot):
    try:
        info = await asyncio.to_thread(get_rates)
    except Exception as e:
        bot.log(f"[currency] {type(e).__name__}: {e}")
        return
    for gid, cfg in list(bot.config.items()):
        c = cfg.get("currency") if isinstance(cfg, dict) else None
        guild = bot.get_guild(int(gid)) if c else None
        ch = guild.get_channel(int(c["channel_id"])) if guild else None
        if ch is None:
            continue
        try:
            msg = await ch.fetch_message(int(c["message_id"]))
            await msg.edit(embed=panel_embed(info))
        except discord.HTTPException as e:
            bot.log(f"[currency] cannot update the panel in {guild.name}: {e} (run /currency setup again)")


async def loop(bot):
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await refresh_panels(bot)
        except Exception as e:
            bot.log(f"[currency] error: {e}")
        await asyncio.sleep(CACHE_SECONDS)


async def install(bot, guild, channel):
    """Post the currency panel in `channel` and remember it (used by the automatic setup)."""
    try:
        info = await asyncio.to_thread(get_rates)
    except Exception:
        info = None
    msg = await channel.send(embed=panel_embed(info), view=PanelView())
    bot.config.setdefault(str(guild.id), {})["currency"] = {"channel_id": channel.id, "message_id": msg.id}
    bot.persist()
    return msg


def register(bot):
    group = app_commands.Group(name="currency", description="Currency converter", guild_only=True)

    @group.command(name="setup", description="(Admin) Post the currency converter in a channel")
    @app_commands.describe(channel="The currency channel")
    async def setup(inter: discord.Interaction, channel: discord.TextChannel):
        if not inter.user.guild_permissions.manage_guild:
            await inter.response.send_message("You need the Manage Server permission.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        try:
            info = await asyncio.to_thread(get_rates)
        except Exception:
            info = None
        try:
            msg = await channel.send(embed=panel_embed(info), view=PanelView())
        except discord.HTTPException as e:
            await inter.followup.send(f"❌ I could not post in {channel.mention}: {e}", ephemeral=True)
            return
        bot.config.setdefault(str(inter.guild_id), {})["currency"] = {"channel_id": channel.id, "message_id": msg.id}
        bot.persist()
        await inter.followup.send(f"✅ The currency converter is live in {channel.mention}.", ephemeral=True)

    @group.command(name="off", description="(Admin) Stop updating the currency panel")
    async def off(inter: discord.Interaction):
        if not inter.user.guild_permissions.manage_guild:
            await inter.response.send_message("You need the Manage Server permission.", ephemeral=True)
            return
        (bot.config.get(str(inter.guild_id)) or {}).pop("currency", None)
        bot.persist()
        await inter.response.send_message("Done. You can delete the panel message.", ephemeral=True)

    @group.command(name="convert", description="Convert an amount between two currencies")
    @app_commands.describe(amount="Amount, e.g. 250 or 1 500,50", source="From, e.g. EUR", target="To, e.g. MAD", public="Show everybody")
    async def convert_cmd(inter: discord.Interaction, amount: str, source: str = "EUR", target: str = "MAD", public: bool = False):
        value = parse_amount(amount)
        if value is None:
            await inter.response.send_message("❌ I could not read that amount. Example: 250 or 1 500,50", ephemeral=True)
            return
        await show_result(inter, value, source.strip().upper(), target.strip().upper(), public=public)

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
