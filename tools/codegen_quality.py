"""Codegen-quality harness for the coder rungs (N24): how good is the code each rung really writes?

    venv/Scripts/python tools/codegen_quality.py [--runs 5] [--rung ishti-qwen2.5-coder:14b] [--task roman]

For every coder rung of the ladder (models_ladder.yaml) and every acceptance task it replays the REAL generation prompt
(ishti.skills.skill_creator.skill.generate - the same code path, same limits: <=5 calls, <=3500 tokens, <=300 lines) N
times and records, per run: did a usable module come back, does it compile, does its own self-check pass in a
subprocess, how many coder calls it took, tokens and seconds. Output: a markdown table on stdout and
data/codegen_quality.json. It needs the GPU for the model it measures: run it with ISHTI idle (the governor admits the
rung, so it never partially offloads). Nothing is installed; every generated file lives in a temp folder.

The measuring core `run_matrix(ask_for_rung, ...)` takes an injected model function, so tests/test_codegen_quality.py
exercises all of it offline with fake models."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "data", "codegen_quality.json")
TASKS = {
    "roman": "create a skill that converts roman numerals to numbers and back",
    "temperature": "build me a skill that converts temperatures between celsius, fahrenheit and kelvin",
    "textstats": "make a skill that counts the words, sentences and the most common word in a pasted text",
}


def check_module(code, timeout=30):
    """-> {'compiles': bool, 'selfcheck': bool, 'detail': str} for one generated skill.py (run in a temp folder)."""
    with tempfile.TemporaryDirectory(prefix="codegen_q_") as tmp:
        path = os.path.join(tmp, "skill.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        c = subprocess.run([sys.executable, "-m", "py_compile", path], capture_output=True, text=True, timeout=timeout,
                           creationflags=flags)
        if c.returncode != 0:
            return {"compiles": False, "selfcheck": False, "detail": (c.stderr or c.stdout)[-200:]}
        try:
            r = subprocess.run([sys.executable, path], cwd=tmp, capture_output=True, text=True, timeout=timeout,
                               creationflags=flags, env={**os.environ, "PYTHONPATH": ROOT, "ISHTI_TEST_MODE": "1"})
        except subprocess.TimeoutExpired:
            return {"compiles": True, "selfcheck": False, "detail": f"self-check still running after {timeout}s"}
        return {"compiles": True, "selfcheck": r.returncode == 0,
                "detail": "" if r.returncode == 0 else (r.stderr or r.stdout)[-200:]}


def run_matrix(ask_for_rung, rungs, tasks=None, runs=5, clock=time.perf_counter):
    """ask_for_rung(rung_name) -> llm_fn(prompt, **kw) -> text. Returns a list of per-(rung, task) result dicts."""
    from ishti.skills.skill_creator import skill as sc
    rows = []
    for rung in rungs:
        ask = ask_for_rung(rung)
        for key, request in (tasks or TASKS).items():
            res = {"rung": rung, "task": key, "runs": runs, "usable": 0, "compiles": 0, "selfcheck": 0, "calls": [],
                   "seconds": [], "failures": []}
            for _ in range(runs):
                t0 = clock()
                gen = sc.generate(request, llm_fn=ask)
                res["seconds"].append(round(clock() - t0, 1))
                res["calls"].append(gen.get("calls", 0))
                if not gen.get("ok"):
                    res["failures"].append(gen.get("reason", "")[:160])
                    continue
                res["usable"] += 1
                chk = check_module(gen["skill_py"])
                res["compiles"] += chk["compiles"]
                res["selfcheck"] += chk["selfcheck"]
                if not chk["selfcheck"]:
                    res["failures"].append(chk["detail"][:160])
            rows.append(res)
    return rows


def table(rows):
    lines = ["| rung | task | usable | compiles | self-check passes | mean calls | mean s |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        n = r["runs"]
        mean = lambda xs: round(sum(xs) / len(xs), 1) if xs else 0  # noqa: E731
        lines.append(f"| {r['rung']} | {r['task']} | {r['usable']}/{n} | {r['compiles']}/{n} | {r['selfcheck']}/{n} | "
                     f"{mean(r['calls'])} | {mean(r['seconds'])} |")
    return "\n".join(lines)


def ollama_ask_for_rung(rung):
    """The real thing: admit the rung through the VRAM governor, then ask it via Ollama (same 3500-token cap)."""
    import requests
    from ishti import vram
    from ishti.skills.skill_creator import skill as sc
    vram.admit(rung, role="coder", job="codegen_quality")

    def ask(prompt, **kw):
        r = requests.post(vram.OLLAMA + "/api/generate", json={
            "model": rung, "prompt": prompt, "stream": False, "keep_alive": "10m",
            "options": {"temperature": kw.get("temperature", 0.1), "num_predict": sc.MAX_OUTPUT_TOKENS}}, timeout=600).json()
        return r.get("response", "")
    return ask


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--rung", action="append", help="only this coder rung (repeatable); default: every installed rung")
    ap.add_argument("--task", action="append", choices=sorted(TASKS), help="only this task (repeatable)")
    a = ap.parse_args(argv)
    from ishti import vram
    rungs = a.rung or [r["name"] for r in vram.rungs("coder") if not r.get("cpu")]
    if not rungs:
        sys.exit("no coder rung is installed - nothing to measure")
    rows = run_matrix(ollama_ask_for_rung, rungs, {k: TASKS[k] for k in (a.task or TASKS)}, a.runs)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"measured_at": time.strftime("%Y-%m-%d %H:%M"), "rows": rows}, f, indent=1)
    print(table(rows))
    print(f"\nsaved to {OUT}")


if __name__ == "__main__":
    main()
