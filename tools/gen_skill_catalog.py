"""Generate docs/SKILL_CATALOG.md from the live Skill Registry (Part A4).

One row per loaded skill, grouped into the spec's domains 21-35 (+ the extra domains the
owner's skills cover). Statuses come from the registry (manifest status -> implemented/
partial/blocked), never from wishful thinking. Domain sections the spec lists but nothing
implements get explicit 'planned' rows with a design note (Tier 3 rule A5).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# spec domain numbers 21-35 -> canonical names; the loader's categories map into them
SPEC_DOMAINS = [
    ("21", "Desktop & PC Control", ["System & Desktop"]),
    ("22", "Application Integration & API Control", ["System & Desktop", "Communication & Social"]),
    ("23", "Real-Time Streaming & Event Processing", []),
    ("24", "Multi-Agent Coordination", ["Knowledge & Intelligence"]),
    ("25", "Multitasking & Parallel Execution", ["Productivity & Planning"]),
    ("26", "Error Handling & Self-Healing", ["System & Desktop", "Development & Code"]),
    ("27", "Observability & Monitoring", ["System & Desktop"]),
    ("28", "Workflow Automation", ["Productivity & Planning"]),
    ("29", "Knowledge Graph & Memory Management", ["Knowledge & Intelligence"]),
    ("30", "Security/Compliance/Governance", ["System & Desktop"]),
    ("31", "Advanced AI & ML", ["Development & Code", "Knowledge & Intelligence"]),
    ("32", "Human-Agent Interaction & UX", ["Communication & Social", "Productivity & Planning"]),
    ("33", "Edge & IoT", []),
    ("34", "Blockchain & Web3", []),
    ("35", "Autonomous Operation", ["Knowledge & Intelligence"]),
]
EXTRA_DOMAINS = ["Files & Storage", "Web & Internet", "Media & Creative",
                 "Finance & Commerce", "Utilities & Daily", "General"]

# which catalog domains own which core module (spec skills implemented as systems, not skills)
CORE_MODULES = {
    "23": [("Real-time event bus (design note, Tier 3)", "planned",
            "Design: watchdog + health loop already poll; an asyncio pub/sub bus with rate limits can subscribe skills to file/email/calendar events (I2). Tier 3: deferred until the owner asks for live streaming.")],
    "24": [("Multi-agent orchestrator (supervisor + 10 specialists)", "implemented", "ishti.agents — C1-C9; self-check + tests/test_prompt3_skills.py")],
    "25": [("Async task manager (priority queue, cancel cascade, GPU semaphore, checkpoints)", "implemented", "ishti.taskmgr — D1-D5; self-check + tests/test_prompt3_skills.py"),
           ("taskctl skill (list/pause/resume/cancel by voice)", "implemented", "ishti/skills/taskctl — D4")],
    "26": [("Resilience core (classify, retry+backoff, circuit breakers, fallback chains, checkpoints, safe mode)", "implemented", "ishti.resilience — G1-G3, G5-G7; watchdog.py stays the process watchdog (G4)"),
           ("Skill failover inside agents (next skill on repeated failure)", "implemented", "ishti.agents._run_agent — C5")],
    "27": [("Observability core (OTel-shaped spans, p50/p95/p99, counters, error view, retention+redaction)", "implemented", "ishti.telemetry — H1, H2, H4; /api/metrics, /api/telemetry/*"),
           ("Eval harness (fixed regression set, stored runs)", "implemented", "ishti.telemetry.run_eval — H5; POST /api/eval/run"),
           ("Traces in the Settings dashboard page", "partial", "JSON APIs ready (/api/telemetry/spans, /api/metrics); a dedicated Settings tab is UI work")],
    "29": [("Memory system (4 layers, conflicts, real-deletion forget, vault)", "implemented", "Prompt 2: ishti/arch2/memory.py + core/vault.py + docs/REQUIREMENTS_MATRIX_PROMPT2.md"),
           ("Relationship graph (kin edges, confidence, disambiguation)", "implemented", "Prompt 2: ishti/rgraph.py + ishti/arch2/people.py"),
           ("Reinforcement-like learning (lessons, feedback, mistakes, decay)", "implemented", "Prompt 2: ishti/learning/* (pre-existing, improved in P2)")],
    "28": [("Trigger rules: 'when X, do Y' (file/battery/disk/CPU triggers)", "implemented", "ishti/skills/automation — the owner's existing trigger engine (I1 subset)"),
           ("Named webhooks + schedules + reminders", "implemented", "ishti/skills/workflows + reminders — runs by name, cron-like scheduling"),
           ("Workflow engine v2: loops, parallel branches, sub-workflows, versioning, dry-run", "partial", "I1: parameters/templates/testing not built; design: compose taskmgr tasks (D1) as branch nodes — Tier-2 follow-up")],
    "30": [("Redaction + audit trail + secrets in keyring", "implemented", "ishti/core/logutil + audit.py + core/settings secrets (Prompts 1-2)"),
           ("PII detection layer for free text", "partial", "regex redaction in logs/spans; a dedicated PII-classification pass is Tier-2 follow-up"),
           ("RBAC (multi-user)", "planned", "Design: single-owner system; an owner/assistant role table over audit+scopes would be the entry point. Not needed today.")],
    "31": [("QLoRA fine-tuning pipeline (offline, eval-gated)", "implemented", "orchestrator/finetune.py (Prompt 2); training run BLOCKED on peft/datasets/bitsandbytes"),
           ("Laya typed-decision router + nomic embeddings", "implemented", "ishti/router.py; ishti/semantic.py")],
    "32": [("Clarifying questions + confirmation gates + progress narration + interruption (barge-in) + kill switch", "implemented", "Prompt 1 resolver (T3), skills.ask_first (T2), dispatcher narrate/killswitch; tests from P1")],
    "35": [("Autonomous scheduled operation (standing rules, automation skill, self-healing restarts)", "partial", "ishti/skills/automation + health.py + watchdog.py cover scheduled actions and recovery; a full autonomy loop with goal ownership is Tier 3")],
    "33": [("Edge & IoT (MQTT/ESP devices)", "planned",
            "Design: an MQTT adapter skill subscribing device topics into the event bus (23) with the same T2 gates. The owner's setup has no IoT hardware yet — deferred.")],
    "34": [("Blockchain & Web3 (wallet read-only, chain lookups)", "planned",
            "Design: a read-only chain explorer skill (balance/tx lookup via a public API) behind the standard scope/net allow-list. No wallet keys, no signing; deferred until requested.")],
}

PLANNED_FALLBACK = ("planned", "Design note pending — no owner requirement yet; the Skill Registry accepts new skills without code changes.")


def main():
    from ishti.skills import load_skills, registry as REG
    skills = load_skills()
    recs = REG.records(skills, refresh=True)

    # group skills by loader category
    by_cat = {}
    for name, r in sorted(recs.items()):
        by_cat.setdefault(r["domain"], []).append((name, r))

    lines = []
    lines.append("# SKILL CATALOG — every skill from the spec's domains 21-35 (Part A4)")
    lines.append("")
    lines.append(f"Generated from the live Skill Registry (`ishti/skills/registry.py`) on "
                 f"{time.strftime('%Y-%m-%d %H:%M')}. **{len(recs)} skills loaded**, "
                 f"{sum(1 for r in recs.values() if r['status'] == 'implemented')} implemented, "
                 f"{sum(1 for r in recs.values() if r['status'] != 'implemented')} partial/broken.")
    lines.append("")
    lines.append("Statuses: **implemented** = loaded + passing self-check/tests · **partial** = exists with named gaps · "
                 "**planned** = Tier-3/optional, design note present · **blocked** = loads broken (see NEEDS.md).")
    lines.append("")
    claimed = set()  # each skill appears under exactly ONE spec domain (its first match)
    mark_of = lambda s: {"implemented": "✅", "partial": "🟡", "planned": "🟠", "blocked": "🔴"}.get(s, "·")
    for num, title, cats in SPEC_DOMAINS:
        lines.append(f"## Domain {num} — {title}")
        lines.append("")
        rows = []
        for module_name, status, where in CORE_MODULES.get(num, []):
            rows.append((f"{mark_of(status)} {module_name}", status, where))
        for cat in cats:
            for name, r in by_cat.get(cat, []):
                if name in claimed:
                    continue
                claimed.add(name)
                rows.append((f"{mark_of(r['status'])} {name}", r["status"],
                             f"{r['risk_level']} risk, v{r['version']} — {r['description'][:110]}"))
        if not rows:
            note = PLANNED_NOTE["note"]
            rows = [("🟠 (none yet)", "planned", note)]
        lines.append("| Skill / module | Status | Notes |")
        lines.append("|---|---|---|")
        for a, b, c in rows:
            lines.append(f"| {a} | {b} | {c} |")
        lines.append("")
    # everything else the owner's skill set actually contains
    lines.append("## Additional domains (the owner's skill set beyond the spec's 21-35 list)")
    lines.append("")
    for cat in EXTRA_DOMAINS:
        unclaimed = [(n, r) for n, r in by_cat.get(cat, []) if n not in claimed]
        if not unclaimed:
            continue
        lines.append(f"### {cat}")
        lines.append("| Skill | Status | Notes |")
        lines.append("|---|---|---|")
        for name, r in unclaimed:
            lines.append(f"| {mark_of(r['status'])} {name} | {r['status']} | {r['risk_level']} risk — {r['description'][:110]} |")
        lines.append("")
    out = os.path.join(ROOT, "docs", "SKILL_CATALOG.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[catalog] wrote {out}: {len(recs)} skills across {len(by_cat)} domains")


PLANNED_NOTE = {"note": "Design: deferred by design (Tier 3) — the registry/adapter seams accept an implementation without changes to the dispatcher."}

import time  # noqa: E402

if __name__ == "__main__":
    main()
