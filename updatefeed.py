
"""GOGO update announcements for the Hermanos server."""
import json
import os
import subprocess
from datetime import datetime, timezone

CHANNEL_NAME = "🆕・updates"
STATE_FILE = "updatefeed_state.json"
REPO_URL = "https://github.com/nigag193-prog/gogo.333/commits/main"


def _git(*args):
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=12,
        ).strip()
    except Exception:
        return ""


def _state_path():
    data_dir = os.environ.get("DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(data_dir, STATE_FILE)


def _read_state():
    try:
        with open(_state_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(data):
    path = _state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(temp, path)


def _find_channel(bot):
    for guild in bot.guilds:
        channel = next(
            (c for c in guild.text_channels if c.name == CHANNEL_NAME),
            None,
        )
        if channel:
            return channel
    return None


async def on_ready(bot):
    if getattr(bot, "_updatefeed_started", False):
        return
    bot._updatefeed_started = True

    channel = _find_channel(bot)
    if channel is None:
        print(f"[updates] Channel {CHANNEL_NAME} not found")
        return

    import discord

    now = datetime.now(timezone.utc)

    # Online announcement on every process start
    online = discord.Embed(
        title="🟢 GOGO is Online",
        description="GOGO.333 has restarted successfully.",
        colour=0x2ECC71,
        timestamp=now,
    )
    online.add_field(name="Status", value="Operational", inline=True)
    online.add_field(name="Server", value=channel.guild.name, inline=True)
    online.set_footer(text="GOGO.333 • System Status")

    try:
        await channel.send(embed=online)
        print(f"[updates] Online message sent to {CHANNEL_NAME}")
    except Exception as exc:
        print(f"[updates] Online announcement failed: {exc}")
        return

    current = _git("rev-parse", "HEAD")
    if not current:
        print("[updates] Git commit unavailable")
        return

    state = _read_state()
    previous = state.get("last_commit")

    # First run: save a baseline without announcing old commits
    if not previous:
        _write_state({
            "last_commit": current,
            "initialized_at": now.isoformat(),
        })
        print("[updates] Initial commit saved")
        return

    if previous == current:
        print("[updates] No new Git updates")
        return

    subjects = _git(
        "log", "--format=%s", f"{previous}..{current}"
    ).splitlines()

    changed = _git(
        "diff", "--name-only", f"{previous}..{current}"
    ).splitlines()

    embed = discord.Embed(
        title="🆕 GOGO.333 — New Update",
        description="A new version has been deployed from GitHub.",
        colour=0x5865F2,
        timestamp=now,
        url=REPO_URL,
    )

    if subjects:
        embed.add_field(
            name="📦 Changes",
            value="\n".join(f"• {s}" for s in subjects[:8])[:1024],
            inline=False,
        )

    if changed:
        embed.add_field(
            name=f"🛠️ Updated files ({len(changed)})",
            value="\n".join(f"`{p}`" for p in changed[:12])[:1024],
            inline=False,
        )

    embed.add_field(
        name="🔗 Repository",
        value=f"[View commits]({REPO_URL})",
        inline=False,
    )
    embed.set_footer(text=f"GOGO.333 • Commit {current[:7]}")

    try:
        await channel.send(embed=embed)
    except Exception as exc:
        print(f"[updates] Update announcement failed: {exc}")
        return

    _write_state({
        "last_commit": current,
        "announced_at": now.isoformat(),
    })
    print(f"[updates] Git update announced: {current[:7]}")
