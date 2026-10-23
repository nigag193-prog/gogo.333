import base64
import io
import json
import time

import requests

MODEL = "claude-sonnet-5-5"  # change here if you want another model

SYSTEM = """You are a Discord server architect. Given a description, design a complete server.
Reply with ONLY one valid JSON object, no markdown, no comments, in this shape
(optional keys may be omitted when they do not fit the request):
{
  "roles": [{"name": "Admin", "color": "#e74c3c", "permissions": "admin", "hoist": true}],
  "categories": [
    {"name": "INFO", "unverified_only": false, "channels": [
      {"name": "welcome", "type": "text", "topic": "short topic", "readonly": true},
      {"name": "staff-chat", "type": "text", "topic": "", "private_roles": ["Admin", "Mod"]},
      {"name": "Lounge", "type": "voice"}
    ]}
  ],
  "verification": {"channel": "verify", "text": "Press the button to unlock the server.", "button_label": "Verify me", "unverified_role": "Unverified", "verified_role": "Member"},
  "welcome": {"channel": "welcome", "message": "Welcome {user} to {server}!", "auto_role": "Member"},
  "antispam": {"enabled": true, "max_messages": 5, "seconds": 6, "block_invites": true,
               "block_links": false, "action": "timeout", "timeout_minutes": 10},
  "tickets": {"channel": "support", "text": "Need help? Press the button.", "staff_roles": ["Admin", "Mod"]},
  "role_panel": {"channel": "get-roles", "text": "Pick your roles:", "roles": ["Gamer", "Artist"]},
  "levels": {"enabled": true, "channel": "general", "roles": [{"level": 5, "role": "Active"}]},
  "logs": {"channel": "logs", "members": "member-logs", "messages": "message-logs", "voice": "voice-logs", "mod": "mod-logs", "server": "server-logs"},
  "visuals": {"icon_prompt": "...", "banner_prompt": "...", "emojis": [{"name": "gg", "prompt": "..."}]}
}
Rules:
- "permissions" is one of: "admin" (full administrator), "senior" (everything EXCEPT administrator: manage roles/channels/guild, kick, ban...), "mod" (kick, ban, move members, timeout, manage messages - NO roles/channels/guild settings), "helper" (timeout + manage messages + audit log), "member" (normal member), "none" (cosmetic role, no permissions).
- Always include roles Admin (admin), Mod (mod) and Member (member), plus cosmetic roles (none) that fit the theme.
- role_panel.roles and levels.roles may only use cosmetic roles (permissions none) that you defined.
- Every channel named in welcome, tickets, role_panel, levels or logs must be a text channel you defined
  (use dedicated channels for tickets, role_panel and logs).
- welcome.auto_role must be a role you defined.
- Every logs channel (logs, members, messages, voice, mod, server) must be a text channel you defined, private to staff.
- Only if the description asks for verification: add an "Unverified" role (none), a first category with "unverified_only": true holding one readonly text channel for verify, set the "verification" block, and set welcome.auto_role to Unverified.
- Keep categories in the top-to-bottom order the description asks for.
- Optional extra modules (add only when they fit):
  "bots": {"welcome": "ProBot", "logs": "Dyno"} hands a module (welcome, logs, levels, antispam) to a bot already in the server;
  "lfg": {"channel": "lfg", "category": "VOICE", "max_players": 5};
  "starboard": {"sources": ["clips"], "target": "best-clips", "emoji": "⭐", "threshold": 3};
  "economy": {"enabled": true, "daily": 100, "per_message": [1, 3], "voice_per_minute": 1, "shop": [{"name": "Gold", "role": "Gold", "price": 1000}]} (shop roles must have permissions "none");
  "custom_vc": {"trigger": "➕・Create Room", "category": "VOICE"} = joining this voice channel gives the member his own public voice channel (place the trigger in the middle of that category);
  "assistant": {"channel": "ask-gogo", "member_role": "Member"} = an AI chat channel that answers any question about the server (channels, roles, rules) in the user's language; only members with member_role can use it;
  "afk": {"channel": "💤・AFK", "timeout": 300} = Discord's inactive-members voice channel (timeout in seconds: 60, 300, 900, 1800 or 3600); put it ALONE in the LAST category;
  "antinuke": {"enabled": true, "max_actions": 3, "seconds": 30, "whitelist_roles": ["Owner"]}.
- Write names, topics and messages in the same language as the description.
- At most 14 categories and 90 channels in total.
- Text channel names: lowercase with hyphens.
- visuals prompts are in English, describe style and theme, never ask for text inside images; at most 6 emojis.
- Include the modules (tickets, levels, logs, role_panel, visuals) whenever the description suggests a community."""


def _extract_json(text):
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("The model did not return JSON. Try again.")
    return json.loads(text[start:end + 1])


# Tried in order; if a model is retired (404) or overloaded (503/500/429) the next one is used.
GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-flash"]
RETRYABLE = (404, 429, 500, 502, 503, 504)


def _gemini(api_key, description, images=None):
    last = "no model worked"
    for attempt in range(3):  # whole list is retried if Google is overloaded
        for model in GEMINI_MODELS:
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": api_key, "content-type": "application/json"},
                    json={
                        "systemInstruction": {"parts": [{"text": SYSTEM}]},
                        "contents": [{"role": "user", "parts": (
                            [{"inline_data": {"mime_type": mt, "data": data}} for mt, data in (images or [])]
                            + [{"text": description}])}],
                        "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 8000},
                    },
                    timeout=180,
                )
            except requests.RequestException as e:
                last = f"{model}: {e}"
                continue
            if r.status_code in RETRYABLE:
                last = f"{model}: HTTP {r.status_code}"
                continue
            if r.status_code != 200:
                raise RuntimeError(f"Gemini error {r.status_code}: {r.text[:300]}")
            try:
                parts = r.json()["candidates"][0]["content"]["parts"]
            except (KeyError, IndexError):
                last = f"{model}: empty answer"
                continue
            return _extract_json("".join(p.get("text", "") for p in parts))
        time.sleep(4 * (attempt + 1))
    raise RuntimeError(
        f"Gemini is overloaded or unavailable right now ({last}). Wait a minute and try again, "
        "or switch the provider to Claude.")


def generate_blueprint(api_key, description, provider="gemini", images=None):
    """images: optional list of (media_type, base64) screenshots."""
    if provider == "gemini":
        return _gemini(api_key, description, images)
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 6000,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": (
                [{"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}}
                 for mt, data in (images or [])] + [{"type": "text", "text": description}])}],
        },
        timeout=180,
    )
    if r.status_code != 200:
        raise RuntimeError(f"API error {r.status_code}: {r.text[:300]}")
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    return _extract_json(text)


SCREENSHOT_PROMPT = """The attached image(s) are screenshots of a Discord server (channel list, role list or both).
Rebuild its STRUCTURE as the JSON blueprint described in your instructions:
- Keep every category and channel name EXACTLY as written, including emojis and separators, and the same top-to-bottom order.
- A speaker/volume icon means a voice channel, a # or text icon a text channel. A megaphone or rules icon is a read-only text channel.
- A lock icon means a private channel: put the staff or top roles you can infer in private_roles.
- Role names and colors: copy them when visible; permissions are NOT visible in screenshots, so guess sensibly
  (owner/admin = "admin", moderator = "mod", ordinary members = "member", cosmetic or badge roles = "none").
- Do NOT invent categories, channels or roles that are not visible. If something is cut off, skip it.
- Only add optional modules (tickets, levels, logs, role_panel, verification) when a matching channel is visible
  (for example a ticket, verify, roles or logs channel).
Extra notes from the user: """


def _prepare_image(path, max_side=1568):
    """Shrink and convert to JPEG so uploads stay small. Returns (media_type, base64)."""
    try:
        from PIL import Image
        img = Image.open(path)
        img.load()
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
        return "image/jpeg", base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        with open(path, "rb") as f:
            raw = f.read()
        ext = path.lower().rsplit(".", 1)[-1]
        mt = {"png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
        return mt, base64.b64encode(raw).decode()


def blueprint_from_screenshots(api_key, paths, provider="gemini", note=""):
    if not paths:
        raise ValueError("Choose at least one screenshot.")
    images = [_prepare_image(p) for p in paths[:6]]
    return generate_blueprint(api_key, SCREENSHOT_PROMPT + (note or "none"), provider, images)