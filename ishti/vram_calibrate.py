"""Measure every installed ladder rung on THIS GPU: python -m ishti.vram_calibrate [model ...]

For each rung (at the context the ladder gives it): unload everything, load it, record the real footprint from /api/ps
(size, size_vram, context) and the GPU's total used memory, run the fixed ISHTI test prompts for quality and tokens/s,
then unload. Results go to data/vram.db (`model_footprints`, so the governor uses measured numbers from then on) and
data/vram_calibration.json. The surviving written record of these measurements is `models_ladder.yaml` and
`docs/arch/gpu-vram-governor.md`; the older MODEL_DECISIONS.md they once fed has been retired.
It takes over the GPU for a few minutes: run it with ISHTI stopped.

Thinking models (a `thinking` capability in /api/show, e.g. Spark-X2.5) reason before answering, so the
fixed quality prompts run with a larger token budget (NUM_PREDICT_THINKING) and are judged on the text
AFTER the </think> stream - the 160-token cap was sized for non-thinking brains and truncated the
reasoning mid-stream (measured: Spark needed 260 tokens for the json prompt, 962 for the table).

Your own prompts (what actually matters for a lighter rung, e.g. Urdu/Pashto, plans, tool use): put them in
data/owner_prompts.txt, one per line, optionally `prompt || text the answer must contain`; lines starting with # are
ignored. Every rung then also answers them and the replies are saved in data/vram_calibration.json (`owner_prompts`)
so a rung's quality is judged on YOUR traffic, not only on the fixed prompts above."""
import base64
import io
import json
import os
import re
import statistics
import sys
import time

import requests

from ishti import vram

OUT = os.path.join(vram.DATA, "vram_calibration.json")
OWNER_PROMPTS = os.path.join(vram.DATA, "owner_prompts.txt")
NUM_PREDICT_DEFAULT = 160     # non-thinking brains: answers fit well inside this (14B quality row, 29 Sep)
NUM_PREDICT_THINKING = 2048   # thinking brains: room to reason first; the answer itself stays short
PROMPTS = {
    "arithmetic": ("What is 17 times 23? Reply with just the number.", lambda t: "391" in t),
    "json": ('Extract JSON {"contact": "...", "message": "..."} from: send hi to ali on whatsapp. Output only JSON.',
             lambda t: (lambda d: str(d.get("contact", "")).lower() == "ali" and "hi" in str(d.get("message", "")).lower())(_json(t))),
    "table": ("Compare cats and dogs in a Markdown table with two rows (Cat, Dog) and columns Size and Sound.", lambda t: "|---" in t.replace(" ", "") or "| ---" in t),
    "urdu": ("Translate 'good morning' to Urdu. Reply with only the Urdu text.", lambda t: bool(re.search(r"[؀-ۿ]", t))),
}
TOOL = [{"type": "function", "function": {"name": "get_weather", "description": "Weather for a city", "parameters": {
    "type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]


def load_owner_prompts(path=None):
    """[(prompt, expected_substring_or_None)] from the owner's prompt file; [] when the file does not exist."""
    out = []
    try:
        with open(path or OWNER_PROMPTS, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    prompt, _, expect = line.partition("||")
                    out.append((prompt.strip(), expect.strip() or None))
    except OSError:  # no prompt file: the fixed prompts are all there is
        return out
    return out


def _json(t):
    m = re.search(r"\{.*\}", t, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}


def _answer(text):
    """The graded answer: everything after the thinking stream, when the model emits one."""
    return text.rsplit("</think>", 1)[-1] if "</think>" in (text or "") else (text or "")


def _image():
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (320, 160), "white")
    ImageDraw.Draw(im).text((20, 50), "PRICE 42", fill="black", font_size=48) if hasattr(ImageDraw.ImageDraw, "text") else None
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def generate(name, prompt, n=96, images=None):
    r = requests.post(vram.OLLAMA + "/api/generate", json={"model": name, "prompt": prompt, "stream": False, "keep_alive": "5m",
                                                          "images": images or [], "options": {"num_predict": n, "temperature": 0}}, timeout=300).json()
    return r


def measure(name, ctx):
    print(f"\n== {name} (ctx {ctx})", flush=True)
    vram.variant(vram.base_of(name)[0], ctx)  # creates the same-weights alias when this rung runs at a non-default context
    for m in vram.ps(True):
        vram.unload(m["name"])
    time.sleep(1.5)
    base = vram.gpu()["used_mb"]
    t0 = time.time()
    ok, entry = vram.load(name, "5m")
    load_s = time.time() - t0
    used = vram.gpu()["used_mb"]
    res = {"model": name, "num_ctx": entry["ctx"] if entry else None, "size_mb": entry["size_mb"] if entry else None,
           "size_vram_mb": entry["size_vram_mb"] if entry else None, "fully_on_gpu": bool(ok), "gpu_delta_mb": used - base,
           "load_s": round(load_s, 1)}
    if not ok:
        print("   NOT fully on GPU:", res, flush=True)
        vram.unload(name)
        return res
    tps, ttft = [], []
    for _ in range(3):
        r = generate(name, "Write two sentences about the city of Peshawar.", 96)
        if r.get("eval_duration"):
            tps.append(r["eval_count"] / r["eval_duration"] * 1e9)
            ttft.append((r.get("prompt_eval_duration", 0) + r.get("load_duration", 0)) / 1e6)
    res["tps"], res["ttft_ms"] = round(statistics.median(tps), 1), round(statistics.median(ttft))
    vram.record([entry], tps=res["tps"], delta_mb=res["gpu_delta_mb"])
    caps = requests.post(vram.OLLAMA + "/api/show", json={"model": name}, timeout=15).json().get("capabilities", [])
    think = "thinking" in caps
    budget = NUM_PREDICT_THINKING if think else NUM_PREDICT_DEFAULT
    quality, notes = 0, []
    total = 0
    for key, (prompt, judge) in PROMPTS.items():
        total += 1
        try:
            ok_q = bool(judge(_answer(generate(name, prompt, budget)["response"])))
        except Exception:
            ok_q = False
        quality += ok_q
        notes.append(f"{key}:{'ok' if ok_q else 'FAIL'}")
    if "tools" in caps:
        total += 1
        try:
            msg = requests.post(vram.OLLAMA + "/api/chat", json={"model": name, "stream": False, "tools": TOOL, "options": {"temperature": 0, "num_predict": budget},
                                                                 "messages": [{"role": "user", "content": "What's the weather in Peshawar right now?"}]}, timeout=120).json()["message"]
            ok_t = any(c["function"]["name"] == "get_weather" for c in msg.get("tool_calls") or [])
        except Exception:
            ok_t = False
        quality += ok_t
        notes.append(f"tool_call:{'ok' if ok_t else 'FAIL'}")
    if "vision" in caps:
        total += 1
        try:
            ok_v = "42" in generate(name, "What number is written in this image? Reply with just the number.", 20, [_image()])["response"]
        except Exception:
            ok_v = False
        quality += ok_v
        notes.append(f"vision:{'ok' if ok_v else 'FAIL'}")
    owner = []
    for prompt, expect in load_owner_prompts():
        try:
            reply = _answer(generate(name, prompt, budget)["response"]).strip()
        except Exception as exc:
            reply = f"(failed: {type(exc).__name__})"
        owner.append({"prompt": prompt, "reply": reply[:400], "expected": expect,
                      "ok": (expect.lower() in reply.lower()) if expect else None})
    res.update(quality=f"{quality}/{total}", quality_detail=" ".join(notes) + (f" [think@{budget}]" if think else ""),
               think=think, quality_num_predict=budget, owner_prompts=owner)
    print("  ", {k: res[k] for k in ("size_vram_mb", "gpu_delta_mb", "load_s", "tps", "ttft_ms", "quality")}, res["quality_detail"], flush=True)
    vram.unload(name)
    return res


def main(only=None):
    todo = []
    for role, spec in vram.cfg()["roles"].items():
        for r in vram.rungs(role):
            if r.get("cpu"):
                continue
            key = (r["name"], r.get("num_ctx") or vram.info(r["model"])["default_ctx"])
            if key not in [t[:2] for t in todo] and (not only or r["name"] in only or r["model"] in only):
                todo.append((*key, role))
    results = [measure(n, c) | {"roles": [t[2] for t in todo if t[0] == n]} for n, c, _ in todo]
    old = []
    if os.path.isfile(OUT):
        old = [r for r in json.load(open(OUT, encoding="utf-8")) if r["model"] not in {x["model"] for x in results}]
    json.dump(old + results, open(OUT, "w", encoding="utf-8"), indent=1)
    print("\n| model | ctx | GPU MiB | tok/s | first token ms | load s | quality |\n|---|---|---|---|---|---|---|")
    for r in results:
        print(f"| {r['model']} | {r['num_ctx']} | {r['size_vram_mb']} | {r.get('tps', '-')} | {r.get('ttft_ms', '-')} | {r['load_s']} | {r.get('quality', 'not fully on GPU')}{' (thinking)' if r.get('think') else ''} |")


if __name__ == "__main__":
    main(set(sys.argv[1:]) or None)
