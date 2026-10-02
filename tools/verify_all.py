"""Run every ISHTI self-check and test in one pass, and report a table.

The parts of ISHTI are verified by their own `__main__` self-checks (core modules and each skill), by the standalone
scripts in tests/, and by the end-to-end core loop in ishti/check.py. This runs them all, one after the other, and
prints what passed. It also inspects the RAW exit code: a Windows fail-fast (0xC0000409, e.g. crash while a native
library is torn down) shows up as a non-zero code even after "passing" self-checks, and a shell may hide it.

    python tools/verify_all.py                # everything
    python tools/verify_all.py --only voice   # only the items whose name contains "voice"
    python tools/verify_all.py --quick        # skip the slow ones (ishti.check, stt, video, browser, skillsmith)

Exit code: 0 when everything passed, 1 otherwise (so a script can gate on it).
"""
import argparse
import os
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

CORE = ["audit", "killswitch", "scope", "untrusted", "health", "facts", "semantic", "corrections", "reflex", "planner",
        "router", "llm", "ftlog", "rgraph", "gpu", "models", "duck", "diarize", "ocr", "clone", "manage", "wake",
        "talk", "voice"]
# Modules whose docstring documents `Self-check: python <path>`: verify THAT exact command too, so a
# missing repo-root bootstrap (ModuleNotFoundError when run as a script) is caught here, not by a human.
DOC_SELFCHECKS = [
    "desktop/overlay/fsm.py", "desktop/overlay/integrations.py", "desktop/overlay/log.py",
    "desktop/overlay/mascot.py", "desktop/overlay/pills.py", "desktop/overlay/window.py",
    "ishti/api_sessions.py", "ishti/dictation.py", "ishti/gateway.py", "ishti/hooks_relay.py",
    "ishti/session_schema.py", "ishti/workspace.py",
    "orchestrator/install_ops.py", "orchestrator/plan_view.py", "orchestrator/quality_gate.py",
]
SLOW = {"ishti.check", "ishti.wake", "ishti.voice", "ishti.diarize", "ishti.clone", "ishti.manage", "ishti.llm",
        "ishti.ocr", "ishti.semantic", "skill:browser", "skill:gemini", "skill:media", "skill:security",
        "skill:skillsmith", "skill:speedtest", "skill:markets", "skill:shopping", "skill:travel", "skill:weblayout",
        "test:test_stt_accuracy", "test:test_stt_threads", "test:test_browser_tabs", "test:test_voice_note_generation",
        "test:test_whatsapp_context", "test:test_multitopic", "test:test_lifecycle", "test:test_capcut_skill"}


def items():
    out = [("ishti.check", [PY, "-m", "ishti.check"])]
    out += [(f"ishti.{m}", [PY, "-m", f"ishti.{m}"]) for m in CORE]
    out += [(f"doc:{os.path.basename(p)}", [PY, os.path.join(ROOT, p)]) for p in DOC_SELFCHECKS]
    skills = sorted(d for d in os.listdir(os.path.join(ROOT, "ishti", "skills"))
                    if os.path.isfile(os.path.join(ROOT, "ishti", "skills", d, "skill.py")))
    out += [(f"skill:{s}", [PY, "-m", f"ishti.skills.{s}.skill"]) for s in skills]
    for f in sorted(os.listdir(os.path.join(ROOT, "tests"))):
        if f.startswith("test_") and f.endswith(".py"):
            out.append((f"test:{f[:-3]}", [PY, os.path.join(ROOT, "tests", f)]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    todo = [i for i in items() if args.only in i[0] and not (args.quick and i[0] in SLOW)]
    rows, t_all = [], time.time()
    for name, cmd in todo:
        t0 = time.time()
        try:
            p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=900, encoding="utf-8",
                               errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            code, out = p.returncode, (p.stdout or "") + (p.stderr or "")
        except subprocess.TimeoutExpired:
            code, out = -1, "(timed out after 900 s)"
        line = next((l.strip() for l in reversed(out.splitlines()) if "[self-check]" in l or "SUCCESS" in l
                     or "core loop OK" in l), "")
        note = ""
        if code != 0:
            note = f"exit {code}" + (" (=0xC0000409 fail-fast)" if (code & 0xFFFFFFFF) == 0xC0000409 else "")
            note += " | " + " / ".join(out.strip().splitlines()[-3:])[:200]
        rows.append((name, code, time.time() - t0, line[:120], note))
        print(f"{'PASS' if code == 0 else 'FAIL'} {name:<28} {time.time() - t0:6.1f}s  {line[:100] or note}", flush=True)
    bad = [r for r in rows if r[1] != 0]
    print(f"\n[verify_all] {len(rows) - len(bad)}/{len(rows)} passed in {time.time() - t_all:.0f} s" + ("" if not bad else
          f" - failed: {', '.join(r[0] for r in bad)}"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
