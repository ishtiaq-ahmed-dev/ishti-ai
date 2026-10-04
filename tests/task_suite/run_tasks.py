"""Run the owner's 100-task checklist against ISHTI.

Two modes:
    venv\\Scripts\\python tests\\task_suite\\run_tasks.py --mode route
        Offline: which skill does each request route to (fast; rules + Laya when cached).

    venv\\Scripts\\python tests\\task_suite\\run_tasks.py --mode live --ids 1,2,11
        Live: each request goes through the real dispatcher in its own subprocess
        (ISHTI_TEST_MODE=1 blocks T2 side effects). Needs Ollama running.

    venv\\Scripts\\python tests\\task_suite\\run_tasks.py --mode live --cats comm,memory

Flags: --ids 1,2,3  --cats weather,files  --skip 55  --timeout 150  --out reports/name
Writes reports/task_suite_<mode>.md and .json and prints a summary table.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
HERE = os.path.dirname(os.path.abspath(__file__))

from tests.task_suite import cases as C  # noqa: E402


# A live run must never write turn data into the owner's real conversation store or its
# crash-recovery inflight file, and it must not die because a reply contained an emoji that
# the Windows console codepage cannot encode (the first full baseline died at #50 on exactly
# that, with ~50 cases still untested). Two measures:
#
#  * each case runs in a one-off subprocess that points its conversation manager at a temp
#    dir and points the dispatcher's in-memory SESSIONS at a throwaway session id, so nothing
#    persists to data/sessions or inflight.json once the process exits;
#  * the worker prints results through a UTF-8 pipe, and run_live writes its own progress
#    with errors="replace" so a single non-codepage character can never abort the whole run.
_TASK_SUITE_ISOLATION_PATCHED = True


def route_of(text):
    try:
        from ishti.dispatcher import SKILLS
        from ishti.router import route
        d = route(text, SKILLS)
        return d.get("skill"), d.get("source"), d.get("confidence")
    except Exception as exc:
        return None, f"error: {type(exc).__name__}: {exc}", 0.0


def _expects(case):
    e = case.get("expect")
    if not e:
        return set()
    return {e} if isinstance(e, str) else set(e)


def verdict_route(case, skill):
    if case.get("forbid") and skill in case["forbid"]:
        return "fail", f"forbidden skill {skill}"
    exp = _expects(case)
    if not exp:
        return "info", "no routing expectation (free-form reply)"
    if skill in exp:
        return "pass", ""
    return "partial", f"routed to {skill}, expected {'/'.join(sorted(exp))}"


def run_route(items):
    rows = []
    for c in items:
        skill, source, conf = route_of(c["text"])
        v, why = verdict_route(c, skill)
        rows.append({"id": c["id"], "cat": c["cat"], "diff": c["diff"], "text": c["text"],
                     "skill": skill, "source": source, "verdict": v, "why": why, "reply": ""})
    return rows


def _worker_cmd(text: str, session_tag: str) -> list[str]:
    """One task, one subprocess, one throwaway session.

    The worker redirects its conversation manager to a temp dir and uses a unique session id
    so the turn is answered through the real dispatcher pipeline but never persisted to the
    owner's data/sessions or the inflight crash-recovery file."""
    py = (
        "import os, sys, tempfile\n"
        "os.environ.setdefault('ISHTI_TEST_MODE', '1')\n"
        "os.environ.setdefault('PYTHONIOENCODING', 'utf-8')\n"
        "sys.path.insert(0, r'" + ROOT.replace("\\", "\\\\") + "')\n"
        # Point the conversation manager at a throwaway dir BEFORE import, so any add_message / new_chat
        # inside _finish writes into a temp dir that is deleted with the process.
        "_tmp = tempfile.mkdtemp(prefix='ishti_ts_')\n"
        "os.environ.setdefault('ISHTI_STATE_DB', os.path.join(_tmp, 'state.db'))\n"
        # Keep the crash-recovery hint file in the temp dir so the worker neither reads nor deletes
        # the owner's real data/inflight.json (which was leaking a false 'Before I restarted' note
        # into every reply and polluting the graded text).
        "os.environ.setdefault('ISHTI_INFLIGHT_FILE', os.path.join(_tmp, 'inflight.json'))\n"
        "class _CM:\n"
        "    sessions_dir = _tmp\n"
        "    active_session_id = None\n"
        "    def list_sessions(self): return []\n"
        "    def new_chat(self, title='New Conversation'):\n"
        "        return 'qa_ts_'+sys.argv[2]\n"
        "    def set_active(self, sid=None): self.active_session_id = sid or self.new_chat()\n"
        "    def add_message(self, *a, **k): pass\n"
        "import orchestrator.memory.conversation as _c\n"
        "_c._conversation_manager_instance = _c.ConversationManager = _CM\n"
        "# Also point the dispatcher's global SESSIONS at nothing real for this sid\n"
        "import ishti.dispatcher as _d\n"
        # Do not inherit the owner's real crash-recovery state: point the inflight file at the temp dir
        # and drop any RECOVER entries, or a stale data/inflight.json leaks a false 'Before I restarted'
        # note into every reply and pollutes the reply being graded.
        "_d.INFLIGHT_FILE = os.path.join(_tmp, 'inflight.json')\n"
        "_d.RECOVER = []\n"
        "_d.SESSIONS.clear()\n"
        "_d.SESSIONS['qa_ts_'+sys.argv[2]] = {'id': 'qa_ts_'+sys.argv[2], 'history': [], 'pending': None, 'last_entity': None, 'tasks': [], 'running': 0}\n"
        "from ishti import dispatcher as _D\n"
        "res = _D.handle(sys.argv[1], session_id='qa_ts_'+sys.argv[2], channel='text') or {}\n"
        "out = {'ok': True, 'reply': str(res.get('final_answer') or ''), 'skill': res.get('skill'),\n"
        "       'route': (res.get('route') or {}).get('skill'), 'source': (res.get('route') or {}).get('source'),\n"
        "       'ms': res.get('latency_ms')}\n"
        "import json as _j\n"
        "print(_j.dumps(out, ensure_ascii=False))\n"
    )
    return [
        sys.executable, "-c", py, text, session_tag,
    ]


def run_live(items, timeout):
    # A reply can carry a character the Windows console/file codepage can't encode (this already
    # killed one full run at #163 with a UnicodeEncodeError on '\u26a0'). Force the stream to replace
    # unencodable characters so one emoji can never abort the whole suite.
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    rows = []
    for c in items:
        t0 = time.time()
        tag = f"{c['id']:03d}_{abs(hash(c['text'])) % 1_000_000}"
        cmd = _worker_cmd(c["text"], tag)
        try:
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, timeout=timeout,
                                  env={**os.environ, "ISHTI_TEST_MODE": "1", "PYTHONIOENCODING": "utf-8"})
            raw_stdout = proc.stdout or b""
            # Decode through UTF-8 with replacement so a single emoji can never abort the whole run.
            text_stdout = raw_stdout.decode("utf-8", errors="replace")
            # The worker's JSON is the result line, but the dispatcher may flush log lines (e.g. the
            # [monitor] correction log) AFTER it at interpreter exit. Take the last line that is JSON,
            # not blindly the last line, or a real pass reads as an empty failure.
            line = next((l for l in reversed(text_stdout.strip().splitlines()) if l.startswith("{")), "")
            data = json.loads(line) if line else {"ok": False, "error": (proc.stderr or b"").decode("utf-8", errors="replace")[-300:] or text_stdout[-300:] or f"exit {proc.returncode}"}
        except subprocess.TimeoutExpired:
            data = {"ok": False, "error": f"timeout after {timeout}s", "reply": "", "skill": None}
        except Exception as exc:  # noqa: BLE001
            data = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "reply": "", "skill": None}
        reply = str(data.get("reply") or "")
        low = reply.lower()
        v, why = "pass", ""
        bad = next((b for b in c.get("bad_any", []) if b in low), None)
        oks = c.get("ok_any") or []
        if not data.get("ok"):
            v, why = "fail", str(data.get("error"))[:200]
        elif bad:
            v, why = "fail", f"reply contains forbidden phrase {bad!r}"
        elif oks and not any(o in low for o in oks):
            v, why = "partial", f"none of the expected cues appeared: {oks[:4]}"
        elif c.get("expect") and data.get("skill") not in _expects(c):
            v, why = "partial", f"handled by {data.get('skill')}, expected {'/'.join(sorted(_expects(c)))}"
        rows.append({"id": c["id"], "cat": c["cat"], "diff": c["diff"], "text": c["text"],
                     "skill": data.get("skill"), "source": data.get("source"), "verdict": v, "why": why,
                     "reply": reply[:600], "ms": data.get("ms") or round((time.time() - t0) * 1000)})
        # Progress line written with errors="replace" so a non-codepage reply can never abort the harness.
        print(f"  #{c['id']:3d} {v:7s} {str(data.get('skill')):14s} {reply[:70]!r}")
    return rows


def write_report(rows, mode):
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    base = os.path.join(ROOT, "reports", f"task_suite_{mode}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"generated": dt.datetime.now().isoformat(timespec="seconds"), "mode": mode, "rows": rows},
                  f, ensure_ascii=False, indent=1)
    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    lines = [f"# ISHTI task suite - {mode} run",
             f"Generated: {dt.datetime.now():%Y-%m-%d %H:%M}  |  "
             + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())), ""]
    lines.append("| # | verdict | skill | request | reply / reason |")
    lines.append("|---|---|---|---|---|")
    for r in rows:
        cell = (r.get("reply") or r.get("why") or "").replace("|", "/").replace("\n", " ")[:160]
        lines.append(f"| {r['id']} | {r['verdict']} | {r.get('skill') or ''} | "
                     f"{r['text'].replace('|', '/')[:70]} | {cell} |")
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return base, counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["route", "live"], default="route")
    ap.add_argument("--ids", default="")
    ap.add_argument("--cats", default="")
    ap.add_argument("--skip", default="")
    ap.add_argument("--timeout", type=int, default=150)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    items = C.select(ids=[x for x in args.ids.split(",") if x],
                     cats=[x for x in args.cats.split(",") if x],
                     skip=[x for x in args.skip.split(",") if x])
    print(f"[task_suite] {args.mode} run: {len(items)} case(s)")
    rows = run_route(items) if args.mode == "route" else run_live(items, args.timeout)
    base, counts = write_report(rows, args.mode)
    if args.out:
        os.replace(base + ".md", os.path.join(ROOT, args.out)) if args.out.endswith(".md") else None
    print(f"[task_suite] summary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"[task_suite] report: {base}.md")
    fails = [r for r in rows if r["verdict"] in ("fail", "partial")]
    if fails:
        print("[task_suite] non-pass cases:")
        for r in fails[:40]:
            print(f"  #{r['id']:3d} {r['verdict']:7s} {r['text'][:60]!r} -> {r.get('why', '')[:80]}")


if __name__ == "__main__":
    main()
