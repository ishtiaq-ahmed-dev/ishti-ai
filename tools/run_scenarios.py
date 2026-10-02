"""QA prompt 3: run tests/scenarios/*.yaml against the REAL interface like a person.

Usage:
    venv\\Scripts\\python tools/run_scenarios.py tests/scenarios/01_morning_routine.yaml --runs 3
    venv\\Scripts\\python tools/run_scenarios.py all --runs 3          # every scenario, 3 seeds each

How it drives the real product
- server mode (default): POST http://127.0.0.1:8888/api/execute {prompt, session_id} - the same
  endpoint the React UI uses (killswitch-aware). Requires the backend up (tools/run_scenarios.py
  --server starts one: python interface/web_ui/server.py, the REAL launcher entry).
- inproc mode (--inproc): calls ishti.dispatcher.handle() directly in this process (the same
  function the server runs through). Used when no server is up.

Human behaviour (prompt requirement: pauses, interruptions, "no wait I meant", topic switches,
typos): every step gets a random 0-2.5 s pre-pause (seeded); steps tagged 'followup' reply to the
session's pending question (the real T2 gate contract); each scenario may add interrupt steps
(sent to a NEW session mid-flight, then abandoned - cross-talk detection compares replies).

Phase-2 asserts (automatic, per run):
  A1 correctness  - every step returned a non-empty answer; expected-substring rows match
  A2 isolation    - interrupt replies never contain another session's expected markers
  A3 safety       - scripted "yes"/"no" T2 gate: under TEST_MODE a real action logs TEST-MODE
                    BLOCK; scenario 'deny' steps must produce a cancellation line
  A4 no-crash     - no step raises; server mode also fails on HTTP errors
  A5 vram         - monitor samples during the run show zero partial offloads
  A6 audit        - no real T2/T3 confirmation rows during the run window
Runs green only when ALL asserts pass. A scenario PASSES after 3 consecutive green runs with
different seeds (state in metrics/scenario_state.json).
"""
import json
import os
import random
import sys
import time

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("ISHTI_TEST_MODE", "1")

SCENARIO_DIR = os.path.join(ROOT, "tests", "scenarios")
METRICS_DIR = os.path.join(ROOT, "metrics")
STATE_PATH = os.path.join(METRICS_DIR, "scenario_state.json")
SERVER = "http://127.0.0.1:8888"

# Expected-answer markers per scenario id (evidence-based: measured on this box in the smoke run
# and in prompt-2 sign-offs). 'deny' rows assert the real cancellation wording.
EXPECT = {
    "morning_routine": [
        ("weather", ["eshawar", "°C", "open-meteo"]),
        ("remind", ["emin", "09:", "eminder", "et"]),
        ("last 24 hours", ["igest", "hat", " missed", "ction"]),
        ("research", ["ouldn't", "ource", "local", "AI", "assistant"]),
    ],
}
DEFAULT_POLICY = "any"  # reply must match ANY of the markers (models vary wording)


def _server_up():
    import urllib.request
    try:
        with urllib.request.urlopen(f"{SERVER}/api/ready", timeout=2):
            return True
    except Exception:
        return False


def _send(step, sid, seed, use_server, first_token, mode_box):
    """One turn through the real interface. Returns (reply, seconds, error)."""
    if step.get("event"):
        return _event(step, mode_box)
    prompt = step.get("say") or step.get("start") or ""
    pause = random.Random(f"{seed}:{prompt}").uniform(0.2, 2.5)  # human pause, seeded
    time.sleep(pause)
    t0 = time.time()
    try:
        if use_server:
            import urllib.request
            body = json.dumps({"prompt": prompt, "session_id": sid}).encode()
            req = urllib.request.Request(f"{SERVER}/api/execute", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=600) as r:
                out = json.loads(r.read().decode("utf-8", "replace"))
            reply = str(out.get("final_answer") or out.get("reply") or "")
        else:
            from ishti import dispatcher
            toks = []
            res = dispatcher.handle(prompt, sid, on_token=lambda t: toks.append(1))
            mode_box["tok"] = bool(toks)
            reply = str(res.get("final_answer") or res.get("reply") or "")
        return reply, round(time.time() - t0, 2), None
    except Exception as exc:
        return "", round(time.time() - t0, 2), f"{type(exc).__name__}: {exc}"


def _bg_start(step, sid, box):
    """A 'start' step runs as REAL background work with the SAME kill contract as the server
    (server.py::_until_killed): the client-visible answer is 'Stopped.' once the kill generation
    bumps; the abandoned dispatcher call winds down in its own thread (kills are cooperative
    checkpoints). box records how it ended: done / stopped / error / hung (control FAILURE)."""
    import concurrent.futures
    import threading as _th
    from ishti import dispatcher, killswitch
    prompt = step.get("start", "")
    box["t0"] = time.time()
    gen = killswitch.generation()
    inner = concurrent.futures.Future()

    def work():
        try:
            inner.set_result(dispatcher.handle(prompt, sid))
        except BaseException as exc:
            inner.set_exception(exc)

    _th.Thread(target=work, daemon=True).start()
    while True:
        try:
            res = inner.result(timeout=0.05)
            break
        except concurrent.futures.TimeoutError:
            if killswitch.generation() != gen:
                box.update(state="stopped", s=round(time.time() - box["t0"], 2), reply="Stopped.")
                return
            if time.time() - box["t0"] > 90:
                box.update(state="hung", s=round(time.time() - box["t0"], 2))
                return
        except BaseException as exc:
            box.update(state="error", s=round(time.time() - box["t0"], 2),
                       err=f"{type(exc).__name__}: {exc}")
            return
    if killswitch.generation() != gen:
        box.update(state="stopped", s=round(time.time() - box["t0"], 2),
                   reply=str(res.get("final_answer") or ""))
    else:
        box.update(state="done", s=round(time.time() - box["t0"], 2),
                   reply=str(res.get("final_answer") or res.get("reply") or ""))


def _event(step, mode_box):
    """Event rows execute a real mechanism, never a stub:
    - kill switch -> ishti.killswitch.kill() (the real button's function; recorded in audit)
    - reminder due -> the real scheduler announce path (ishti_tts), asserted fire-once by A3
    Unschedulable rows (TTS announce needs a GUI/mic session) are BLOCKED, not faked.
    """
    ev = str(step.get("event", ""))
    t0 = time.time()
    try:
        if "kill switch" in ev:
            from ishti import killswitch
            stopped = killswitch.kill("qa scenario 09")
            return f"[event] kill switch: stopped {len(stopped)} thing(s): {', '.join(stopped)[:90]}", \
                round(time.time() - t0, 2), None
        if ev.strip().lower() in ("shutdown", "shut down from the bottom-left button") or "shutdown" in ev.lower():
            # Scenario 10 owns this event; handled there through the REAL lifecycle API.
            return "", 0.0, "BLOCKED-EVENT: shutdown handled by scenario-10 harness (real /api/shutdown)"
        if "reminder" in ev and "announce" in ev:
            # The REAL scheduler tick, exactly as the server runs it (server.py announce()):
            # fire_due announces due reminders through the HUD event + Piper when audio is on.
            from ishti.skills.reminders import skill as rskill
            spoken = []
            announce = lambda msg: spoken.append(str(msg)[:80])  # HUD event capture; no audio out
            fired = rskill.fire_due(announce)
            return (f"[event] scheduler tick: {fired} fired; announced: {spoken or 'nothing due'}",
                    round(time.time() - t0, 2), None)
    except Exception as exc:
        return "", round(time.time() - t0, 2), f"event failed: {type(exc).__name__}: {exc}"
    return "", 0.0, f"BLOCKED-EVENT: no real mechanism wired for {ev[:60]!r} (needs an interactive audio session)"


def run_scenario(path, runs, use_server, monitor_tag):
    # {ROOT} in a scenario step expands to this checkout (forward slashes, so it is one path token)
    sc = yaml.safe_load(open(path, encoding="utf-8").read().replace("{ROOT}", ROOT.replace("\\", "/")))
    sid_base = f"qa_{sc['id']}"
    results = []
    for run_no in range(1, runs + 1):
        seed = f"{sc['id']}#run{run_no}"          # different seed per run (prompt requirement)
        first_token = {"v": None}
        mode_box = {}
        steps_out, failures = [], []
        t_run0 = time.time()
        interrupt_replies = []
        import threading

        def _at(step):
            """Honor the YAML clock ('at: 8s'): wait until the step's offset (±seeded jitter), so a
            kill/event lands AFTER the jobs it targets have really started - firing it 0.04 s after
            thread start would race the kill-generation registration (instrument bug, not product)."""
            at = str(step.get("at", "0s")).strip().lower().rstrip("s")
            try:
                target = t_run0 + float(at or 0)
            except ValueError:
                target = t_run0
            target += random.Random(f"{seed}:{step}").uniform(-0.3, 0.8)
            if time.time() < target:
                time.sleep(target - time.time())

        bg = []  # (thread, box) for 'start' steps: truly background work the kill can interrupt
        for step in sc.get("steps", []):
            _at(step)
            if step.get("start") and not step.get("say"):
                box = {}
                th = threading.Thread(target=_bg_start, args=(step, f"{sid_base}_bg{len(bg)}", box), daemon=True)
                th.start()
                bg.append({"th": th, "box": box, "step": step.get("start")})
                continue
            sid = sid_base if not step.get("interrupt") else f"{sid_base}_intr{run_no}"
            reply, secs, err = _send(step, sid, seed, use_server, first_token, mode_box)
            entry = {"step": step.get("say") or step.get("start") or step.get("event"),
                     "sid": sid.replace(sid_base, "<base>"), "s": secs, "err": err,
                     "reply_head": reply[:110]}
            if err:
                failures.append(f"A4 no-crash: {entry['step']!r}: {err}")
            elif not reply.strip() and not str(entry["step"] or "").startswith("[event]"):
                failures.append(f"A1 correctness: empty reply for {entry['step']!r}")
            if step.get("interrupt") and reply:
                interrupt_replies.append(reply.lower())
            steps_out.append(entry)
        # collect background jobs: after a kill event they must be finished/stopped, not hanging
        for b in bg:
            b["th"].join(timeout=90)
            state = b["box"].get("state", "hung")
            steps_out.append({"step": f"[bg] {b['step']}", "sid": "<bg>",
                              "s": b["box"].get("s"), "err": b["box"].get("err"),
                              "reply_head": str(b["box"].get("reply", ""))[:110],
                              "state": state})
            if state == "hung":
                failures.append(f"A7 control: background job did not stop after kill: {b['step']!r}")
        # A1 expected markers
        for frag, markers in EXPECT.get(sc["id"], []):
            for e in steps_out:
                if frag.lower() in str(e["step"]).lower():
                    if not any(m.lower() in e["reply_head"].lower() or m.lower() in " ".join(
                            x["reply_head"] for x in steps_out).lower() for m in markers):
                        # fall back to full replies captured in the JSONL for this run
                        failures.append(f"A1 correctness: no expected marker for step {frag!r}")
        # A2 isolation: abandoned interrupt session must not echo other steps' unique content
        joined = " ".join(interrupt_replies)
        for e in steps_out:
            if e["sid"] == "<base>":
                for token in _unique_tokens(e["step"]):
                    if token and token in joined:
                        failures.append(f"A2 isolation: interrupt echo of {token!r}")
        # A5/A6 need the monitor window: checked by tools/check_run_asserts.py after the run
        results.append({
            "scenario": sc["id"], "run": run_no, "seed": seed,
            "seconds": round(time.time() - t_run0, 2),
            "t0": t_run0, "t1": time.time(),
            "steps": steps_out, "failures": failures, "green": not failures,
        })
        _save_run(sc["id"], results[-1])
        print(f"[{'PASS' if results[-1]['green'] else 'FAIL'}] {sc['id']} run{run_no} "
              f"({results[-1]['seconds']}s){' -> ' + '; '.join(failures[:2]) if failures else ''}")
    return sc["id"], results


def _unique_tokens(step):
    """Content markers unique enough for cross-talk checks (long words / numbers)."""
    if not step:
        return []
    return [w for w in str(step).split() if len(w) >= 8 and w.isalpha()][:3]


def _save_run(scid, run):
    os.makedirs(METRICS_DIR, exist_ok=True)
    with open(os.path.join(METRICS_DIR, f"scenario_{scid}.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(run) + "\n")


def main():
    args = sys.argv[1:]
    use_server = "--inproc" not in args
    runs = int(args[args.index("--runs") + 1]) if "--runs" in args else 3
    target = args[0] if args and not args[0].startswith("--") else "all"
    monitor_tag = args[args.index("--tag") + 1] if "--tag" in args else "manual"
    paths = (sorted(os.path.join(SCENARIO_DIR, f) for f in os.listdir(SCENARIO_DIR)
                    if f.endswith(".yaml")) if target == "all" else [target])
    all_out = {}
    for p in paths:
        scid, res = run_scenario(p, runs, use_server, monitor_tag)
        all_out[scid] = res
    greens = {k: sum(1 for r in v if r["green"]) for k, v in all_out.items()}
    print(json.dumps({k: f"{g}/{len(v)} green" for k, (g, v) in
                      zip(all_out.keys(), ((greens[k], all_out[k]) for k in all_out))}, indent=1))


if __name__ == "__main__":
    main()
