"""/help: the complete, professional guide to every GOGO command (English).

  /help            opens an interactive guide (pick a topic in the menu)
  /help topic      jumps to one topic

Every command is explained: what it does, who can use it and how. The guide also scans the commands that are really
registered in the bot, so a new module is never missing: anything not described below appears under "More commands".
"""
import discord
from discord import app_commands

E, S, O = "🟢 Everyone", "🛡️ Staff", "👑 Owner"          # who can use it

# key -> (emoji, title, intro, [(usage, who, description), ...])
TOPICS = {
    "start": ("🚀", "Getting started",
              "**GOGO** is an all-in-one community bot: moderation, self-service roles, an economy, live information panels and "
              "an AI assistant that obeys the server owner in plain language.\n\n"
              "**Three ways to use it**\n"
              "• **Slash commands:** type `/` and pick a command (everything is listed in this guide).\n"
              "• **Ask GOGO:** write in the #ask-gogo channel (or mention @GOGO) to get answers about the server.\n"
              "• **Owner assistant:** the server owner can give orders in everyday words, for example "
              "*“@GOGO warn him”* (as a reply), *“mute @user 30 minutes”* or *“post the rules in #rules and pin them”*.\n\n"
              "**Admin quick start**\n"
              "1️⃣ Move GOGO's role **above** the roles it should manage (Server Settings → Roles).\n"
              "2️⃣ `/get config` to choose the self-service roles.  3️⃣ `/weather setup`, `/prayer setup`, `/currency setup`, "
              "`/football setup` in the channels you want.  4️⃣ `/smartmod log` to try AI moderation safely.", []),
    "economy": ("💰", "Economy & fun",
                "Members earn **coins** by chatting and with a daily bonus, then spend them in the shop. They also earn **XP** "
                "and levels automatically while talking.",
                [("/balance [member]", E, "Shows your coin balance, or another member's."),
                 ("/daily", E, "Claims your daily coins. It can be used once per day; streaks are not required."),
                 ("/shop", E, "Lists the items the server sells for coins, with their prices."),
                 ("/buy item", E, "Buys an item from the shop if you have enough coins; the item's reward is applied at once."),
                 ("/richest", E, "Displays the top 10 members by coins."),
                 ("/lfg", E, "Posts a *Looking for group* request to find teammates (the server must have an LFG channel set up)."),
                 ("!rank [@member]", E, "Shows level, XP and leaderboard position of you or the mentioned member."),
                 ("!top", E, "Shows the XP leaderboard of the server.")]),
    "roles": ("🎭", "Self-service roles",
              "Members choose their own roles from a menu. GOGO only offers roles that are safe: it never hands out administrator-like "
              "roles or roles above its own, and it re-checks every click.",
              [("/get role", E, "Opens a private menu: select a role to get it, or a role you already have to drop it."),
               ("/get config", "🛡️ Manage Roles", "**Scans every role of the server** and lets you choose which ones members may claim "
                "(browse pages, select, save). Dangerous or too-high roles are hidden automatically."),
               ("/get panel", "🛡️ Manage Roles", "Posts the public roles panel (a menu) in the current channel, using the roles you saved.")]),
    "mod": ("🛡️", "Moderation",
            "Manual tools for staff, plus protections that work on their own: anti-spam (invites, links, floods), an insult filter "
            "with escalating warnings (Warn 1 → 2 → 3 → timeout), and optional AI moderation that understands context.",
            [("!warn @user [reason]", S, "Records a warning (and applies the warning role). Needs *Moderate Members*."),
             ("!warns @user", S, "Lists the warnings of a member with their dates."),
             ("!mute @user [minutes]", S, "Times a member out (default 10 minutes). Needs *Moderate Members*."),
             ("!unmute @user", S, "Removes a timeout."),
             ("!kick @user [reason]", S, "Removes a member (they can rejoin). Needs *Kick Members*."),
             ("!ban @user [reason]", S, "Bans a member. Needs *Ban Members*."),
             ("!clear 1-100", S, "Deletes the last N messages of the channel. Needs *Manage Messages*."),
             ("/lockdown", "🛡️ Admin", "Locks every public text channel so nobody can write: an emergency brake during a raid."),
             ("/unlock", "🛡️ Admin", "Reverses the lockdown and reopens the channels."),
             ("/smartmod off|log|act", "🛡️ Manage Server", "AI moderation per server. **log** only reports what it would do (recommended first); "
              "**act** deletes the message, warns or mutes. It never bans by itself and never touches staff."),
             ("/appeal reason", E, "A member who thinks an AI moderation action was a mistake explains why; the staff can approve it in one click.")]),
    "owner": ("👑", "Owner assistant & automation",
              "The **server owner** (verified by Discord itself) can talk to GOGO in everyday language and it executes: moderation, roles, "
              "channels, announcements, voice. Everyone else gets a polite refusal. Address the bot with *@GOGO* or *gogo ...*, "
              "or write in #ask-gogo.",
              [("“warn him” (as a reply)", O, "Warns the author of the message you replied to."),
               ("“kick / ban / mute @user 30 min”", O, "Moderation by sentence. Role hierarchy and the owner are always protected."),
               ("“give @user role X for 1 hour”", O, "Temporary roles: GOGO removes the role by itself at the right time."),
               ("“at 9 pm post … in #news”", O, "Scheduled tasks, also recurring (daily / weekly). They survive restarts."),
               ("“say … in voice”", O, "Joins a voice channel and announces your sentence with text-to-speech, then leaves or stays."),
               ("“write the rules in #rules and pin”", O, "GOGO writes the text, posts it and pins it. It can also create, edit, lock or delete channels and roles."),
               ("“remember that …”", O, "Long-term memory: GOGO keeps the fact and uses it in later answers. Say *forget …* to remove it."),
               ("“add #info to your knowledge”", O, "Knowledge base: GOGO reads the channel and uses it to answer members' questions.")]),
    "voice": ("🔊", "Voice",
              "Voice features for communities that live in voice channels.",
              [("➕ Create Room (channel)", E, "Join the *Create Room* voice channel and GOGO makes you a private room named after you; it disappears when empty."),
               ("/stay [channel]", O, "Makes GOGO join a voice channel and **stay there 24/7**. It returns by itself if it is moved or disconnected."),
               ("/unstay", O, "GOGO leaves the voice channel and stops coming back.")]),
    "panels": ("🌍", "Information panels",
               "Beautiful, always-up-to-date panels with a menu. Each one is installed in the channel of your choice by an admin "
               "(*Manage Server*) and works for every member.",
               [("/weather setup #channel", "🛡️ Manage Server", "Posts the weather panel: pick a region then a city (Europe + Morocco). Cards look like the iOS Weather app."),
                ("/weather city name", E, "Weather of any city in the world, with details (UV, wind, pressure, moon, 7-day forecast)."),
                ("/prayer setup #channel", "🛡️ Manage Server", "Posts the prayer-times panel with an animated mosque card, next prayer and countdown."),
                ("/prayer city name", E, "Prayer times for any city; the button *Method* switches the calculation method."),
                ("/currency setup #channel", "🛡️ Manage Server", "Posts a currency converter with live rates and a menu of popular conversions (MAD, EUR, USD, GBP…)."),
                ("/currency convert amount from to", E, "Converts an amount between two currencies (about 160 supported)."),
                ("/football setup #channel", "🛡️ Manage Server", "Posts the football panel: today's matches, a competition menu and Today / Tomorrow / Live buttons."),
                ("/football today|tomorrow [competition]", E, "Matches of today or tomorrow, in your time zone."),
                ("/freegames #channel", "🛡️ Manage Server", "Posts every new free game automatically in that channel. Run it without a channel to turn it off."),
                ("/freecheck [post_now]", "🛡️ Manage Server", "Diagnostics: what the free-games feed returns now and what is not posted yet."),
                ("/deals #channel", "🛡️ Manage Server", "Posts big game discounts (80% and more) automatically."),
                ("/live setup #channel", "🛡️ Manage Server", "Chooses the channel for live-stream alerts."),
                ("/live add platform name", E, "Adds **your own** Twitch or Kick channel: GOGO announces you when you go live."),
                ("/live remove platform name  ·  /live list", E, "Removes your channel / lists the tracked channels."),
                ("/golive platform link", E, "Announces a live on Facebook, YouTube, TikTok or Instagram (these cannot be detected automatically)."),
                ("/weather off  ·  /prayer off  ·  /currency off  ·  /football off", "🛡️ Manage Server", "Stops updating the panel of that type.")]),
    "protect": ("🏰", "Server protection",
                "Owner-level safety tools. They are listed with the description registered in Discord; open the command list to see each option.",
                []),
}
ORDER = ["start", "economy", "roles", "mod", "owner", "voice", "panels", "protect"]
BADGE_OWNER = ("backup", "restore", "antinuke", "serverlock", "serverunlock", "stay", "unstay")


def _flatten(bot):
    """{qualified name: description} of every slash command registered in the bot"""
    out = {}
    for cmd in bot.tree.get_commands():
        if isinstance(cmd, app_commands.Group):
            for sub in cmd.walk_commands():
                if not isinstance(sub, app_commands.Group):
                    out[sub.qualified_name] = sub.description
        else:
            out[cmd.qualified_name] = cmd.description
    return out


def _covered():
    """top-level slash command names that are written in the guide"""
    names = set()
    for _, _, _, entries in TOPICS.values():
        for usage, _, _ in entries:
            for part in usage.split("·"):
                part = part.strip()
                if part.startswith("/"):
                    names.add(part[1:].split(" ")[0])
    return names


def build(bot, key):
    emoji, title, intro, entries = TOPICS[key]
    emb = discord.Embed(title=f"{emoji}  {title}", description=intro, color=0x5865F2)
    for usage, who, desc in entries[:24]:
        emb.add_field(name=f"`{usage}`", value=f"{who}\n{desc}"[:1024], inline=False)
    if key == "protect":
        flat = _flatten(bot)
        shown = [(n, d) for n, d in flat.items() if n.split(" ")[0] in BADGE_OWNER and n.split(" ")[0] not in ("stay", "unstay")]
        for n, d in shown[:20]:
            emb.add_field(name=f"`/{n}`", value=f"{O}\n{d or 'Owner-only protection tool.'}"[:1024], inline=False)
        if not shown:
            emb.add_field(name="Not installed", value="The protection module is not active on this bot.", inline=False)
    emb.set_footer(text="GOGO help • /help <topic> to jump • Staff = needs the matching Discord permission")
    return emb


def build_more(bot):
    """Commands that exist in the bot but are not written in the guide above."""
    flat = _flatten(bot)
    top_written = _covered()
    more = [(n, d) for n, d in flat.items() if n.split(" ")[0] not in top_written and n.split(" ")[0] not in BADGE_OWNER and n != "help"]
    emb = discord.Embed(title="📋  More commands", color=0x5865F2,
                        description="Commands registered in this bot that are not covered by the sections above.")
    for n, d in more[:24]:
        emb.add_field(name=f"`/{n}`", value=(d or "No description.")[:300], inline=False)
    if not more:
        emb.description = "Everything is already described in the other sections. ✅"
    return emb


class HelpView(discord.ui.View):
    def __init__(self, bot, current="start"):
        super().__init__(timeout=300)
        self.bot = bot
        sel = discord.ui.Select(placeholder="📖 Choose a topic", min_values=1, max_values=1,
                                options=[discord.SelectOption(label=TOPICS[k][1], value=k, emoji=TOPICS[k][0], default=k == current) for k in ORDER]
                                + [discord.SelectOption(label="More commands", value="more", emoji="📋", default=current == "more")])
        sel.callback = self.on_pick
        self.sel = sel
        self.add_item(sel)

    async def on_pick(self, interaction):
        key = self.sel.values[0]
        emb = build_more(self.bot) if key == "more" else build(self.bot, key)
        await interaction.response.edit_message(embed=emb, view=HelpView(self.bot, key))


def register(bot):
    choices = [app_commands.Choice(name=TOPICS[k][1], value=k) for k in ORDER] + [app_commands.Choice(name="More commands", value="more")]

    @bot.tree.command(name="help", description="Everything GOGO can do: every command explained")
    @app_commands.describe(topic="Jump straight to a topic")
    @app_commands.choices(topic=choices)
    async def help_cmd(inter: discord.Interaction, topic: app_commands.Choice[str] = None):
        key = topic.value if topic else "start"
        emb = build_more(bot) if key == "more" else build(bot, key)
        await inter.response.send_message(embed=emb, view=HelpView(bot, key), ephemeral=True)
