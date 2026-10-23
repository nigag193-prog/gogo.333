"""Run once next to assistant.py:   python patch_assistant.py
- updates the Gemini model list to models that exist today
- makes _gemini() print the REAL error of every failed call (status code + message) in the console
- gives the model enough output tokens (Gemini 3.x "thinking" tokens count against the limit, so 450/700
  could end with an empty answer -> "I could not answer right now")
A copy of the old file is saved as assistant.py.bak"""
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
path = os.path.join(HERE, "assistant.py")
src = open(path, encoding="utf-8").read()

NEW_MODELS = 'MODELS = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]'
NEW_FUNC = '''def _gemini(key, system, question, as_json=False):
    """Tries the models in order. Prints why a model failed (see the bot console).
    Note: temperature/top_p are deprecated on Gemini 3.6+ and must NOT be sent."""
    for model in MODELS:
        try:
            r = requests.post(
                GEMINI_URL.format(model=model),
                headers={"x-goog-api-key": key, "content-type": "application/json"},
                json={
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": question}]}],
                    "generationConfig": ({"maxOutputTokens": 4096, "responseMimeType": "application/json"}
                                         if as_json else {"maxOutputTokens": 2048}),
                },
                timeout=30)
            if r.status_code != 200:
                try:
                    msg = r.json()["error"]["message"][:200]
                except Exception:
                    msg = r.text[:200]
                print(f"[gogo-ai] {model}: HTTP {r.status_code} - {msg}")
                continue                      # retired / quota: try the next model
            data = r.json()
            cand = (data.get("candidates") or [{}])[0]
            parts = (cand.get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
            if text:
                return text
            print(f"[gogo-ai] {model}: empty answer (finishReason={cand.get('finishReason')}, "
                  f"promptFeedback={data.get('promptFeedback')})")
        except Exception as e:
            print(f"[gogo-ai] {model}: {type(e).__name__}: {e}")
    return None


'''

new_src, n1 = re.subn(r"MODELS = \[.*?\]", NEW_MODELS, src, count=1, flags=re.S)
new_src, n2 = re.subn(r"def _gemini\(.*?\n\n\n(?=def _faq_answer)", lambda m: NEW_FUNC, new_src, count=1, flags=re.S)
if not (n1 and n2):
    print("Could not find MODELS or _gemini in assistant.py - nothing changed.")
    sys.exit(1)
shutil.copy(path, path + ".bak")
open(path, "w", encoding="utf-8").write(new_src)
print("assistant.py patched (backup: assistant.py.bak). Restart the bot.")
