import base64
import io
import urllib.parse

import requests
from PIL import Image


def _fetch(prompt, w, h, openai_key=None):
    """Returns a PIL image. Uses OpenAI images if a key is given, else the free Pollinations service."""
    if openai_key:
        size = "1024x1024" if w == h else "1536x1024"
        r = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {openai_key}"},
            json={"model": "gpt-image-1", "prompt": prompt, "size": size},
            timeout=180)
        if r.status_code != 200:
            raise RuntimeError(f"Image API error {r.status_code}: {r.text[:200]}")
        raw = base64.b64decode(r.json()["data"][0]["b64_json"])
    else:
        url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt)
               + f"?width={w}&height={h}&nologo=true")
        r = requests.get(url, timeout=180)
        if r.status_code != 200:
            raise RuntimeError(f"Image service error {r.status_code}")
        raw = r.content
    return Image.open(io.BytesIO(raw)).convert("RGBA")


def _png(img, size):
    out = io.BytesIO()
    img.resize(size, Image.LANCZOS).save(out, format="PNG", optimize=True)
    return out.getvalue()


def make_icon(prompt, openai_key=None):
    img = _fetch(f"square server logo, flat design, no text, {prompt}", 1024, 1024, openai_key)
    return _png(img, (512, 512))


def make_banner(prompt, openai_key=None):
    img = _fetch(f"wide server banner, no text, {prompt}", 1280, 720, openai_key)
    return _png(img, (1280, 720))


def make_emoji(prompt, openai_key=None):
    """Discord emojis must be under 256 KB; shrink until it fits."""
    img = _fetch(f"single emoji sticker, simple, centered, plain background, no text, {prompt}",
                 512, 512, openai_key)
    for side in (128, 96, 64):
        data = _png(img, (side, side))
        if len(data) < 250_000:
            return data
    return data
