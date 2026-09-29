"""Real-hardware tests for the VRAM governor + lifecycle (docs/API_CONTRACT.md, models_ladder.yaml). NOT pytest: it drives
the real GPU, Ollama and backend processes and prints its evidence. ISHTI itself must not be running.

    venv\\Scripts\\python tests\\e2e_vram_lifecycle.py t1a t1b t2 t3 t4 t5 [n] t5b perf

  t1a  heavy vision load (default 80% budget) with the planner resident -> planner downshifts, vision loads FULLY, upshift after
  t1b  same with a 95% budget and the tier required (strict) -> the planner goes down two rungs, the 12B tier loads FULLY
  t2   VRAM pressure (a GPU hog process + an oversized model): /api/ps never shows a partial offload; the watchdog catches a raw one
  t3   live voice priority: the planner isn't touched by heavy jobs during a voice turn; the STT load may downshift it
  t4   boot while Ollama is killed: /api/ready reports a plain-language error, then recovers
  t5   n x start -> ready -> POST /api/shutdown: no orphan processes, ports free, SQLite consistent
  t5b  Job Object: killing a process hard (no cleanup) takes its children down with it
  perf cold/warm boot times, planner first-token latency, event-loop latency, voice-turn latency"""
import base64
import io
import json
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time

import psutil
import requests

ROOT = r"D:\ISHTI"
sys.path.insert(0, ROOT)
from ishti import events, lifecycle, vram  # noqa: E402

PY = os.path.join(ROOT, "venv", "Scripts", "python.exe")
RESULTS = os.path.join(ROOT, "data", "e2e_vram_lifecycle.json")
OK, BAD = "PASS", "FAIL"
log = []


def say(*a):
    print(*a, flush=True)


def record(name, ok, detail):
    log.append({"test": name, "result": OK if ok else BAD, "detail": detail})
    say(f"\n>>> {name}: {OK if ok else BAD} - {detail}")
    old = json.load(open(RESULTS, encoding="utf-8")) if os.path.isfile(RESULTS) else []
    json.dump([r for r in old if r["test"] != name] + [log[-1]], open(RESULTS, "w", encoding="utf-8"), indent=1)


# ---------------------------------------------------------------- helpers
def use_ladder(**over):
    """A temp copy of models_ladder.yaml with some top-level values overridden (short hysteresis, other budget)."""
    import yaml
    c = yaml.safe_load(open(os.path.join(ROOT, "models_ladder.yaml"), encoding="utf-8"))
    c.update(over)
    path = os.path.join(tempfile.mkdtemp(), "ladder.yaml")
    yaml.safe_dump(c, open(path, "w", encoding="utf-8"), sort_keys=False)
    vram.LADDERS = path
    vram._cache.pop("cfg", None)
    return path


def fresh():
    vram.reset_state()
    for m in vram.ps(True, strict=False):
        vram.unload(m["name"])
    time.sleep(1.0)


class Watch:
    """Polls Ollama's /api/ps every 150 ms: any model whose size_vram < size is a violation."""
    def __init__(self):
        self.viol, self.max_used, self.n, self._stop = [], 0, 0, threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.n += 1
                for m in vram.ps(True):
                    if not m["full"]:
                        self.viol.append((round(time.time() % 1000, 1), m["name"], m["size_vram_mb"], m["size_mb"]))
                self.max_used = max(self.max_used, vram.gpu()["used_mb"])
            except Exception:
                pass
            time.sleep(0.15)

    def stop(self):
        self._stop.set()
        return self.viol


def picture():
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (400, 200), "white")
    ImageDraw.Draw(im).text((30, 70), "PRICE 42", fill="black", font_size=64)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    p = os.path.join(tempfile.mkdtemp(), "shot.png")
    open(p, "wb").write(buf.getvalue())
    return p


def state_line(label):
    g = vram.gpu()
    loaded = ", ".join(f"{m['name']} {m['size_vram_mb']}/{m['size_mb']}{'' if m['full'] else ' PARTIAL'}" for m in vram.ps(True, strict=False)) or "-"
    say(f"  [{label}] GPU {g['used_mb']} MiB ({100 * g['used_mb'] // g['total_mb']}%) | {loaded} | planner rung {vram.current_rung('planner')}")


def switches():
    got = []
    events.subscribe(lambda m: got.append(m["data"]) if m["type"] == "model_switch" else None)
    return got


def first_token_ms(role="planner", n=3):
    from ishti import llm
    xs = []
    for _ in range(n):
        t0, box = time.time(), []
        llm.chat([{"role": "user", "content": "Reply with the single word: ok"}], role=role, system=None, temperature=0, max_tokens=3,
                 on_token=lambda t: box or box.append(time.time() - t0))
        xs.append(1000 * (box[0] if box else time.time() - t0))
    return statistics.median(xs)


# ---------------------------------------------------------------- t1
def t1(strict, budget, name):
    from ishti import llm
    from ishti.skills.video.pro import look
    use_ladder(hysteresis_s=8, budget_pct=budget)
    fresh()
    vram.start_sampler()
    sw, w = switches(), Watch()
    r = vram.admit("ishti-qwen2.5:14b", role="planner", job="start")
    assert r["ok"], r
    state_line("planner resident")
    ttft0 = first_token_ms()
    role, model = "vision3", "gemma3:12b"
    t0 = time.time()
    with vram.heavy(model, role=role, job="analyse a dense page", strict=strict, wait_s=20) as h:
        say(f"  heavy admit ok={h['ok']} loaded={h.get('model')} waited={h['waited_s']}s actions={[a['do'] + ' ' + str(a.get('model', a.get('num_ctx', ''))) for a in h['actions']]}")
        if not h["ok"]:
            record(name, False, f"queued: {h['queued']}")
            return w.stop()
        state_line("during the heavy job")
        answer = look(picture(), "What number is written in this image? Reply with just the number.", role=role).get("text", "")
        say(f"  vision answer: {answer!r} (model {llm.model_for(role)})")
        planner_ans = llm.ask("Reply with the single word: ok", role="planner", system=None, temperature=0, max_tokens=3)
        say(f"  planner still answers during the job: {planner_ans!r} on {llm.model_for('planner')}")
        full = all(m["full"] for m in vram.ps(True))
        names = {m["name"] for m in vram.ps(True)}
        both = llm.model_for("planner") in names and h["model"] in names  # the planner AND the vision model really are resident together
        say(f"  resident together: {sorted(names)} -> {both}")
        rung_during = vram.current_rung("planner")
    say(f"  job window {time.time() - t0:.1f} s; planner rung during job: {rung_during}")
    end = time.time() + 120
    while time.time() < end and vram.current_rung("planner") != 0:
        time.sleep(1)
    up = vram.current_rung("planner") == 0
    time.sleep(1.0)
    state_line("after the job")
    viol = w.stop()
    ok = (bool(h["ok"]) and full and both and "42" in answer and rung_during >= 1 and up and not viol
          and all(m["full"] for m in vram.ps(True)) and any(m["name"] == "ishti-qwen2.5:14b" for m in vram.ps(True)))
    for s in sw:
        say(f"  model_switch: {s['role']:9} {s['from']} -> {s['to']} | {s['reason']}")
    record(name, ok, f"loaded {h.get('model')} fully on GPU; planner rung during job {rung_during}, back to rung 0 after {time.time() - t0:.0f} s; "
                     f"vision answer {answer.strip()!r}; {w.n} /api/ps samples, partial offloads: {len(viol)}; peak GPU {w.max_used} MiB "
                     f"({100 * w.max_used // vram.gpu()['total_mb']}%); planner first token {ttft0:.0f} ms")
    return viol


def t1a():
    return t1(False, 80, "T1a heavy vision load, default 80% budget")


def t1b():
    return t1(True, 95, "T1b tier-3 vision required (strict), 95% budget")


# ---------------------------------------------------------------- t2
HOG = "import torch,time,sys; x=torch.empty(int(float(sys.argv[1])*2**30),dtype=torch.uint8,device='cuda'); x.fill_(1); print('ready',flush=True); time.sleep(900)"


def hog(gb):
    p = subprocess.Popen([PY, "-c", HOG, str(gb)], stdout=subprocess.PIPE, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert p.stdout.readline().strip() == "ready"
    time.sleep(1.0)
    return p


def t2():
    use_ladder(hysteresis_s=8, budget_pct=80)
    w, results = Watch(), []
    for gb, expect in ((3, "planner fits at a lighter rung"), (7, "planner at the smallest rung"), (12, "nothing fits: queued with a reason")):
        fresh()
        p = hog(gb)
        try:
            base = vram.gpu()["used_mb"]
            r = vram.admit("ishti-qwen2.5:14b", role="planner", job=f"start with a {gb} GB hog", wait_s=6)
            ok = r["ok"]
            model = r.get("model") if ok else None
            say(f"  hog {gb} GB (GPU {base} MiB used before): admit ok={ok} model={model} queued={r.get('queued')}")
            state_line(f"hog {gb} GB")
            results.append((gb, ok, model, r.get("queued")))
        finally:
            p.kill()
            p.wait()
        time.sleep(1.0)
    fresh()
    # an oversized model through the governor: refused, never attempted
    big = vram.admit("qwen-27b:latest", job="an oversized model", wait_s=4)
    say(f"  qwen-27b (19 GB) via the governor: ok={big['ok']} queued={big.get('queued')}")
    loaded_after = [m["name"] for m in vram.ps(True)]
    # CONTROL: the same load straight through Ollama (what used to happen). The partial-offload watch must catch it.
    vram.start_sampler()
    raw = {"seen": False, "gone_after": None}
    say("  control: loading qwen-27b directly (no governor) ...")
    t0 = time.time()
    try:
        requests.post(vram.OLLAMA + "/api/generate", json={"model": "qwen-27b:latest", "prompt": "", "keep_alive": "5m", "stream": False}, timeout=240)
    except requests.RequestException as exc:
        say(f"  control load ended with {type(exc).__name__}: {str(exc)[:100]}")
    seen_at = None
    end = time.time() + 15
    while time.time() < end:
        loaded = vram.ps(True, strict=False)
        bad = [m for m in loaded if not m["full"]]
        if bad and seen_at is None:
            seen_at = time.time()
            say(f"  control: PARTIAL OFFLOAD appeared: {bad[0]['name']} {bad[0]['size_vram_mb']}/{bad[0]['size_mb']} MiB")
        if seen_at and not any(m["name"].startswith("qwen-27b") for m in loaded):
            raw["gone_after"] = round(time.time() - seen_at, 1)
            break
        time.sleep(0.3)
    viol = w.stop()
    governed_viol = [v for v in viol if not v[1].startswith("qwen-27b")]
    ok = (results[0][1] and results[1][1] and not results[2][1] and not big["ok"] and not any(m.startswith("qwen-27b") for m in loaded_after)
          and not governed_viol)
    record("T2 VRAM pressure", ok,
           f"hog 3 GB -> planner {results[0][2]}; hog 7 GB -> planner {results[1][2]}; hog 12 GB -> "
           f"{'queued: ' + str(results[2][3])[:90] if not results[2][1] else 'loaded ' + str(results[2][2])}; qwen-27b via governor refused={not big['ok']}; "
           f"partial offloads while governed: {len(governed_viol)}; control (raw Ollama load of qwen-27b): "
           f"{'partial offload seen and unloaded by the watch after ' + str(raw['gone_after']) + ' s' if seen_at else 'no partial offload appeared (Ollama refused/crashed the load)'}")
    for m in vram.ps(True, strict=False):
        vram.unload(m["name"])


# ---------------------------------------------------------------- t3
def t3():
    from ishti import llm, voice
    use_ladder(hysteresis_s=8)
    fresh()
    vram.start_sampler()
    w = Watch()
    assert vram.admit("ishti-qwen2.5:14b", role="planner", job="start")["ok"]
    base = first_token_ms(n=5)
    say(f"  planner first-token latency, idle: {base:.0f} ms")
    res, lat = {}, []

    def heavy_job():
        res["h"] = vram.admit("gemma3:12b", role="vision3", job="analyse a page during a voice turn", wait_s=10)
    with vram.voice_turn():
        th = threading.Thread(target=heavy_job)
        th.start()
        while th.is_alive():
            lat.append(first_token_ms(n=1))
        th.join()
        rung_during_voice = vram.current_rung("planner")
        planner_model = llm.model_for("planner")
    h = res["h"]
    say(f"  during the voice turn: planner rung {rung_during_voice} ({planner_model}); heavy job ok={h['ok']} queued={str(h.get('queued'))[:120]}")
    worst = max(lat) if lat else 0
    say(f"  planner first token while the heavy job was waiting: median {statistics.median(lat):.0f} ms, worst {worst:.0f} ms (idle {base:.0f})")
    # the voice pipeline's OWN load (Whisper) may take the planner down a rung: voice has priority
    voice.release_model()
    t0 = time.time()
    voice.stt_model()
    load_s = time.time() - t0
    state_line("whisper loaded")
    rung_after_stt = vram.current_rung("planner")
    stt_ok = voice.is_stt_loaded()
    say(f"  whisper load {load_s:.1f} s; planner rung {rung_after_stt}")
    voice.release_model()  # the STT's idle eviction
    end = time.time() + 90
    while time.time() < end and vram.current_rung("planner") != 0:
        time.sleep(1)
    up = vram.current_rung("planner") == 0
    viol = w.stop()
    ok = (rung_during_voice == 0 and not h["ok"] and worst <= base * 1.6 + 300 and stt_ok and not viol and up)
    record("T3 live-voice priority", ok, f"heavy job during a voice turn: {'queued (' + str(h.get('queued'))[:70] + ')' if not h['ok'] else 'loaded ' + str(h.get('model'))}; "
           f"planner untouched (rung {rung_during_voice}); planner first token idle {base:.0f} ms vs worst during the job {worst:.0f} ms; "
           f"whisper loaded in {load_s:.1f} s with planner rung {rung_after_stt} (voice has priority); upshift after STT release: {up}; partial offloads: {len(viol)}")


# ---------------------------------------------------------------- backend processes (t4 / t5 / perf)
PORT, OPORT = 8896, 11500
BASE = f"http://127.0.0.1:{PORT}"
OURL = f"http://127.0.0.1:{OPORT}"


def start_backend(ollama_url=OURL, planner="qwen2.5:3b"):
    """A test backend on :8896 with its own Ollama. The lifecycle tests pin the planner to the 3B (ISHTI_MODEL_PLANNER) so they
    don't depend on what else is using the GPU right now (another session's 14B); the 14B is exercised by t1/t2/t3/perf."""
    env = {**os.environ, "ISHTI_OLLAMA_URL": ollama_url, "PYTHONIOENCODING": "utf-8"}
    if planner:
        env["ISHTI_MODEL_PLANNER"] = planner
    return subprocess.Popen([PY, "-m", "ishti", str(PORT)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)), time.time()


def get_ready():
    try:
        return requests.get(BASE + "/api/ready", timeout=2).json()
    except (requests.RequestException, ValueError):
        return None


def registry():
    return lifecycle.registry(port=PORT)


def wait_gone(pid, secs=40):
    end = time.time() + secs
    while time.time() < end and psutil.pid_exists(pid):
        time.sleep(0.3)
    return not psutil.pid_exists(pid)


def snapshot_pids():
    return {p.pid: p for p in psutil.process_iter(["name", "cmdline", "ppid"])}


def orphans(before):
    """Processes that did not exist before and belong to ISHTI/Ollama: the server (python -m ishti), ollama, llama-server runners,
    MCP node servers. A runner counts when its parent is gone (a live parent would be the other, untouched Ollama)."""
    out = []
    listeners = {c.pid for c in psutil.net_connections(kind="inet") if c.status == "LISTEN" and c.laddr.port == OPORT}
    for pid, p in snapshot_pids().items():
        if pid in before:
            continue
        try:
            n, cmd = (p.info["name"] or "").lower(), " ".join(p.info["cmdline"] or [])
        except psutil.Error:
            continue
        # exactly OUR test backend (`-m ishti 8896`, not another session's `-m ishti.check`), an `ollama serve` that still listens
        # on OUR test port, a runner whose Ollama is gone, and Playwright MCP node servers
        if (n.startswith("python") and cmd.endswith(f"-m ishti {PORT}")) or (n.startswith("ollama") and "serve" in cmd and pid in listeners) or \
                (n.startswith("llama-server") and not psutil.pid_exists(p.info["ppid"] or 0)) or (n.startswith("node") and "playwright" in cmd.lower()):
            out.append((pid, n, cmd[:80]))
    return out


def descendants(pid):
    """{pid: (create_time, name, cmd)} of a process and everything under it."""
    out = {}
    try:
        root = psutil.Process(pid)
        for p in [root] + root.children(recursive=True):
            out[p.pid] = (p.create_time(), p.name().lower(), " ".join(p.cmdline())[:70])
    except psutil.Error:
        pass
    return out


def alive_same(pid, ct):
    try:
        return abs(psutil.Process(pid).create_time() - ct) < 1.0
    except psutil.Error:
        return False


def free_gpu_for_backend():
    """The backend under test loads the planner itself: hand it an empty GPU (the untouched Ollama on :11434 has nothing pinned)."""
    for url in ("http://127.0.0.1:11434",):
        try:
            for m in requests.get(url + "/api/ps", timeout=3).json().get("models", []):
                requests.post(url + "/api/generate", json={"model": m["name"], "prompt": "", "keep_alive": 0, "stream": False}, timeout=30)
        except requests.RequestException:
            pass
    time.sleep(1.5)


def t4():
    free_gpu_for_backend()
    before = snapshot_pids()
    proc, t0 = start_backend()
    seen, killed_at, err_seen, err_msg, log_ok, recovered = [], None, None, None, None, None
    end = time.time() + 240
    while time.time() < end:
        r = get_ready()
        if r:
            stage_now = r["stage"]
            if not seen or seen[-1] != (stage_now, r["error"] is not None):
                seen.append((stage_now, r["error"] is not None))
                say(f"  +{time.time() - t0:5.1f}s stage={stage_now} progress={r['progress']}% error={str(r['error'])[:90] if r['error'] else None}")
            done = [s["id"] for s in r["stages"] if s["done"]]
            if killed_at is None and "memory_loaded" in done:  # Ollama is up and the boot is in its middle: kill it
                pid = registry().get("ollama_pid")
                say(f"  killing our Ollama (pid {pid}) mid-boot")
                lifecycle.kill_tree(pid)
                killed_at = time.time()
            if r["error"] and err_seen is None:
                err_seen, err_msg = time.time() - killed_at if killed_at else None, r["error"]
                log_ok = os.path.isfile(r.get("log_path", ""))
            if killed_at and err_seen is not None and r["ready"]:
                recovered = time.time() - killed_at
                break
        time.sleep(0.25)
    requests.post(BASE + "/api/shutdown", json={}, timeout=5) if get_ready() else None
    gone = wait_gone(proc.pid)
    ok = bool(err_msg) and "Ollama" in err_msg and "Traceback" not in err_msg and log_ok and gone
    record("T4 boot with Ollama killed", ok, f"error reported {err_seen:.1f} s after the kill: {err_msg!r}; log file present: {log_ok}; "
           f"{'recovered (ready) ' + format(recovered, '.0f') + ' s after the kill' if recovered else 'did not recover within the window'}; "
           f"backend exited cleanly: {gone}; leftovers: {orphans(before)}")


def t5(n=20):
    free_gpu_for_backend()
    times, problems, not_ready = [], [], []
    integ = [os.path.join(ROOT, "data", f) for f in ("ishti.db", "memory.db", "audit.db")] + [os.path.join(ROOT, "data", "chroma", "chroma.sqlite3")]
    for i in range(1, n + 1):
        before = snapshot_pids()
        proc, t0 = start_backend()
        end = time.time() + 240
        r = None
        while time.time() < end:
            r = get_ready()
            if r and r["ready"]:
                break
            time.sleep(0.4)
        if not (r and r["ready"]):
            not_ready.append(f"#{i}: {r and r['error']}")
            say(f"  #{i:2} NOT ready after 240 s ({r and r['error']}) - shutting it down anyway (the lifecycle must be clean in every state)")
            family = descendants(proc.pid)
            requests.post(BASE + "/api/shutdown", json={}, timeout=5)
            gone = wait_gone(proc.pid)
            time.sleep(2.0)
            left = [(pid, n, c) for pid, (ct, n, c) in family.items() if alive_same(pid, ct)]
            if not gone or left:
                problems.append(f"#{i}: (not ready) gone={gone} leftovers={left}")
            continue
        boot = time.time() - t0
        reg = registry()
        family = descendants(proc.pid)  # the server, its Ollama, the runners, MCP node servers... exactly what must be gone
        say(f"        (family before shutdown: {sorted({n for _, n, _ in family.values()})})")
        t1 = time.time()
        requests.post(BASE + "/api/shutdown", json={}, timeout=5)
        gone = wait_gone(proc.pid)
        took = time.time() - t1
        time.sleep(2.0)  # runners of the stopped Ollama go with the Job Object
        left = [(pid, n, c) for pid, (ct, n, c) in family.items() if alive_same(pid, ct)]
        left += [o for o in orphans(before) if o[0] not in {x[0] for x in left}] if left else []
        ports = [c.laddr.port for c in psutil.net_connections(kind="inet") if c.status == "LISTEN" and c.laddr.port in (PORT, OPORT)]
        bad_db = []
        for path in integ:
            if os.path.isfile(path):
                try:
                    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10) as c:
                        if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            bad_db.append(os.path.basename(path))
                except sqlite3.Error as exc:
                    bad_db.append(f"{os.path.basename(path)}:{exc}")
        regs_clear = not registry().get("ollama_pid") and not registry().get("server_pid")
        times.append((boot, took))
        flag = "" if gone and not left and not ports and not bad_db and regs_clear else "  <-- PROBLEM"
        say(f"  #{i:2} ready in {boot:4.1f} s, shutdown {took:4.1f} s, server gone={gone}, orphans={len(left)}, ports open={ports}, db problems={bad_db}, registry clear={regs_clear}{flag}")
        if flag:
            say(f"        registry after exit: {json.dumps(registry())}")
            problems.append(f"#{i}: gone={gone} orphans={left} ports={ports} db={bad_db} registry_clear={regs_clear}")
        del reg
    ok = not problems and len(times) + len(not_ready) == n
    record(f"T5 {n}x start/shutdown", ok, f"{n - len(problems)}/{n} cycles clean (not ready under GPU contention: {len(not_ready)}); boot median {statistics.median(t[0] for t in times):.1f} s (min {min(t[0] for t in times):.1f}, "
           f"max {max(t[0] for t in times):.1f}); shutdown median {statistics.median(t[1] for t in times):.1f} s (max {max(t[1] for t in times):.1f}); "
           f"leftover processes from the server's own family: {'see problems' if problems else 0}; problems: {problems or 'none'}; boot findings: {not_ready or 'none'}")


def t5b():
    """Hard kill (no cleanup runs): the Job Object must still take the children down."""
    code = ("import os,subprocess,sys,time\nsys.path.insert(0,r'D:\\ISHTI')\nfrom ishti import lifecycle\n"
            "print(lifecycle.adopt_job(),flush=True)\n"
            "kids=[subprocess.Popen(['ping','-n','300','127.0.0.1'],stdout=subprocess.DEVNULL) for _ in range(2)]\n"
            "print(' '.join(str(k.pid) for k in kids),flush=True)\ntime.sleep(300)\n")
    p = subprocess.Popen([PY, "-c", code], stdout=subprocess.PIPE, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    active = p.stdout.readline().strip() == "True"
    kids = [int(x) for x in p.stdout.readline().split()]
    alive_before = all(psutil.pid_exists(k) for k in kids)
    subprocess.run(["taskkill", "/F", "/PID", str(p.pid)], capture_output=True)  # no atexit, no finally: a crash
    time.sleep(1.5)
    gone = not any(psutil.pid_exists(k) for k in kids)
    record("T5b Job Object crash test", active and alive_before and gone, f"job active={active}; children {kids} alive before={alive_before}; "
           f"after the parent was hard-killed they are gone={gone}")


# ---------------------------------------------------------------- t6
def listener_pid(port):
    return next((c.pid for c in psutil.net_connections(kind="inet") if c.status == "LISTEN" and c.laddr.port == port), None)


def t6():
    """The controlled 'apply Ollama settings' path: an Ollama ISHTI started is restarted with the new env; one it did not start
    is never touched (it reports needs_restart and how)."""
    free_gpu_for_backend()
    proc, t0 = start_backend()
    while time.time() - t0 < 240:
        r = get_ready()
        if r and r["ready"]:
            break
        time.sleep(0.5)
    s0 = requests.get(BASE + "/api/ollama/settings", timeout=10).json()
    pid0 = listener_pid(OPORT)
    saved = requests.post(BASE + "/api/ollama/settings", json={"num_parallel": 2, "max_loaded_models": 3}, timeout=10).json()
    say(f"  ours={s0['ours']} active={s0['active']}; saved {saved['settings']['num_parallel']}/{saved['settings']['max_loaded_models']} needs_restart={saved['needs_restart']}")
    applied = requests.post(BASE + "/api/ollama/apply", json={}, timeout=120).json()
    pid1 = listener_pid(OPORT)
    after = requests.get(BASE + "/api/ollama/settings", timeout=10).json()
    say(f"  apply -> {applied['applied']} restarted={applied['restarted']}; Ollama pid {pid0} -> {pid1}; active env {after['active_env']}")
    still_ready = get_ready()["ready"]
    requests.post(BASE + "/api/ollama/settings", json={"num_parallel": 1, "max_loaded_models": 4}, timeout=10)
    requests.post(BASE + "/api/ollama/apply", json={}, timeout=120)  # put the defaults back
    restored = requests.get(BASE + "/api/ollama/settings", timeout=10).json()
    requests.post(BASE + "/api/shutdown", json={}, timeout=5)
    gone = wait_gone(proc.pid)
    ours_ok = (s0["ours"] and applied["restarted"] and pid1 and pid1 != pid0 and after["active_env"].get("OLLAMA_NUM_PARALLEL") == "2"
               and after["active_env"].get("OLLAMA_MAX_LOADED_MODELS") == "3" and "OLLAMA_NUM_GPU" not in after["active_env"] and still_ready
               and restored["active_env"].get("OLLAMA_NUM_PARALLEL") == "1" and gone)
    # an Ollama ISHTI did NOT start: must be left alone
    real_pid = listener_pid(11434)
    proc2, t1 = start_backend("http://127.0.0.1:11434")
    while time.time() - t1 < 240:
        r = get_ready()
        if r and r["ready"]:
            break
        time.sleep(0.5)
    s1 = requests.get(BASE + "/api/ollama/settings", timeout=10).json()
    requests.post(BASE + "/api/ollama/settings", json={"num_parallel": 2}, timeout=10)
    refused = requests.post(BASE + "/api/ollama/apply", json={}, timeout=60).json()
    real_pid2 = listener_pid(11434)
    requests.post(BASE + "/api/ollama/settings", json={"num_parallel": 1}, timeout=10)
    requests.post(BASE + "/api/shutdown", json={}, timeout=5)
    wait_gone(proc2.pid)
    real_alive = psutil.pid_exists(real_pid or 0) and listener_pid(11434) == real_pid
    say(f"  not-ours Ollama: ours={s1['ours']}; apply -> applied={refused['applied']} needs_restart={refused.get('needs_restart')}; pid {real_pid} -> {real_pid2}; still running after ISHTI exited: {real_alive}")
    not_ours_ok = (not s1["ours"]) and refused["applied"] is False and refused["needs_restart"] and real_pid == real_pid2 and real_alive and "instructions" in refused
    record("T6 Ollama settings apply", bool(ours_ok and not_ours_ok), f"our Ollama restarted with OLLAMA_NUM_PARALLEL=2/MAX_LOADED=3 (pid {pid0} -> {pid1}), no num_gpu, ISHTI stayed ready, defaults restored; "
           f"an Ollama ISHTI did not start (pid {real_pid}) was refused with needs_restart, untouched, and still running after ISHTI exited")


# ---------------------------------------------------------------- perf
def perf():
    from ishti import llm
    free_gpu_for_backend()
    say("  --- cold boot (our own Ollama started by ISHTI, planner loaded from disk) ---")
    proc, t0 = start_backend(planner=None)  # the REAL planner: these are the boot numbers
    while time.time() - t0 < 240:
        r = get_ready()
        if r and r["ready"]:
            break
        time.sleep(0.25)
    cold = time.time() - t0
    stages = json.load(open(os.path.join(ROOT, "data", "boot_times.json"), encoding="utf-8"))[-1]
    say(f"  cold boot to ready: {cold:.1f} s; stages {stages['stages']}; planner first token at boot {stages['first_token_ms']} ms")
    # event-loop latency: 300 concurrent-ish /api/ready + /api/vram requests
    lat = {"/api/ready": [], "/api/vram": []}

    def hit(path):
        t = time.time()
        requests.get(BASE + path, timeout=15)
        lat[path].append(1000 * (time.time() - t))
    ths = [threading.Thread(target=hit, args=("/api/ready" if i % 2 else "/api/vram",)) for i in range(300)]
    t = time.time()
    [x.start() for x in ths]
    [x.join() for x in ths]
    for path, xs in lat.items():
        xs.sort()
        say(f"  event loop, {path}: {len(xs)} concurrent requests (300 in {time.time() - t:.2f} s total) median {statistics.median(xs):.0f} ms, p95 {xs[int(len(xs) * .95)]:.0f} ms, max {xs[-1]:.0f} ms")
    lat = lat["/api/ready"]
    requests.post(BASE + "/api/shutdown", json={}, timeout=5)
    wait_gone(proc.pid)
    say("  --- warm boot (Ollama running, planner already resident) ---")
    lifecycle.OLLAMA = vram.OLLAMA
    for m in vram.ps(True, strict=False):
        vram.unload(m["name"])
    vram.admit("ishti-qwen2.5:14b", role="planner", job="pre-load")
    env_url = vram.OLLAMA
    proc, t0 = start_backend(env_url, planner=None)
    while time.time() - t0 < 240:
        r = get_ready()
        if r and r["ready"]:
            break
        time.sleep(0.25)
    warm = time.time() - t0
    say(f"  warm boot to ready: {warm:.1f} s")
    requests.post(BASE + "/api/shutdown", json={}, timeout=5)
    wait_gone(proc.pid)
    # voice turn: speech -> text -> first LLM token -> first audio, all in-process on the real models
    from ishti import voice
    say("  --- voice turn latency ---")
    fresh()
    vram.admit("ishti-qwen2.5:14b", role="planner", job="voice benchmark")
    wav = voice.synthesize("What is seventeen times twenty three?", voice.current_voice())
    voice.transcribe(io.BytesIO(wav))  # warm (loads whisper through the governor)
    rows = []
    for _ in range(5):
        t0 = time.time()
        text = voice.transcribe(io.BytesIO(wav))
        t_stt = time.time() - t0
        t1 = time.time()
        box = []
        llm.chat([{"role": "user", "content": text}], role="planner", temperature=0, max_tokens=40, on_token=lambda x: box or box.append(time.time() - t1))
        t_llm = box[0] if box else time.time() - t1
        t2 = time.time()
        voice.synthesize("Seventeen times twenty three is three hundred and ninety one.", voice.current_voice())
        rows.append((t_stt, t_llm, time.time() - t2))
    med = [statistics.median(c) for c in zip(*rows)]
    say(f"  voice turn (median of 5): STT {med[0] * 1000:.0f} ms + first LLM token {med[1] * 1000:.0f} ms + first-sentence TTS {med[2] * 1000:.0f} ms = {sum(med) * 1000:.0f} ms to first audio")
    record("PERF", True, f"cold boot {cold:.1f} s; warm boot {warm:.1f} s; planner first token {stages['first_token_ms']} ms at boot; "
           f"/api/ready p95 {lat[int(len(lat) * .95)]:.0f} ms with 300 concurrent requests in flight; voice turn to first audio {sum(med) * 1000:.0f} ms "
           f"(STT {med[0] * 1000:.0f} + LLM {med[1] * 1000:.0f} + TTS {med[2] * 1000:.0f})")


if __name__ == "__main__":
    tests = {"t1a": t1a, "t1b": t1b, "t2": t2, "t3": t3, "t4": t4, "t5": t5, "t5b": t5b, "t6": t6, "perf": perf}
    args = sys.argv[1:] or list(tests)
    i = 0
    while i < len(args):
        name = args[i]
        i += 1
        try:
            if name == "t5" and i < len(args) and args[i].isdigit():
                t5(int(args[i]))
                i += 1
            else:
                tests[name]()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            record(name.upper(), False, f"crashed: {type(exc).__name__}: {exc}")
