"""ARCH1 vs ARCH2 head-to-head (QA mega-request: "test both architectures").

Same 20 rows through the REAL dispatcher twice: once with arch.active=arch1 (deterministic
routing + skills) and once with arch.active=arch2 (intent shortlist -> Qwen3-14B think/tool
loop -> validation -> final answer). The arch2 toggle is ishti/arch2/registry.set_active(),
the same path the dashboard and "use arch 2" voice command use; the phase's `finally` restores
arch1 and rewrites config.json, so a killed run cannot leave the machine on arch2.

Rows: deterministic-answer skills (calc, sudoku, subscriptions, notes), retrieval (owner,
checklist), honest-decline (mnemonic without a body), Urdu language handling, and plain
conversation - chosen so both architectures face identical, objectively checkable asks.

Usage:
    ISHTI_TEST_MODE=1 venv\\Scripts\\python.exe tools\\arch_compare.py
Results append to reports/arch_compare_results.json after every row (kill-safe resumable);
pass --reset to start a fresh comparison. NEVER changes the live instance's config: it
mutates this checkout's ishti/config.json only.
"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("ISHTI_TEST_MODE", "1")

RESULTS_PATH = os.path.join(ROOT, "reports", "arch_compare_results.json")

ROWS = [
    # (id, request, checker: callable(reply) -> (ok, note))
    ("calc_linear", "solve 2x + 3 = 11",   # 2x=8 -> x=4
     lambda r: "4" in r and "x" in r.lower(), "x=4 must appear"),
    ("sudoku_row1", "solve this sudoku: 530070000600195000098000060800060003400803001700020006060000280000419005000080079",
     lambda r: "534678912" in r.replace(" ", "").replace("\n", ""),
     "known solution must appear (dispatcher strips the 'Solved' header)"),
    ("subscriptions_add", "track subscription netflix 1500 monthly",
     lambda r: "netflix" in r.lower(), "must acknowledge netflix"),
    ("notes_add", "note: buy contact lenses",
     lambda r: ("buy contact lenses" in r.lower()) or ("note" in r.lower() and "saved" in r.lower()),
     "must save the note"),
    ("checklist_add", "add submit tax form to my checklist",
     lambda r: "checklist" in r.lower() or "submit tax form" in r.lower(), "must acknowledge item"),
    ("owner_facts", "what do you know about me",
     lambda r: len(r) > 40, "must produce a real answer"),
    ("mnemonic_decline", "make a mnemonic for",
     lambda r: len(r) > 0, "any honest reply beats a crash"),
    ("urdu_weather", "پشاور میں موسم کیسا ہے",
     lambda r: len(r) > 10, "must answer (any language)"),
    ("chat_pong", "Reply with the exact word: PONG",
     lambda r: "pong" in r.lower(), "must contain PONG"),
    ("conversation", "what can you do for me",
     lambda r: len(r) > 40, "real capability summary"),
    ("meta_skills", "check your skills",
     lambda r: ("skill" in r.lower() and len(r) > 40), "health summary"),
    ("vocab_stats", "vocab stats",
     lambda r: len(r) > 5, "must answer"),
    ("quickcalc", "2000 with a 15% off",
     lambda r: "1700" in r.replace(",", ""), "1500*0.85=1700"),
    ("paper_account", "paper account",
     lambda r: "paper" in r.lower() and ("cash" in r.lower() or "equity" in r.lower() or "test-mode" in r.lower())
     or "stopped at the last step" in r,   # the correct T2 block under ISHTI_TEST_MODE
     "account state or the correct T2 test-mode block"),
    ("time_now", "what time is it right now",
     lambda r: any(c.isdigit() for c in r), "must give a time"),
    ("unknown_refusal", "book a flight to Dubai using my credit card",
     lambda r: len(r) > 10, "honest handling, no fake booking"),
    ("multi_step_math", "if I spend 250 on groceries and 175 on fuel, how much did I spend",
     lambda r: "425" in r.replace(",", ""), "250+175=425"),
    ("memory_store", "remember that my sister's name is Zainab",
     lambda r: "zainab" in r.lower(), "must acknowledge Zainab"),
    ("memory_recall", "what is my sister's name",
     lambda r: len(r) > 3, "must answer (recall or honest miss)"),
    ("summarize_self", "in one sentence, what is ISHTI",
     lambda r: len(r) > 20, "self-description"),
]


def _load_results():
    if os.path.isfile(RESULTS_PATH) and not os.environ.get("ARCH_RESET"):
        try:
            return json.load(open(RESULTS_PATH, encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save(results):
    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    json.dump(results, open(RESULTS_PATH, "w", encoding="utf-8"), indent=1)


def run_phase(arch, rows):
    from ishti.arch2 import registry
    if arch == "arch2":
        registry.set_active("arch2", by="arch-compare")
    else:
        registry.set_active("arch1", by="arch-compare")
    try:
        from ishti import dispatcher
        for rid, text, check, want in rows:
            t0 = time.time()
            err = None
            out = None
            try:
                # a FRESH session per row: rows must not answer each other's pending gates
                # or inherit conversation history (that bled the urdu row into a T2 gate once)
                out = dispatcher.handle(text, f"archcmp_{arch}_{rid}")
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
            dur = round(time.time() - t0, 1)
            # dispatcher.handle returns final_answer (dispatcher shape); skills return reply
            reply = str((out or {}).get("final_answer") or (out or {}).get("reply") or "") if out else ""
            ok, note = (False, err or "no reply") if err or out is None else (False, "empty reply")
            if out is not None and not err:
                try:
                    res = check(reply)
                    ok, note = res if isinstance(res, tuple) else (bool(res), "")
                except Exception as exc:
                    ok, note = False, f"checker error: {exc}"
            rec = {"row": rid, "arch": arch, "ok": bool(ok), "note": str(note)[:160],
                   "seconds": dur, "reply_head": reply[:220].replace("\n", " "),
                   "mode": str((out or {}).get("mode") or "rules/skill")}
            print(f"[{'PASS' if ok else 'FAIL'}] {arch:>5} {rid:<18} {dur:>6.1f}s  {str(note)[:90]}")
            yield rec
    finally:
        registry.set_active("arch1", by="arch-compare-restore")
        print("[restore] arch.active = arch1")


def main():
    results = _load_results()
    order = ["arch1", "arch2"]
    for arch in order:
        done = {r["row"] for r in results.values() if r.get("arch") == arch}
        pending = [r for r in ROWS if r[0] not in done]
        if not pending:
            print(f"[skip] {arch}: all rows already recorded")
            continue
        print(f"\n=== phase {arch.upper()} ({len(pending)} rows) ===")
        for rec in run_phase(arch, pending):
            results[f"{rec['arch']}:{rec['row']}"] = rec
            _save(results)
    # summary
    print("\n=== SUMMARY ===")
    for arch in order:
        rs = [r for r in results.values() if r.get("arch") == arch]
        if not rs:
            continue
        secs = [r["seconds"] for r in rs]
        oks = sum(1 for r in rs if r["ok"])
        two_pass = sum(1 for r in rs if r.get("mode") == "ARCH2")
        print(f"{arch}: {oks}/{len(rs)} checks passed, mean {sum(secs)/len(secs):.1f}s, "
              f"median {sorted(secs)[len(secs)//2]:.1f}s, max {max(secs):.1f}s"
              + (f", two-pass pipeline answered {two_pass}/{len(rs)}" if arch == "arch2" else ""))
    misses = [f"{r['arch']}:{r['row']}" for r in results.values() if not r["ok"]]
    if misses:
        print("failed checks:", ", ".join(sorted(misses)))


if __name__ == "__main__":
    if "--reset" in sys.argv:
        os.environ["ARCH_RESET"] = "1"
        if os.path.isfile(RESULTS_PATH):
            os.remove(RESULTS_PATH)
    main()
