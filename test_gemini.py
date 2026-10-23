"""Run this next to run_bot.py:   python test_gemini.py
It tests every Gemini key the bot knows (.env + gemini_keys.txt) with every model the bot uses,
and prints the real reason when Ask GOGO says "I could not answer right now"."""
import os
import re
import sys

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MODELS = ["gemini-2.0-flash"]
try:
    import assistant
    MODELS = list(getattr(assistant, "MODELS", MODELS))
    URL = getattr(assistant, "GEMINI_URL", URL)
    print("models used by the bot:", MODELS)
except Exception as e:
    print("(could not import assistant.py:", e, ") using defaults")

keys = {}
env = os.path.join(HERE, ".env")
if os.path.exists(env):
    for line in open(env, encoding="utf-8"):
        m = re.match(r"\s*(GEMINI_API_KEY|GEMINI_KEY_\d+)\s*=\s*(\S+)", line)
        if m:
            keys[m.group(1)] = m.group(2).strip('"').strip("'")
pool = os.path.join(HERE, "gemini_keys.txt")
if os.path.exists(pool):
    for i, line in enumerate(open(pool, encoding="utf-8")):
        line = line.strip()
        if line and not line.startswith("#"):
            keys[f"gemini_keys.txt line {i + 1}"] = line
if not keys:
    print("NO KEY FOUND: add GEMINI_API_KEY=... to .env")
    sys.exit(1)

for name, key in keys.items():
    print(f"\n== {name}  ({key[:6]}...{key[-4:]})")
    for model in MODELS:
        try:
            r = requests.post(URL.format(model=model),
                              headers={"x-goog-api-key": key, "content-type": "application/json"},
                              json={"contents": [{"role": "user", "parts": [{"text": "say ok"}]}]}, timeout=30)
            if r.status_code == 200:
                print(f"  {model}: OK")
            else:
                try:
                    msg = r.json()["error"]["message"][:160]
                except Exception:
                    msg = r.text[:160]
                print(f"  {model}: HTTP {r.status_code} - {msg}")
        except Exception as e:
            print(f"  {model}: ERROR {e}")
