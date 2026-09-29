"""KV-cache type / flash-attention probe on a PRIVATE Ollama (port 11501, same model store; the real Ollama is untouched).

    venv\\Scripts\\python tests\\kv_cache_probe.py f16 q8_0

For each setting: start `ollama serve` with that OLLAMA_KV_CACHE_TYPE, load ishti-qwen2.5:14b, and record the footprint
(/api/ps), warm tokens/s, first-token latency, the fixed ISHTI quality prompts (ishti.vram_calibrate.PROMPTS + a tool call)
and a long-context recall test (a fact hidden in ~6k tokens of text, asked for at the end): the case where a quantized KV
cache is reported to cost quality. Also greps the server log for what this Ollama version says about flash attention.
Needs the GPU free enough for one 14B model: unload the real Ollama's models first."""
import json
import os
import re
import statistics
import subprocess
import sys
import time

import requests

ROOT = r"D:\ISHTI"
sys.path.insert(0, ROOT)
from ishti.vram_calibrate import PROMPTS, TOOL  # noqa: E402

PORT = 11501
URL = f"http://127.0.0.1:{PORT}"
MODEL = "ishti-qwen2.5:14b"
LOG = os.path.join(os.environ["LOCALAPPDATA"], "ISHTI", "logs", "ollama_probe.log")


def chat(prompt, n=200, ctx=None):
    body = {"model": MODEL, "prompt": prompt, "stream": False, "keep_alive": "5m", "options": {"num_predict": n, "temperature": 0}}
    if ctx:
        body["options"]["num_ctx"] = ctx
    return requests.post(URL + "/api/generate", json=body, timeout=300).json()


def needle_test():
    """A code word stated once at the start of ~5.5k tokens of filler; asked for at the end. 3 different positions/words."""
    filler = " ".join(f"Paragraph {i}: the committee reviewed item {i} and postponed it to the next quarter without further comment."
                      for i in range(230))
    ok = 0
    for word, pos in (("TURQUOISE-47", 0.05), ("HALCYON-92", 0.5), ("MARIGOLD-13", 0.9)):
        cut = int(len(filler) * pos)
        text = filler[:cut] + f" IMPORTANT: the secret code word is {word}. " + filler[cut:]
        r = chat(text + "\n\nWhat is the secret code word? Reply with only the code word.", 20, ctx=8192)
        ok += word in r.get("response", "")
    return ok


def run(kv):
    env = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{PORT}", "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_DEBUG": "1"}
    env.pop("OLLAMA_KV_CACHE_TYPE", None)
    env.pop("OLLAMA_FLASH_ATTENTION", None)
    if kv != "f16":
        env["OLLAMA_KV_CACHE_TYPE"] = kv
        env["OLLAMA_FLASH_ATTENTION"] = "1"
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    out = open(LOG, "w", encoding="utf-8", buffering=1)
    p = subprocess.Popen(["ollama", "serve"], env=env, stdout=out, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        for _ in range(60):
            try:
                requests.get(URL + "/api/tags", timeout=1)
                break
            except requests.RequestException:
                time.sleep(0.5)
        t0 = time.time()
        chat("", 1)
        load_s = time.time() - t0
        ps = requests.get(URL + "/api/ps").json()["models"][0]
        tps, ttft = [], []
        for _ in range(3):
            r = chat("Write two sentences about the city of Peshawar.", 96)
            tps.append(r["eval_count"] / r["eval_duration"] * 1e9)
            ttft.append((r.get("prompt_eval_duration", 0)) / 1e6)
        quality = []
        for key, (prompt, judge) in PROMPTS.items():
            try:
                quality.append(bool(judge(chat(prompt, 160)["response"])))
            except Exception:
                quality.append(False)
        msg = requests.post(URL + "/api/chat", json={"model": MODEL, "stream": False, "tools": TOOL, "options": {"temperature": 0, "num_predict": 100},
                                                     "messages": [{"role": "user", "content": "What's the weather in Peshawar right now?"}]}, timeout=120).json()["message"]
        quality.append(any(c["function"]["name"] == "get_weather" for c in msg.get("tool_calls") or []))
        needle = needle_test()
        res = {"kv_cache_type": kv, "size_vram_mb": round(ps["size_vram"] / 2 ** 20), "size_mb": round(ps["size"] / 2 ** 20), "ctx": ps["context_length"],
               "load_s": round(load_s, 1), "tps": round(statistics.median(tps), 1), "ttft_ms": round(statistics.median(ttft)),
               "quality": f"{sum(quality)}/{len(quality)}", "long_context_recall": f"{needle}/3"}
    finally:
        import psutil  # terminating `ollama serve` alone leaves its llama-server runner (and its VRAM) behind
        try:
            for c in psutil.Process(p.pid).children(recursive=True) + [psutil.Process(p.pid)]:
                try:
                    c.kill()
                except psutil.Error:
                    pass
        except psutil.NoSuchProcess:
            print("(ollama serve had already exited; log tail follows)", open(LOG, encoding="utf-8", errors="replace").read()[-600:])
        out.close()
        time.sleep(3)  # let the GPU memory come back before the next setting
    text = open(LOG, encoding="utf-8", errors="replace").read()
    res["log_flash_attention"] = sorted(set(re.findall(r"(?i)[^\n]*flash[^\n]*", text)))[:4]
    res["log_kv"] = sorted(set(re.findall(r"(?i)[^\n]*kv[ _]?cache[^\n]*", text)))[:3]
    return res


if __name__ == "__main__":
    results = []
    for kv in sys.argv[1:] or ["f16", "q8_0"]:
        r = run(kv)
        print(json.dumps(r, indent=1, ensure_ascii=False)[:1800], flush=True)
        results.append(r)
    json.dump(results, open(os.path.join(ROOT, "data", "kv_cache_probe.json"), "w", encoding="utf-8"), indent=1)
