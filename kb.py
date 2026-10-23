"""Server knowledge base (RAG) + long memory for GOGO.

* Knowledge: the messages of the server's info channels (rules, announcements, faq...) are read, split in
  pieces and stored with Gemini embeddings (gemini-embedding-001) in knowledge.db. When someone asks GOGO
  something (or the owner gives an order) the most relevant pieces are added to the prompt.
* Memory: the owner teaches facts ("gogo hfed bli l'tournoi kol joumo3a") and GOGO keeps them forever.
* Without a working embedding key it falls back to keyword search, so it never breaks.

Which channels are indexed: set automatically (channel names with rules / announce / info / faq / قوانين / اعلان...),
change it by talking to GOGO as owner ("zid #regles l ma3rifa", "7yed #x mn lma3rifa", "3awd 9ra l ma3rifa").
"""
import array
import asyncio
import math
import os
import re
import sqlite3
import threading
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_FOR = None
EMB_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents"
KB_NAME_RE = re.compile(r"(rule|announce|info|faq|welcome|guide|قوان|قانون|اعلان|إعلان|معلومات)", re.I)
MAX_ROWS_PER_GUILD = 2500
_lock = threading.Lock()
_conn = None
_qcache = {}


def _db():
    global _conn
    if _conn is None:
        path = os.path.join(os.environ.get("DATA_DIR") or HERE, "knowledge.db")
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.execute("CREATE TABLE IF NOT EXISTS kb(id INTEGER PRIMARY KEY AUTOINCREMENT, guild TEXT, kind TEXT, "
                      "ref TEXT, title TEXT, text TEXT, emb BLOB, ts REAL)")
        _conn.execute("CREATE INDEX IF NOT EXISTS kb_g ON kb(guild, kind, ref)")
        _conn.commit()
    return _conn


# ---------------------------------------------------------------- embeddings
def _norm(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed(key, texts, task="RETRIEVAL_DOCUMENT"):
    """list of normalised vectors, or None if the API fails"""
    out = []
    try:
        for i in range(0, len(texts), 50):
            batch = texts[i:i + 50]
            r = requests.post(EMB_URL, headers={"x-goog-api-key": key, "content-type": "application/json"}, timeout=40,
                              json={"requests": [{"model": "models/gemini-embedding-001",
                                                  "content": {"parts": [{"text": t[:2000]}]},
                                                  "taskType": task, "outputDimensionality": 768} for t in batch]})
            if r.status_code != 200:
                return None
            out += [_norm(e["values"]) for e in r.json()["embeddings"]]
    except Exception:
        return None
    return out if len(out) == len(texts) else None


def _pack(v):
    return array.array("f", v).tobytes()


def _unpack(b):
    a = array.array("f")
    a.frombytes(b)
    return a


def chunk_text(text, size=800):
    out, cur = [], ""
    for para in re.split(r"\n{2,}|\n(?=[#*\-•\d])", text.strip()):
        para = para.strip()
        while len(para) > size:
            out.append(para[:size]); para = para[size:]
        if len(cur) + len(para) + 1 > size and cur:
            out.append(cur); cur = ""
        cur = (cur + "\n" + para).strip()
    if cur:
        out.append(cur)
    return out


def _tokens(s):
    return {w for w in re.findall(r"\w{3,}", s.lower())}


# ---------------------------------------------------------------- storage
def _insert(guild, kind, ref, title, texts, embs):
    now = time.time()
    with _lock:
        db = _db()
        db.execute("DELETE FROM kb WHERE guild=? AND kind=? AND ref=?", (guild, kind, ref))
        for i, t in enumerate(texts):
            db.execute("INSERT INTO kb(guild,kind,ref,title,text,emb,ts) VALUES(?,?,?,?,?,?,?)",
                       (guild, kind, ref, title, t, _pack(embs[i]) if embs else None, now))
        n = db.execute("SELECT COUNT(*) FROM kb WHERE guild=?", (guild,)).fetchone()[0]
        if n > MAX_ROWS_PER_GUILD:        # drop the oldest channel pieces, never memories
            db.execute("DELETE FROM kb WHERE id IN (SELECT id FROM kb WHERE guild=? AND kind='channel' "
                       "ORDER BY ts LIMIT ?)", (guild, n - MAX_ROWS_PER_GUILD))
        db.commit()


def add(guild, kind, ref, title, text, key=None):
    texts = chunk_text(text) or [text.strip()[:800]]
    embs = embed(key, [f"{title}: {t}" for t in texts]) if key else None
    _insert(str(guild), kind, str(ref), title, texts, embs)
    return len(texts)


def add_many(guild, kind, items, key=None):
    """items: [(ref, title, text)]. Skips what is already stored."""
    guild = str(guild)
    with _lock:
        have = {r[0] for r in _db().execute("SELECT DISTINCT ref FROM kb WHERE guild=? AND kind=?", (guild, kind))}
    new = [(str(r), t, x) for r, t, x in items if str(r) not in have]
    if not new:
        return 0
    pieces = [(r, t, c) for r, t, x in new for c in (chunk_text(x) or [x[:800]])]
    embs = embed(key, [f"{t}: {c}" for _, t, c in pieces]) if key else None
    by_ref = {}
    for i, (r, t, c) in enumerate(pieces):
        by_ref.setdefault(r, {"title": t, "texts": [], "embs": []})
        by_ref[r]["texts"].append(c)
        if embs:
            by_ref[r]["embs"].append(embs[i])
    for r, d in by_ref.items():
        _insert(guild, kind, r, d["title"], d["texts"], d["embs"] or None)
    return len(new)


def remove(guild, kind, ref):
    with _lock:
        db = _db()
        db.execute("DELETE FROM kb WHERE guild=? AND kind=? AND ref=?", (str(guild), kind, str(ref)))
        db.commit()


def remember(guild, text, by="owner", key=None):
    text = text.strip()[:600]
    if not text:
        return None
    embs = embed(key, [text]) if key else None
    ref = f"m{int(time.time() * 1000)}"
    _insert(str(guild), "memory", ref, "memory", [text], embs)
    with _lock:
        return _db().execute("SELECT id FROM kb WHERE guild=? AND ref=?", (str(guild), ref)).fetchone()[0]


def memories(guild, limit=40):
    with _lock:
        return _db().execute("SELECT id, text FROM kb WHERE guild=? AND kind='memory' ORDER BY id DESC LIMIT ?",
                             (str(guild), limit)).fetchall()


def forget(guild, mem_id):
    with _lock:
        db = _db()
        cur = db.execute("DELETE FROM kb WHERE guild=? AND kind='memory' AND id=?", (str(guild), int(mem_id)))
        db.commit()
        return cur.rowcount > 0


def stats(guild):
    with _lock:
        rows = _db().execute("SELECT kind, COUNT(*) FROM kb WHERE guild=? GROUP BY kind", (str(guild),)).fetchall()
    return dict(rows)


# ---------------------------------------------------------------- search
def search(guild, query, key=None, k=5):
    with _lock:
        rows = _db().execute("SELECT kind, title, text, emb FROM kb WHERE guild=?", (str(guild),)).fetchall()
    if not rows or not query.strip():
        return []
    qv = None
    if key and any(r[3] for r in rows):
        ck = (key[-6:], query)
        qv = _qcache.get(ck)
        if qv is None:
            res = embed(key, [query], "RETRIEVAL_QUERY")
            qv = res[0] if res else None
            if qv:
                if len(_qcache) > 300:
                    _qcache.clear()
                _qcache[ck] = qv
    qt = _tokens(query)
    scored = []
    for kind, title, text, emb in rows:
        kw = len(qt & _tokens(text)) / (len(qt) or 1)
        if qv is not None and emb:
            s = sum(a * b for a, b in zip(qv, _unpack(emb))) + 0.3 * kw      # meaning + exact words
            ok = s >= 0.5
        else:
            s = kw
            ok = s >= 0.3
        if ok:
            scored.append((s + (0.05 if kind == "memory" else 0), kind, title, text))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:k]


def context_for(guild, query, key=None, k=5, max_chars=1800):
    out, used = [], 0
    for _, kind, title, text in search(guild, query, key, k):
        line = f"- [{'owner memory' if kind == 'memory' else title}] {text}"
        if used + len(line) > max_chars:
            break
        out.append(line)
        used += len(line)
    return "\n".join(out)


# ---------------------------------------------------------------- Discord side
def kb_channel_ids(bot, guild, discover=False):
    cfg = bot.config.setdefault(str(guild.id), {})
    if "kb" not in cfg and discover:
        cfg["kb"] = {"channel_ids": [c.id for c in guild.text_channels if KB_NAME_RE.search(c.name)]}
        bot.persist()
    return list((cfg.get("kb") or {}).get("channel_ids") or [])


def _key(guild_id):
    return KEY_FOR(guild_id) if KEY_FOR else None


async def sync_guild(bot, guild, limit=150):
    key, total = _key(guild.id), 0
    for cid in kb_channel_ids(bot, guild, discover=True):
        ch = guild.get_channel(cid)
        if ch is None:
            continue
        items = []
        try:
            async for m in ch.history(limit=limit):
                if not m.author.bot and len((m.content or "").strip()) >= 12:
                    items.append((m.id, f"#{ch.name}", m.content.strip()))
        except Exception:
            continue
        total += await asyncio.to_thread(add_many, guild.id, "channel", items, key)
    return total


async def on_new_message(bot, message):
    if message.author.bot or not message.guild or len((message.content or "").strip()) < 12:
        return
    if message.channel.id in kb_channel_ids(bot, message.guild):
        await asyncio.to_thread(add, message.guild.id, "channel", message.id, f"#{message.channel.name}",
                                message.content.strip(), _key(message.guild.id))


async def on_edit(bot, after):
    await on_new_message(bot, after)


async def on_delete(bot, message):
    if message.guild and message.channel.id in kb_channel_ids(bot, message.guild):
        await asyncio.to_thread(remove, message.guild.id, "channel", message.id)


def register(bot):
    """Index the info channels at start-up and keep them in sync."""
    started = {"v": False}
    original_ready, original_edit, original_delete = bot.on_ready, bot.on_message_edit, bot.on_message_delete

    async def sync_all():
        await bot.wait_until_ready()
        for g in list(bot.guilds):
            try:
                n = await sync_guild(bot, g)
                if n:
                    bot.log(f"[kb] {g.name}: indexed {n} new message(s)")
            except Exception as e:
                bot.log(f"[kb] {g.name}: {e}")
            await asyncio.sleep(2)

    async def on_ready():
        await original_ready()
        if not started["v"]:
            started["v"] = True
            asyncio.create_task(sync_all())

    async def on_message_edit(before, after):
        await original_edit(before, after)
        try:
            await on_edit(bot, after)
        except Exception as e:
            bot.log(f"[kb] edit: {e}")

    async def on_message_delete(message):
        await original_delete(message)
        try:
            await on_delete(bot, message)
        except Exception as e:
            bot.log(f"[kb] delete: {e}")

    bot.on_ready, bot.on_message_edit, bot.on_message_delete = on_ready, on_message_edit, on_message_delete
