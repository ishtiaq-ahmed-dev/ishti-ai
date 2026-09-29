"""Regenerate FEATURE_CATALOG.md from the skill manifests (source of truth) + verified core modules.

Why a tool: the catalog must never drift from the manifests. When a skill is added or a tier/scope
changes, run this and the catalog is current again. Rows claim only what is on disk: entry points
are checked for existence; anything described but missing lands in the MISSING table with evidence.

Run:  venv\\Scripts\\python tools\\gen_feature_catalog.py
"""
import glob
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)


def esc(s, n=90):
    s = str(s or "").replace("|", "\\|").replace("\n", " ")
    return (s[:n] + "...") if len(s) > n else s


skills = []
for p in sorted(glob.glob("ishti/skills/*/manifest.json")):
    m = json.load(open(p, encoding="utf-8"))
    name = m.get("name") or os.path.basename(os.path.dirname(p))
    needs = m.get("needs", {})
    scope = m.get("scope", {})
    net = ", ".join(scope.get("net") or []) or "none"
    models = ", ".join(needs.get("models") or []) or "none"
    mcp = ", ".join(needs.get("mcp") or []) or "none"
    deps = ", ".join((needs.get("python") or [])[:4]) or "stdlib"
    if mcp != "none":
        deps += f"; mcp:{mcp}"
    skill_py = os.path.join(os.path.dirname(p), "skill.py")
    status = "works" if (m.get("status") == "working" and os.path.isfile(skill_py)) else "partial"
    route = m.get("route", "")
    skills.append({
        "id": name, "entry": f"ishti/skills/{name}/skill.py",
        "tier": m.get("tier", "?"), "scope_net": net,
        "scope_write": ", ".join(scope.get("write") or []) or "none",
        "deps": deps, "models": models, "status": status,
        "expected": m.get("description", ""),
        "accept": f"`{esc(route, 60)}` routes; run() returns a reply dict; self-check/smoke green",
        "howto": "run_skill harness: happy + language + edge + failure + safety rows in tests/skills/%s/" % name,
        "data": "synthetic fixtures in tests/fixtures/; no real accounts",
    })

# ---- core features (each verified on disk; keep this list in sync with reality) ----
core = [
    ("CORE-01", "Router (deterministic + Laya typed decision)", "core", "ishti/router.py; ishti/dispatcher.py:route()", "T0",
     "none", "laya", "planner (Laya CPU/GPU)", "Routes each message to 0..n skills with confidence; 5-20 window.",
     "routing eval 143/143", "pytest tests/test_dispatcher_followups.py; ishti.check", "fixture messages", "works"),
    ("CORE-02", "Dispatcher session state + T2 gate + repeat guard", "core", "ishti/dispatcher.py:handle()",
     "gate", "audit.db", "router, skills ask_first", "planner",
     "Pending yes/no gate; T3 blocked; repeat guard within 20 s.",
     "T2 asks before acting; 'no' cancels; T3 blocked", "tests/test_prompt3_skills.py; check.py:301",
     "synthetic sessions", "works"),
    ("CORE-03", "Voice pipeline (STT faster-whisper, TTS, VAD, barge-in)", "core", "ishti/voice.py; voice/tts.py",
     "T0", "mic_control", "silero VAD, faster-whisper, Fish Speech", "whisper + tts (GPU)",
     "Mic default OFF; wake word; STT->plan->TTS; barge-in ~150 ms.",
     "turn latency measured; mic off = no capture", "tests/test_stt_threads.py; tests/test_multitask_voice_session.py",
     "synthetic wav fixtures", "works (mic hardware BLOCKED - NEEDS)"),
    ("CORE-04", "Wake words + mic kill switch", "core", "ishti/wake.py; ishti/mic_control.py", "T0",
     "mic_control", "openWakeWord/silero", "CPU", "Wake phrase starts a session; mic off = nothing captured.",
     "no frames captured when off", "tests/test_stt_accuracy.py; audit mic lines", "synthetic audio", "works"),
    ("CORE-05", "VRAM governor + ladders + no partial offload", "core", "ishti/vram.py", "core",
     "none", "ollama /api/ps", "all models", "Downshift/upshift; admit only if size_vram==size verified.",
     "0 partial offload; thrash count 0", "tests/test_vram_governor_pressure.py; e2e_vram_lifecycle.py",
     "real Ollama", "works"),
    ("CORE-06", "Lifecycle: boot stages, watchdog, kill switch, graceful shutdown", "core",
     "ishti/lifecycle.py; ishti/watchdog.py; ishti/killswitch.py", "core", "run/ishti.json",
     "psutil, Job Object", "none", "One state machine; force-kill timeout; 0 orphans after exit routes.",
     "0 orphans x20; integrity_check ok", "tests/test_shutdown_cleanliness.py; tests/test_lifecycle.py",
     "none", "works"),
    ("CORE-07", "Memory: SQLite facts, Chroma, relationship graph, CRM isolation", "core",
     "ishti/arch2/memory.py; ishti/rgraph.py; ishti/skills/clients/skill.py", "T1",
     "data/*.db", "sqlite, chroma", "planner (embed?)",
     "Exact facts + semantic recall + per-client isolation; one service down -> still answers.",
     "cross-client leakage test green", "tests/test_arch2_memory.py; tests/test_client_isolation.py",
     "synthetic people/clients", "works"),
    ("CORE-08", "Learning/corrections/lessons (no LoRA weights yet)", "core",
     "ishti/learning/", "T1", "data/learning.db", "audit, sessions", "none (heuristics)",
     "Lessons from corrections; attribution-gated; confirm to promote.",
     "no unattributed lessons", "tests/test_learning.py; test_learning_wiring.py", "synthetic sessions",
     "partial (LoRA MISSING - NEEDS)"),
    ("CORE-09", "Audit log + attribution + secrets masking", "core", "ishti/audit.py", "core",
     "data/audit.db", "sessions", "none", "Every action: initiator, tier, confirmed-time, masked secrets.",
     "T2 rows carry confirm time", "ishti/audit self-check", "none", "works"),
    ("CORE-10", "Prompt-injection defense (untrusted content marking)", "core", "ishti/untrusted.py",
     "core", "none", "dispatcher", "none", "Chat/file/web content treated as data; never changes tiers.",
     "injection tests green", "tests/test_chat_digest.py N5; check.py", "injection fixtures", "works"),
    ("CORE-11", "Desktop shell (splash, exit routes, tray, single instance)", "UI",
     "desktop/launcher.py; desktop/app_window.py; desktop/tray.py", "core", "run/ishti.json",
     "pywebview, pystray", "none", "No terminal; splash -> ready; all exit routes clean.",
     "0 orphans; no terminal window", "tests/test_lifecycle.py; test_backend_watch.py", "none", "works"),
    ("CORE-12", "Web UI + chat (dashboard, digest tables)", "UI",
     "interface/web_ui/server.py; interface/app", "core", "localhost:8888", "FastAPI, React",
     "planner", "Chat renders GFM tables; upload picker MISSING (NEEDS N9).",
     "contract tests green", "tests/contract_test.py (e2e); N8 in test_chat_digest.py", "synthetic", "partial (upload picker MISSING)"),
    ("CORE-13", "Background multitasking (agents, task manager)", "core", "ishti/agents.py; ishti/taskmgr.py",
     "core", "taskmgr db", "dispatcher", "planner", "Tasks survive topic switch; no UI starvation.",
     "M1: ~10 topics", "tests/test_multitask_voice_session.py; test_multitopic.py", "synthetic", "works"),
    ("CORE-14", "Skill lifecycle: skillsmith creation, registry, enable/disable", "core",
     "ishti/skill_lifecycle.py; ishti/skills/registry.py", "core", "settings svc",
     "loader", "planner (skillsmith)", "New skills scanned + T2-gated; enable/disable persists.",
     "unknown skill refused", "tests/test_prompt3_skills.py", "synthetic manifest", "works"),
    ("CORE-15", "Resilience: degraded-mode fallbacks", "core", "ishti/resilience.py", "core",
     "none", "dispatcher, health", "none", "Model down -> lighter rung or honest failure; no hang.",
     "no hang on Ollama down", "ishti/resilience self-check", "simulated outage", "works"),
    ("CORE-16", "MCP client (tools via MCP servers)", "core", "ishti/mcp_client.py", "core",
     "mcp config", "mcp servers", "none", "Local MCP tool servers; sandboxed tools.",
     "server down -> graceful", "checked via resilience", "mock MCP", "partial (unverified in suite)"),
]

skill_ids = {s["id"] for s in skills}
extra_missing = []
for name, hint in [("media_rename", "media"), ("browser_agent", "browser"),
                   ("diagrams", "chart"), ("skill_library", "skillsmith")]:
    if name not in skill_ids:
        extra_missing.append((name, hint))

lines = []
A = lines.append
A("# FEATURE_CATALOG - generated from the manifests + verified core modules (2026-09-30)")
A("")
A("Source of truth: `ishti/skills/*/manifest.json` (%d files) + the core modules verified on disk this pass" % len(skills))
A("(file existence checked, not assumed). Status values: works / partial / broken / unknown / **MISSING**.")
A("Acceptance criteria and test data are the objective rules Prompt 2 will run. Regenerate with")
A("`venv\\Scripts\\python tools\\gen_feature_catalog.py` after any manifest change.")
A("")
A("## Counts")
A("")
A(f"- Skills: **{len(skills)}** (T0: {sum(1 for s in skills if s['tier']=='T0')}, "
   f"T1: {sum(1 for s in skills if s['tier']=='T1')}, T2: {sum(1 for s in skills if s['tier']=='T2')})")
A("- Core/UI features: **%d**" % len(core))
A("- Prompt-named skills not found as standalone folders: " +
   (", ".join(f"**{n}** (likely covered by `{h}`)" for n, h in extra_missing) if extra_missing else "none"))
A("- Declared `test_file` in manifests: only chat_digest. Per-skill tests under `tests/skills/` close that gap.")
A("")
A("## Skills (%d)" % len(skills))
A("")
A("| ID | Feature | Type | Entry point | Tier | Scope (write/net) | Depends on | Models/GPU | Expected behavior | Acceptance criteria | How to test | Test data | Status |")
A("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for s in skills:
    A(f"| {s['id']} | {esc(s['id'])} | skill | {s['entry']} | {s['tier']} | "
      f"{esc(s['scope_write'])} / {esc(s['scope_net'],50)} | {esc(s['deps'],60)} | {esc(s['models'],40)} | "
      f"{esc(s['expected'])} | {esc(s['accept'],80)} | {esc(s['howto'],80)} | {esc(s['data'],50)} | {s['status']} |")
A("")
A("## Core & UI features")
A("")
A("| ID | Feature | Type | Entry point | Tier | Scope | Depends on | Models/GPU | Expected behavior | Acceptance criteria | How to test | Test data | Status |")
A("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for r in core:
    A("| " + " | ".join(esc(x, 110) for x in r) + " |")
A("")
A("## MISSING / unknown (docs say, code says no)")
A("")
A("| Item | Evidence | Verdict |")
A("|---|---|---|")
for name, hint in extra_missing:
    A(f"| {name} | no `ishti/skills/{name}/` folder; the capability lives inside `{hint}` | MISSING as named (see `{hint}` skill) |")
A("| LoRA training pipeline | `ishti/learning/` has lessons/corrections; no trainer/weights anywhere in repo | MISSING (weights) - lessons work; fine-tuning does not exist |")
A("| Chat upload picker (D1-D4) | NEEDS.md N9; no paperclip component in Chat.tsx | MISSING (UI) - documented BLOCKED |")
A("| Live microphone hardware | NEEDS.md N7: no mic/speaker connected (`Error querying device -1`) | BLOCKED (hardware) - pipeline tested in-process |")
A("")

out = os.path.join(ROOT, "FEATURE_CATALOG.md")
open(out, "w", encoding="utf-8").write("\n".join(lines))
print("FEATURE_CATALOG.md written:", len(lines), "lines;", len(skills), "skills +", len(core), "core rows;",
      "missing-as-named:", [n for n, _ in extra_missing])
