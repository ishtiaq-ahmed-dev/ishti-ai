# Model decisions — think/execute loop (research note, 2026-10-06)

Owner asked: "make my agent like a Claude model which thinks, executes, does everything accurately and intelligently."

## Research (internet, cited)

- Anthropic, "Building Effective AI Agents" (anthropic.com/engineering/building-effective-agents): successful systems use simple, composable patterns, not heavy frameworks. Patterns: augmented LLM → prompt chaining (with programmatic **gates** on intermediate steps) → routing → parallelization/voting → orchestrator-workers → evaluator-optimizer. Agents are worth the latency/cost only when flexibility is needed; verifiable feedback (tests/checks) is where loops shine.
- Anthropic Claude Code research (anthropic.com/research/claude-code-expertise): agentic coding works as a tightly coupled model↔tool loop; refine-plan-act style flows reduce chaos (Medium, "The Refine-Plan-Act Pattern", 2026).
- Community practice 2026 (Oracle "The Agent Loop Decoded"; AddyOsmani "Self-Improving Agents"): assemble context → reason → act → verify → revise, with external verification signals and human review of the loop.

## How ISHTI already maps (backend WP2/WP3/WP7, worktree `agent/backend`)

| Anthropic pattern | ISHTI implementation |
|---|---|
| Prompt chaining + gates | WP2 never-early research gate: a final answer may only go out after research reaches COMPLET, else an honest exhaustion report (verified live: "research did not reach COMPLET; answer marked unverified"). |
| Evaluator-optimizer | WP3 decision loop: plan → act → verify → revise with injectable critic + confidence/contradiction checks (observed live: multi-pass runs, ~50–90 s for simple math). |
| Orchestrator-workers | WP7 agent bus: typed envelopes, depth ≤ 3, TTL, dedupe, scoped skills. |
| Routing (small vs big model) | Existing model switching (e.g. `ishti-spark2.5-q8:4b` for fast thinking; heavier models for hard tasks). |
| Verifiable feedback | Runs are durable; verify steps recorded as `run_step`; UI shows "N verification passes done". |

## Recommended next steps (backend agent, in priority order)

1. Add a **voting/parallel verification pass** for factual answers (2–3 critic prompts, majority or all-must-agree) — cheapest accuracy win per Anthropic's parallelization pattern.
2. Keep the decision loop **budgeted**: cap revise passes (already bounded) and emit `run_step` for each pass so the UI keeps showing "thinking" honestly.
3. Route easy tasks to the small local model and escalate only on low confidence — already partially present; measure before changing defaults (see NEEDS.md benchmark gap).
4. Do not add agent frameworks — keep direct Ollama calls per the research and ISHTI's local-first constraint.

Frontend note: the UI already renders each loop pass (`run_step` → RunStatusCard/graph), so any backend loop improvement is visible without UI changes.
