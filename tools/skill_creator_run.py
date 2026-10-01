"""Run the skill_creator pipeline for real, on one task, and record what happened.

This is the acceptance-test driver (master prompt acceptance test 1). It is deliberately SEPARATE from the
skill's own self-checks: those run offline with an injected fake, this one uses the real local coder model,
the real research and vetting stages, and the real sandbox.

    venv\\Scripts\\python tools\\skill_creator_run.py "make yourself able to <do X>"

Safety: ISHTI_TEST_MODE is forced on, nothing is installed (the generated manifest's dependencies are
VETTED but never pip-installed by this script), and the owner's "yes" gate is never auto-answered - the
script stops at the gate and prints what would be installed. VRAM is sampled before and after from
/api/ps so a partial offload would be visible.
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["ISHTI_TEST_MODE"] = "1"          # nothing real can happen, even if a gate were answered

import requests  # noqa: E402

from ishti import CONFIG  # noqa: E402
from ishti.skills.skill_creator import design, drafts, gap, research, vetting  # noqa: E402
from ishti.skills.skill_creator import skill as sc  # noqa: E402

BASE = CONFIG["llm"]["base_url"].rsplit("/v1", 1)[0]


def ps_snapshot():
    """/api/ps + nvidia-smi, so a PARTIAL offload (size_vram != size) is impossible to miss."""
    try:
        models = requests.get(f"{BASE}/api/ps", timeout=5).json().get("models", []) or []
    except Exception as exc:
        return {"error": f"/api/ps: {type(exc).__name__}: {exc}"}
    import subprocess
    try:
        free = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total",
                               "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=8,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        used, total = (int(x) for x in free.stdout.strip().splitlines()[0].split(","))
    except Exception:
        used = total = None
    return {"gpu_used_mb": used, "gpu_total_mb": total,
            "loaded": [{"name": m.get("name"), "size": m.get("size"), "size_vram": m.get("size_vram"),
                        "PARTIAL_OFFLOAD": m.get("size") != m.get("size_vram")} for m in models]}


def main(task):
    print(f"=== skill_creator real run ===\nTASK: {task}\n")
    report = {"task": task, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "stages": {}}
    print("BEFORE:", json.dumps(ps_snapshot()))
    report["gpu_before"] = ps_snapshot()

    # 1. gap - forced, because the point of this run is to BUILD, not to test routing
    g = {"gap": True, "reason": "forced: this driver exists to build", "clarify": None,
         "route": {"skill": None, "confidence": 0.0, "source": "rule"}, "recurring": []}
    print(f"1. gap: forced True")

    # 2. feasibility (real probe)
    feas = design.feasibility(task)
    report["stages"]["feasibility"] = {k: v for k, v in feas.items() if k != "gpu"}
    print(f"2. feasibility: tier={feas['tier']} possible={feas['possible']} gpu={feas['gpu'].get('note')}")
    if not feas["possible"]:
        print("REFUSED (honestly):\n" + design.refuse(feas))
        report["result"] = "refused"
        _save(report)
        return 1

    # 3. research (real web, budgeted)
    t0 = time.time()
    res = research.research(task, {"trace": lambda **k: None})
    report["stages"]["research"] = {"sources": len(res.get("sources") or []), "note": res.get("note"),
                                     "injections": len(res.get("injections") or []),
                                     "seconds": round(time.time() - t0, 1)}
    print(f"3. research: {len(res.get('sources') or [])} source(s) in {round(time.time()-t0,1)}s "
          f"-> {res.get('path')}")

    session = {"trace": lambda **k: None}
    t1 = time.time()
    print("4..8. building + vetting + sandbox testing (this loads the coder model)...")
    out = sc.build(task, session, gap_fn=lambda req: g, research_fn=lambda req, s=None: res)
    elapsed = round(time.time() - t1, 1)
    facts = out.get("facts", {})
    report["stages"].update({
        "generate": facts.get("generate"), "scan": facts.get("scan"),
        "vetting": facts.get("vetting"), "tests": facts.get("tests"),
        "package": facts.get("package"), "draft": out.get("draft"), "stage": out.get("stage"),
    })
    print(f"   generate: {facts.get('generate')}")
    print(f"   scan findings: {len(facts.get('scan') or [])}")
    print(f"   vetting: {(facts.get('vetting') or {}).get('ok')} pinned="
          f"{(facts.get('vetting') or {}).get('pinned')}")
    print(f"   tests: {facts.get('tests')}")
    print(f"   package: {facts.get('package')}")
    print(f"   -> stage={out.get('stage')} draft={out.get('draft')} in {elapsed}s")
    print(f"AFTER : {json.dumps(ps_snapshot())}")
    report["gpu_after"] = ps_snapshot()
    report["seconds"] = elapsed
    report["reply"] = out.get("reply", "")[:2000]
    print("\n=== REPLY TO THE OWNER ===\n" + out.get("reply", ""))
    installed = os.path.isdir(os.path.join(ROOT, "ishti", "skills", out.get("draft") or "__none__"))
    report["installed"] = installed
    print(f"\ninstalled into ishti/skills? {installed}  (expected False: the owner's yes is still pending)")
    _save(report)
    return 0 if out.get("stage") in ("gate", "test", "vetting") else 0


def _save(report):
    path = os.path.join(ROOT, "scratch", "skill_creator_run_report.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n[report] {path}")


if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) or "make yourself able to combine a folder of images into one PDF"
    sys.exit(main(task))
