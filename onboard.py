"""Smart onboarding: what GOGO does when it joins a NEW server (needs AUTO_SETUP=1 in .env).

1. It READS the server first: which welcome / verify / logs / weather / prayer / ... channels already exist.
2. It creates ONLY what is missing, in a tidy order, and never touches a channel it did not create:
      VERIFICATION → INFORMATION (welcome) → GOGO (ask-gogo) → UTILITIES (weather, prayer, currency, football)
      → FREE GAMES → LIVE → VOICE (create room) → GOGO LOGS (staff only)
   Existing features are left alone and the owner is told the one command to plug GOGO into them.
3. It posts the working panels (weather, prayer times, currency, football) and saves every setting.
4. It sends the server OWNER a clear report: what was created, what was found, and what to do next (/help, /get config...).
   If the owner has old log channels, a button lets him delete them in one click (only the owner can press it).

Verification is added only to a brand-new server (<= 5 members) that has no verification yet: it locks new members.
Install: add ("onboard", "smart setup when the bot joins a server") to the module list of run_bot.py.
"""
import asyncio
import os
import re
import unicodedata

import discord

# what to look for in the names of the channels that already exist (names are cleaned first: no emojis, no symbols)
PATTERNS = {
    "welcome": r"welcome|bienvenue|bienvenida|willkommen|benvenuto|arriv|مرحب|اهلا|أهلا|استقبال",
    "verify": r"verif|vérif|captcha|تحقق|توثيق|rules?\s*accept",
    "logs": r"\blogs?\b|audit|journal|\bsijill|سجل",
    "gogo": r"\bgogo\b",
    "weather": r"weather|meteo|météo|wetter|clima|طقس|ta9s|tiempo",
    "prayer": r"prayer|salat|salah|adhan|azan|athan|namaz|priere|prière|أذان|اذان|صلاة|صلوات",
    "currency": r"currency|exchange|forex|devise|change\b|عملات|صرف|moneda",
    "football": r"football|soccer|\bfoot\b|futbol|fútbol|كرة|ligue|laliga|premier",
    "freegames": r"free\s*games?|freebies|giveaways?|gratuit|jeux\s*gratuits|ألعاب\s*مجانية",
    "deals": r"\bdeals?\b|\bdiscounts?\b|\bpromos?\b|soldes|bons\s*plans|\boffers?\b|تخفيض|عروض",
    "live": r"\blive\b|stream|twitch|kick\b|مباشر|بث",
    "createroom": r"create|join\s*to\s*create|créer|creer|temp|crear|\+|إنشاء",
    "quran": r"quran|qur ?an|koran|coran|قرآن|قران|قرأن",
}
OK_VOICE = "createroom"


def norm(name):
    s = unicodedata.normalize("NFKC", name or "").lower()
    s = re.sub(r"[\u200d\ufe0f]", "", s)
    return re.sub(r"[^\w\u0600-\u06ff+]+", " ", s).strip()


def scan(guild):
    """{kind: [existing channels]} (text channels, or voice channels for the create-room kind)"""
    found = {}
    for kind, pat in PATTERNS.items():
        pool = guild.voice_channels if kind == OK_VOICE else guild.text_channels
        if kind == "quran":
            pool = list(guild.text_channels) + list(guild.voice_channels)
        found[kind] = [c for c in pool if re.search(pat, norm(c.name))]
    return found


def quran_possible():
    """The radio needs the quran.py module and PyNaCl (voice) on the host."""
    try:
        import quran  # noqa: F401
        import nacl  # noqa: F401
        return True
    except ImportError:
        return False


def decide(found, fresh, football_ok, quran_ok=False):
    """What to create. A feature that already exists is never created again."""
    c = {k: not found[k] for k in ("welcome", "verify", "weather", "prayer", "currency", "football", "freegames", "deals", "live", "createroom")}
    c["verify"] = c["verify"] and fresh                       # verification locks new members: new servers only
    c["football"] = c["football"] and football_ok             # the panel needs the operator's football key
    c["quran"] = (not found["quran"]) and quran_ok
    c["gogo"] = not found["gogo"]
    c["logs"] = True                                          # GOGO's own staff-only logs, always its own channels
    return c


# names of the channels GOGO creates
N = {"verify": "✅・verify", "welcome": "👋・welcome", "gogo": "🤖・ask-gogo", "weather": "🌦️・weather", "prayer": "🕌・prayer-times",
     "currency": "💱・currency", "football": "⚽・football", "freegames": "🎁・free-games", "deals": "💸・deals", "live": "🔴・live",
     "createroom": "➕・Create Room", "quran": "📖・quran", "quran_voice": "🕋・Quran Radio"}
LOGS = {"channel": "📋・logs", "members": "👥・member-logs", "messages": "💬・message-logs", "voice": "🎙️・voice-logs",
        "mod": "🛡️・mod-logs", "server": "🏗️・server-logs"}


def blueprint(guild, create, fresh, gogo_channel_name):
    hide = [{"target_type": "role", "target": "@everyone", "target_id": str(guild.default_role.id), "allow": 0, "deny": 1024}]
    if create["verify"]:
        hide.append({"target_type": "role", "target": "✅ Member", "target_id": "", "allow": 0, "deny": 1024})

    def txt(key, topic, readonly=True):
        return {"name": N[key], "type": "text", "readonly": readonly, "topic": topic}

    cats = []
    if create["verify"]:
        cats.append({"name": "✅ VERIFICATION", "unverified_only": True,
                     "channels": [txt("verify", "Press the button below to verify yourself and unlock the server.")]})
    if create["welcome"]:
        cats.append({"name": "📌 INFORMATION", "channels": [txt("welcome", "Welcome to the server.")]})
    if create["gogo"]:
        cats.append({"name": "🤖 GOGO", "channels": [txt("gogo", "Ask GOGO anything about the server.", readonly=False)]})
    util = [txt(k, t) for k, t in (("weather", "Weather menu for Europe and Morocco."), ("prayer", "Prayer times with a menu."),
                                   ("currency", "Currency converter."), ("football", "Football matches.")) if create[k]]
    if util:
        cats.append({"name": "🌍 UTILITIES", "channels": util})
    free = [txt(k, t) for k, t in (("freegames", "New free games, posted automatically."), ("deals", "Big game discounts.")) if create[k]]
    if free:
        cats.append({"name": "🎁 FREE GAMES", "channels": free})
    if create["live"]:
        cats.append({"name": "📺 LIVE", "channels": [txt("live", "Live alerts. Use /live add to add your Twitch or Kick channel.")]})
    if create["createroom"]:
        cats.append({"name": "🎧 VOICE", "channels": [{"name": N["createroom"], "type": "voice"}]})
    if create["quran"]:
        cats.append({"name": "📖 QURAN RADIO", "channels": [txt("quran", "Quran radio controls and the ayah of the day."),
                                                             {"name": N["quran_voice"], "type": "voice"}]})
    cats.append({"name": "📋 GOGO LOGS", "channels": [{"name": n, "type": "text", "topic": "Staff only.", "permission_overwrites": hide}
                                                       for n in LOGS.values()]})
    bp = {"roles": ([{"name": "🔒 Unverified", "color": "#95a5a6", "permissions": "none"},
                     {"name": "✅ Member", "color": "#2ecc71", "permissions": "member"}] if create["verify"] else []),
          "categories": cats, "logs": LOGS,
          "assistant": {"channel": gogo_channel_name, "member_role": "✅ Member" if create["verify"] else None}}
    if create["welcome"]:
        bp["welcome"] = {"channel": N["welcome"], "message": "Welcome to {server}, {user}! 👋" + (
            " Press **Verify me** in #verify to unlock the server. 🎉" if create["verify"] else " 🎉")}
        if create["verify"]:
            bp["welcome"]["auto_role"] = "🔒 Unverified"
    if create["verify"]:
        bp["verification"] = {"channel": N["verify"], "button_label": "Verify me",
                              "text": "Welcome! 👋 Press the button below to verify yourself and unlock the whole server.",
                              "unverified_role": "🔒 Unverified", "verified_role": "✅ Member"}
    if create["createroom"]:
        bp["custom_vc"] = {"trigger": N["createroom"], "category": "🎧 VOICE"}
    return bp


HOW = {"welcome": "kept as it is, GOGO did not add a second welcome",
       "verify": "another verification already exists",
       "weather": "to use GOGO's weather menu there: `/weather setup #channel`",
       "prayer": "to use GOGO's prayer times there: `/prayer setup #channel`",
       "currency": "to use GOGO's converter there: `/currency setup #channel`",
       "football": "to use GOGO's football menu there: `/football setup #channel`",
       "freegames": "to use GOGO there: `/freegames #channel`", "deals": "to use GOGO there: `/deals #channel`",
       "live": "to use GOGO there: `/live setup #channel`", "createroom": "private rooms already exist",
       "quran": "to use GOGO's Quran radio there: `/quran setup`"}
LABEL = {"welcome": "Welcome", "verify": "Verification", "weather": "Weather", "prayer": "Prayer times", "currency": "Currency",
         "football": "Football", "freegames": "Free games", "deals": "Deals", "live": "Live alerts", "createroom": "Create-room voice",
         "quran": "Quran radio"}


class OldLogsView(discord.ui.View):
    """One button, only for the server owner: delete the old log channels that were found."""

    def __init__(self, guild, channels):
        super().__init__(timeout=86400)
        self.guild, self.ids = guild, [c.id for c in channels]
        b = discord.ui.Button(label="Delete my old log channels", emoji="🗑️", style=discord.ButtonStyle.danger)
        b.callback = self.on_delete
        self.add_item(b)

    async def on_delete(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            await interaction.response.send_message("Only the server owner can do this.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        done, failed = [], []
        for cid in self.ids:
            ch = self.guild.get_channel(cid)
            if ch is None:
                continue
            try:
                await ch.delete(reason="GOGO onboarding: the owner asked to delete the old log channels")
                done.append(ch.name)
            except discord.HTTPException:
                failed.append(ch.name)
        self.clear_items()
        await interaction.followup.send("🗑️ Deleted: " + (", ".join(done) or "nothing") + (f"\n⚠️ Could not delete: {', '.join(failed)}" if failed else ""),
                                        ephemeral=True)


def report(guild, created, found, create, notes, old_logs):
    emb = discord.Embed(title=f"👋 GOGO is ready in {guild.name}", color=0x57F287,
                        description="I read your server first and created **only what was missing**. Nothing of yours was touched.")
    if created:
        emb.add_field(name="✅ Created", value="\n".join(created)[:1000], inline=False)
    kept = [f"• **{LABEL[k]}**: {', '.join('#' + c.name for c in found[k][:2])} → {HOW[k]}" for k in LABEL if not create.get(k) and found.get(k)]
    if kept:
        emb.add_field(name="↪️ Already in your server (left as is)", value="\n".join(kept)[:1000], inline=False)
    if notes:
        emb.add_field(name="ℹ️ Notes", value="\n".join(notes)[:1000], inline=False)
    if old_logs:
        emb.add_field(name="🧹 Your old log channels", value=", ".join("#" + c.name for c in old_logs[:8])
                      + "\nI left them alone. GOGO now writes its own logs in **GOGO LOGS**. If you do not need the old ones, use the button below.",
                      inline=False)
    emb.add_field(name="🚀 Next steps",
                  value="• **`/help`**: every command explained\n• **`/get config`**: choose the roles members can claim\n"
                        "• **`/smartmod log`**: try AI moderation safely\n• **Move my role to the top** (Server Settings → Roles) so I can manage roles\n"
                        "• Tell me things in plain words: *@GOGO ...* (owner only)", inline=False)
    emb.set_footer(text="You are receiving this because you own the server where GOGO was added.")
    return emb


async def deliver(bot, guild, emb, view, gogo_channel):
    """DM the owner; if his DMs are closed, post in ask-gogo (or the system channel)."""
    owner = guild.owner
    if owner is None and guild.owner_id:
        try:
            owner = await guild.fetch_member(guild.owner_id)
        except discord.HTTPException:
            owner = None
    try:
        if owner is not None:
            await owner.send(embed=emb, **({"view": view} if view else {}))
            return "dm"
    except discord.HTTPException:
        pass
    target = gogo_channel or guild.system_channel
    if target is not None:
        try:
            await target.send(content=owner.mention if owner else None, embed=emb, **({"view": view} if view else {}))
            return "channel"
        except discord.HTTPException:
            pass
    return "none"


async def run(bot, guild):
    """The whole onboarding of one server."""
    if bot.config.get(str(guild.id)):
        print(f"[onboard] {guild.name}: already configured, skipping")
        return None
    fresh = (guild.member_count or 0) <= 5
    football_ok = bool(os.environ.get("FOOTBALL_DATA_KEY") or _env_file("FOOTBALL_DATA_KEY"))
    found = scan(guild)
    create = decide(found, fresh, football_ok, quran_possible())
    gogo_name = found["gogo"][0].name if found["gogo"] else N["gogo"]
    print(f"[onboard] {guild.name} ({guild.id}): fresh={fresh}, creating: {', '.join(k for k, v in create.items() if v)}")
    bp = blueprint(guild, create, fresh, gogo_name)
    try:
        await bot.apply_blueprint(guild.id, bp, wipe=False, keep_roles=True, keep_channels=True, keep_permissions=True,
                                  add_missing_roles=True, add_missing_channels=True)
    except Exception as e:
        print(f"[onboard] {guild.name}: FAILED: {e}  (give the bot Administrator and move its role to the top)")
        return None
    await asyncio.sleep(2)
    try:
        channels = {c.name: c for c in await guild.fetch_channels()}
    except discord.HTTPException:
        channels = {c.name: c for c in guild.channels}
    created, notes = [], []
    cfg = bot.config.setdefault(str(guild.id), {})
    for key in ("freegames", "deals", "live"):                        # simple "post here" settings
        ch = channels.get(N[key])
        if create[key] and ch is not None:
            cfg[key] = {"channel_id": ch.id}
            created.append(f"• {ch.mention} ({LABEL[key]})")
    bot.persist()
    for key, modname in (("weather", "weather"), ("prayer", "prayer"), ("currency", "currency"), ("football", "football")):
        ch = channels.get(N[key])
        if not (create[key] and ch is not None):
            continue
        try:
            mod = __import__(modname)
            await mod.install(bot, guild, ch)
            created.append(f"• {ch.mention} ({LABEL[key]} panel)")
        except Exception as e:
            notes.append(f"{LABEL[key]}: the panel could not be posted ({type(e).__name__}). Run `/{modname} setup #{ch.name}`.")
            print(f"[onboard] {guild.name}: {modname} panel failed: {e}")
    qt, qv = channels.get(N["quran"]), channels.get(N["quran_voice"])
    if create["quran"] and qt is not None and qv is not None:
        try:
            import quran
            problem = await quran.install(bot, guild, qt, qv)
            created.append(f"• {qt.mention} + {qv.mention} (Quran radio, 24/7)")
            if problem:
                notes.append(f"Quran radio: created, but it could not start yet ({problem}). Open the panel to retry.")
        except Exception as e:
            notes.append(f"Quran radio: the channels exist but the panel failed ({type(e).__name__}). Run `/quran setup`.")
            print(f"[onboard] {guild.name}: quran failed: {e}")
    for key in ("verify", "welcome", "createroom"):
        ch = channels.get(N[key])
        if create[key] and ch is not None:
            created.append(f"• {ch.mention} ({LABEL[key]})")
    g = channels.get(gogo_name)
    if create["gogo"] and g is not None:
        created.append(f"• {g.mention} (Ask GOGO: AI assistant)")
    created.append("• **GOGO LOGS**: staff-only logs (members, messages, voice, moderation, server)")
    if not create["football"] and not found["football"] and not football_ok:
        notes.append("Football menu: not available on this bot yet (no football key configured by the bot's operator).")
    if not fresh and not found["verify"]:
        notes.append("Verification was **not** added because your server already has members (it would lock them). Tell me if you want it.")
    old_logs = [c for c in found["logs"] if c.name not in LOGS.values()]
    emb = report(guild, created, found, create, notes, old_logs)
    sent = await deliver(bot, guild, emb, OldLogsView(guild, old_logs) if old_logs else None, g)
    print(f"[onboard] {guild.name}: done, report sent by {sent}")
    return {"created": created, "found": {k: [c.name for c in v] for k, v in found.items() if v}, "sent": sent}


def _env_file(name):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if line.strip().startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def register(bot):
    """Replaces the old automatic setup of run_bot.py (it keeps working for the AUTO_SETUP=1 switch)."""
    async def _auto_setup(guild):
        await run(bot, guild)

    bot._auto_setup = _auto_setup
