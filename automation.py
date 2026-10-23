"""Automation for GOGO (everything is ordered by the server OWNER in plain words, see owner_ai.py):

  * Scheduled tasks      "gogo f 12 dyal lil 9fel server w f 8 7ellou"   /   "kol nhar f 9 kteb f #annonces ..."
                         The planned actions are saved in automation.json and run at the time, even after a restart.
  * Temporary roles      "gogo 3tih @Ali rol VIP l 7 ayyam"              -> the role is removed by itself
  * Voice announcements  "gogo dkhol voice X w goul fiha ... w mn b3d mute / khroj"
                         GOGO joins the voice channel, says the sentence out loud (text-to-speech) and then
                         leaves, mutes itself or stays, as ordered.
  * "gogo raj3 akher 7aja" (undo) lives in owner_ai.py; the actions here register how to be taken back.

Needs (requirements-bot.txt):   edge-tts    (the voice)
                                imageio-ffmpeg    (only if ffmpeg is not installed on the host)
                                PyNaCl + davey    (voice, already needed for /stay)

.env options (all optional):
    TIMEZONE=Africa/Casablanca       the owner's time zone (names like Europe/Paris also work)
    GOGO_VOICE_AR=ar-MA-JamalNeural  voice per language (AR / FR / EN / ES), e.g. ar-MA-MounaNeural for a woman
    GOGO_VOICE_RATE=-6%              speaking speed (-15% slower ... +10% faster); slower = clearer
"""
import asyncio
import datetime
import json
import os
import re
import shutil
import tempfile
import threading
import time
import types

import discord

HERE = os.path.dirname(os.path.abspath(__file__))
VOICES = {"ar": "ar-MA-JamalNeural", "fr": "fr-FR-HenriNeural", "en": "en-US-GuyNeural", "es": "es-ES-AlvaroNeural"}
LATE_SECONDS = 7200            # a one-time task that is more than 2 h late (bot was off) is skipped, not run
_lock = threading.Lock()
_voice_locks = {}


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


# ---------------------------------------------------------------- time
def tz():
    name = _env("TIMEZONE", "Africa/Casablanca")
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        raw = _env("TZ_OFFSET_HOURS", "1")
        try:
            hours = int(raw)
        except ValueError:
            hours = 1
        return datetime.timezone(datetime.timedelta(hours=hours), name)


def now_line():
    now = datetime.datetime.now(tz())
    return f"NOW: {now:%Y-%m-%d %H:%M} ({now:%A}), time zone {_env('TIMEZONE', 'Africa/Casablanca')}"


def parse_when(s):
    """'YYYY-MM-DD HH:MM' (or just 'HH:MM' = next time it happens) in the owner's time zone -> (timestamp, error)"""
    s = str(s or "").strip().replace("T", " ")
    zone, now = tz(), datetime.datetime.now(tz())
    dt = None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.datetime.strptime(s, fmt).replace(tzinfo=zone)
            break
        except ValueError:
            pass
    if dt is None:
        try:
            t = datetime.datetime.strptime(s, "%H:%M")
            dt = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            if dt <= now:
                dt += datetime.timedelta(days=1)
        except ValueError:
            return None, "I could not understand the time. Say it like 'tomorrow at 9' or 'in 30 minutes'."
    ts = dt.timestamp()
    if ts < time.time() - 60:
        return None, "That time is already in the past."
    if ts > time.time() + 400 * 86400:
        return None, "That is too far away (maximum 400 days)."
    return ts, None


def fmt_ts(ts):
    return datetime.datetime.fromtimestamp(ts, tz()).strftime("%Y-%m-%d %H:%M")


def human_minutes(m):
    d, rest = divmod(int(m), 1440)
    h, mi = divmod(rest, 60)
    return " ".join(x for x in (f"{d} day(s)" if d else "", f"{h} h" if h else "", f"{mi} min" if mi else "") if x) or "0 min"


# ---------------------------------------------------------------- storage
def _path():
    return os.path.join(os.environ.get("DATA_DIR") or HERE, "automation.json")


def _load():
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("next_id", 1)
    d.setdefault("schedules", [])
    d.setdefault("temp_roles", [])
    return d


def _save(d):
    with open(_path(), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


def add_schedule(gid, by, chan, when, repeat, actions, known, label):
    with _lock:
        d = _load()
        sid = d["next_id"]
        d["next_id"] += 1
        d["schedules"].append({"id": sid, "gid": gid, "by": by, "chan": chan, "when": when, "repeat": repeat,
                               "actions": actions, "known": sorted(known), "label": label[:100]})
        _save(d)
    return sid


def cancel(gid, sid=None):
    """sid None = every scheduled task of the server. Returns how many were removed."""
    with _lock:
        d = _load()
        keep = [s for s in d["schedules"] if not (s["gid"] == gid and (sid is None or s["id"] == sid))]
        n = len(d["schedules"]) - len(keep)
        d["schedules"] = keep
        _save(d)
    return n


_ctx = {}                       # {"bot": ..., "loop": ...} filled when the bot is ready


def add_temp(gid, uid, rid, exp):
    with _lock:
        d = _load()
        d["temp_roles"] = [t for t in d["temp_roles"] if not (t["gid"] == gid and t["uid"] == uid and t["rid"] == rid)]
        d["temp_roles"].append({"gid": gid, "uid": uid, "rid": rid, "exp": exp})
        _save(d)
    _kick_timer(gid, uid, rid, exp)


def _pop_temp(gid, uid, rid, exp):
    """True if this exact temporary role was still waiting (and removes it from the list)."""
    with _lock:
        d = _load()
        keep = [t for t in d["temp_roles"]
                if not (t["gid"] == gid and t["uid"] == uid and t["rid"] == rid and abs(t["exp"] - exp) < 1)]
        found = len(keep) != len(d["temp_roles"])
        if found:
            d["temp_roles"] = keep
            _save(d)
    return found


async def _expire_after(gid, uid, rid, exp):
    """Exact timer: removes the role at the right second (the 20 s loop stays as a backup after restarts)."""
    await asyncio.sleep(max(0, exp - time.time()) + 1)
    bot = _ctx.get("bot")
    if bot is not None and _pop_temp(gid, uid, rid, exp):
        try:
            await _expire_role(bot, {"gid": gid, "uid": uid, "rid": rid, "exp": exp})
        except Exception as e:
            bot.log(f"[automation] temporary role error: {type(e).__name__}: {e}")


def _kick_timer(gid, uid, rid, exp):
    loop = _ctx.get("loop")
    if loop is None or exp - time.time() > 86400:      # longer than a day: the loop will handle it
        return
    try:
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_expire_after(gid, uid, rid, exp)))
    except RuntimeError:
        pass


def remove_temp(gid, uid, rid):
    with _lock:
        d = _load()
        d["temp_roles"] = [t for t in d["temp_roles"] if not (t["gid"] == gid and t["uid"] == uid and t["rid"] == rid)]
        _save(d)


def list_text(gid):
    d = _load()
    lines = []
    for s in sorted((s for s in d["schedules"] if s["gid"] == gid), key=lambda s: s["when"]):
        rep = f" 🔁 {s['repeat']}" if s.get("repeat") in ("daily", "weekly") else ""
        lines.append(f"⏰ **#{s['id']}** {fmt_ts(s['when'])}{rep} — {s.get('label') or 'task'}")
    for t in sorted((t for t in d["temp_roles"] if t["gid"] == gid), key=lambda t: t["exp"]):
        lines.append(f"⏳ <@{t['uid']}> has <@&{t['rid']}> until {fmt_ts(t['exp'])}")
    return "\n".join(lines) or "📭 Nothing is scheduled."


# ---------------------------------------------------------------- running the schedules
def _advance(when, repeat):
    zone = tz()
    dt = datetime.datetime.fromtimestamp(when, zone).replace(tzinfo=None)
    step = datetime.timedelta(days=1 if repeat == "daily" else 7)
    while dt.replace(tzinfo=zone).timestamp() <= time.time():
        dt += step                                   # same wall-clock time on the next day / week
    return dt.replace(tzinfo=zone).timestamp()


async def fire(bot, s, late=False):
    import owner_ai
    g = bot.get_guild(s["gid"])
    if g is None:
        return
    chan = g.get_channel(s.get("chan") or 0)
    owner = g.get_member(s["by"]) or types.SimpleNamespace(id=s["by"], voice=None)
    shim = types.SimpleNamespace(guild=g, author=owner, channel=chan, mentions=[], content="", reference=None)
    steps, errors = await owner_ai.build_steps(bot, shim, {"actions": s["actions"]}, set(s.get("known") or []))
    files = []
    results = await owner_ai.run_steps(steps, files, g.id) if steps else ""
    text = f"⏰ **Scheduled task #{s['id']}** — {s.get('label') or 'task'}" + (" (late)" if late else "")
    text += "\n" + "\n".join(x for x in (results, "\n".join("❌ " + e for e in errors)) if x)
    if chan is not None:
        try:
            await chan.send(text[:1900], allowed_mentions=discord.AllowedMentions.none(),
                            **({"files": files[:3]} if files else {}))
        except discord.HTTPException:
            pass
    bot.log(f"[automation] task #{s['id']} done in {g.name}")


async def _expire_role(bot, t):
    g = bot.get_guild(t["gid"])
    if g is None:
        return
    role = g.get_role(t["rid"])
    m = g.get_member(t["uid"])
    if m is None:
        try:
            m = await g.fetch_member(t["uid"])
        except discord.HTTPException:
            return
    if role is None or role not in m.roles:
        return
    try:
        await m.remove_roles(role, reason="temporary role ended")
    except discord.HTTPException as e:
        bot.log(f"[automation] could not remove {role.name} from {m}: {e}")
        return
    try:
        await bot.event_log(g, "members", "⏳ Temporary role ended", f"{m.mention}: - {role.name}", 0x9B59B6)
    except Exception:
        pass


async def tick(bot):
    now = time.time()
    due, expired = [], []
    with _lock:
        d = _load()
        keep = []
        for s in d["schedules"]:
            if s["when"] > now:
                keep.append(s)
                continue
            late_by = now - s["when"]
            due.append((dict(s), late_by))
            if s.get("repeat") in ("daily", "weekly"):
                s["when"] = _advance(s["when"], s["repeat"])
                keep.append(s)
        d["schedules"] = keep
        left = []
        for t in d["temp_roles"]:
            (expired if t["exp"] <= now else left).append(t)
        d["temp_roles"] = left
        if due or expired:
            _save(d)
    for s, late_by in due:
        if late_by > LATE_SECONDS:                    # the bot was off for a long time: do not surprise anybody
            bot.log(f"[automation] task #{s['id']} skipped (it was {int(late_by // 60)} min late)")
            g = bot.get_guild(s["gid"])
            ch = g.get_channel(s.get("chan") or 0) if g else None
            if ch is not None:
                try:
                    await ch.send(f"⏰ I missed the scheduled task #{s['id']} ({s.get('label') or 'task'}): "
                                  "the bot was offline for too long. Tell me again if you still want it.",
                                  allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException:
                    pass
            continue
        try:
            await fire(bot, s, late=late_by > 120)
        except Exception as e:
            bot.log(f"[automation] task #{s['id']} failed: {type(e).__name__}: {e}")
    for t in expired:
        try:
            await _expire_role(bot, t)
        except Exception as e:
            bot.log(f"[automation] temporary role error: {e}")


async def loop(bot):
    await bot.wait_until_ready()
    bot.log("[automation] scheduled tasks and temporary roles are active "
            f"(time zone {_env('TIMEZONE', 'Africa/Casablanca')})")
    while not bot.is_closed():
        try:
            await tick(bot)
        except Exception as e:
            bot.log(f"[automation] {type(e).__name__}: {e}")
        await asyncio.sleep(20)


# ---------------------------------------------------------------- voice announcements
def _ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


async def _prerender(ff, src):
    """mp3 -> raw 48 kHz stereo PCM, with a short silence before (so the start is never cut), even volume,
    and a short silence after. Done BEFORE joining voice, so playing it is just reading a file (no lag)."""
    out = src.rsplit(".", 1)[0] + ".pcm"
    try:
        proc = await asyncio.create_subprocess_exec(
            ff, "-y", "-nostdin", "-loglevel", "error", "-i", src,
            "-af", "aformat=channel_layouts=stereo,adelay=500|500,loudnorm=I=-16:TP=-1.5:LRA=11,apad=pad_dur=0.4",
            "-f", "s16le", "-ar", "48000", "-ac", "2", out,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 3840 * 10:
            return out
        print(f"[automation] ffmpeg could not prepare the voice file: {(err or b'')[-200:].decode(errors='ignore')}")
    except Exception as e:
        print(f"[automation] voice pre-render failed: {type(e).__name__}: {e}")
    return None


async def announce(bot, g, channel, text, lang="ar", after="leave"):
    """Join `channel`, say `text` with text-to-speech, then leave / mute / stay. Returns a text for the owner."""
    try:
        import edge_tts
    except ImportError:
        return "❌ For voice announcements add `edge-tts` to requirements-bot.txt (and `imageio-ffmpeg` if the host has no ffmpeg)."
    ff = _ffmpeg()
    if not ff:
        return "❌ ffmpeg is missing on this host: add `imageio-ffmpeg` to requirements-bot.txt."
    perms = channel.permissions_for(g.me)
    if not (perms.connect and perms.speak):
        return f"❌ I need the Connect and Speak permissions on {channel.name}."
    if any("\u0600" <= c <= "\u06ff" for c in text):
        lang = "ar"
    voice = _env(f"GOGO_VOICE_{lang.upper()}", VOICES.get(lang, VOICES["en"]))
    try:
        import voice247
        paused = voice247.PAUSED
    except ImportError:
        voice247, paused = None, set()
    lock = _voice_locks.setdefault(g.id, asyncio.Lock())
    async with lock:
        path = os.path.join(tempfile.gettempdir(), f"gogo_tts_{g.id}_{int(time.time() * 1000)}.mp3")
        try:
            rate = _env("GOGO_VOICE_RATE", "-6%")
            if not re.match(r"^[+-]\d{1,2}%$", rate):
                rate = "-6%"
            await edge_tts.Communicate(text[:900], voice, rate=rate).save(path)
        except Exception as e:
            return f"❌ The text-to-speech service did not answer ({type(e).__name__}: {e})."
        pcm = await _prerender(ff, path)              # heavy work first, then join
        paused.add(g.id)                              # the /stay watchdog must not drag me back during the announcement
        outcome = ""
        try:
            vc = g.voice_client
            if vc and vc.is_connected():
                if vc.channel.id != channel.id:
                    await vc.move_to(channel)
            else:
                if vc:
                    await vc.disconnect(force=True)
                vc = await channel.connect(self_deaf=True, timeout=30)
            await g.change_voice_state(channel=channel, self_mute=False, self_deaf=True)
            for _ in range(20):                           # wait until the voice connection is really ready
                if vc.is_connected():
                    break
                await asyncio.sleep(0.25)
            await asyncio.sleep(1.2)
            done, loop_ = asyncio.Event(), asyncio.get_running_loop()
            stream = open(pcm, "rb") if pcm else None

            def finished(e):
                if stream:
                    stream.close()
                loop_.call_soon_threadsafe(done.set)

            if stream:
                source = discord.PCMAudio(stream)
            else:                                         # fallback: let ffmpeg decode while playing
                source = discord.FFmpegPCMAudio(path, executable=ff, before_options="-nostdin", options="-vn")
            vc.play(source, after=finished)
            await asyncio.wait_for(done.wait(), timeout=180)
            await asyncio.sleep(0.5)
            cfg = bot.config.get(str(g.id))
            stay = isinstance(cfg, dict) and cfg.get("stay_voice")
            if after == "leave":
                if stay and voice247 is not None:
                    paused.discard(g.id)
                    await voice247.ensure(bot, g.id)
                    outcome = "went back to my /stay channel"
                else:
                    await vc.disconnect(force=True)
                    outcome = "left the voice channel"
            else:
                if after == "mute":
                    await g.change_voice_state(channel=channel, self_mute=True, self_deaf=True)
                    outcome = "muted myself and stayed there"
                else:
                    outcome = "stayed there"
                if stay:                              # this channel is now my home for /stay
                    cfg["stay_voice"] = {"channel_id": channel.id}
                    bot.persist()
        except Exception as e:
            return f"❌ I could not do the voice announcement ({type(e).__name__}: {e}). Voice needs PyNaCl and davey."
        finally:
            paused.discard(g.id)
            for f in (path, pcm):
                try:
                    if f:
                        os.remove(f)
                except OSError:
                    pass
    return f"🔊 I said it in {channel.name} and {outcome}."


# ---------------------------------------------------------------- start-up
def register(bot):
    """Starts the loop (scheduled tasks + temporary roles). There are no slash commands: the owner just talks to GOGO."""
    started = {"v": False}
    original_on_ready = bot.on_ready

    async def on_ready():
        try:
            await original_on_ready()
        except Exception as e:          # another module failed at start-up: do not let it stop the timers
            bot.log(f"[automation] an earlier start-up step failed ({type(e).__name__}: {e}); continuing")
        _ctx["bot"], _ctx["loop"] = bot, asyncio.get_running_loop()
        if not started["v"]:
            started["v"] = True
            asyncio.create_task(loop(bot))

    bot.on_ready = on_ready