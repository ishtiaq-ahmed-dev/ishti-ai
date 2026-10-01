# Request lifecycle (dispatcher)

Source: `ishti/dispatcher.py` (read in full 2026-10-01). `/api/chat` runs `handle()` on an executor
thread; voice, wake and the remote channels call the same function with a different `channel`.

## Ordering guarantees (the cascade, cheapest first)

1. **Pending yes/no** — a T2 gate answer (yes/no/decline) resolves before any routing; the audit row
   records `confirmed=<ts>`; test mode blocks the side effect at this last step and records it.
2. **Ambiguous-reference completion** — "the cheapest one" answers the resolver's pending question.
3. **reflex.answer** (0.003 ms median) — pure-regex instant commands; never while a question waits.
4. **mic_control**, **arch2/multi-agent/flowise profile commands**, **language lock** — deterministic.
5. **correction re-route** ("that should have been the browser") re-runs the last request and records
   a training example (ftlog `corrected`).
6. **pending-sticky** hands the message back to the skill that asked a question.
7. **contextualize** — `ishti.core.resolver` rewrites follow-ups from Conversation State
   (deterministic first; LLM rewriter only when the state cannot anchor; ONE clarifying question
   when genuinely ambiguous, T3).
8. **planner** — `candidate()` clause-splits (SMALLTALK excluded); `plan()` LLM-splits into ≤4 steps
   only when ≥2 distinct skills match; steps without `{prev}` run in parallel; a failed step stops
   dependents; any pending confirmation pauses the plan.
9. **route** — corrections.learned (0.007 ms at current volume) → manifest matchers (0.24 ms over
   122 skills) → Laya typed decisions (~35 ms fp16, conf ≥ 0.2) → default chat; then
   `recency_tiebreak` within 0.05 margin. Router never picks NON_ROUTABLE statuses
   (broken/draft/disabled/retired) or Settings-disabled skills (5 s TTL cache).
10. **_run loop** — a skill returning None is "not mine": the next best choice runs (max hops 2 via
    ESCALATE map + `fell_short` LLM judge for web skills). Failures are caught, logged
    (ErrorLogger + NEEDS.md), and stated plainly. ScopeError is reported as a permission boundary.
11. **_finish** — cited-sources filter, secret masking everywhere (history, saved chat, events),
    history [-20:], then ONLY background threads: semantic remember, ftlog, arch2 consolidation,
    owner learn, learning turn (reward + Reflexion + decay), monitor inspection.

## Concurrency

- One session can run several independent requests at once: `View(shared, own=…)` gives each
  request its own trace/token-stream/route; follow-ups (`FOLLOW_UP`, `CORRECTION`, a yes/no)
  wait on the session's condition variable (600 s cap).
- Threads/pools spawned inside a request inherit the kill generation and audit/scope context
  (`killswitch.propagate` patched into Thread.start/run and ThreadPoolExecutor.submit).

## Failure policy

Nothing above the skill layer may break the reply: every background hook, the resolver, and the
profiles (arch2/multi-agent/flowise) are wrapped in try/except with a printed reason. The LLM being
down degrades to a plain sentence (`llm._create` starts Ollama once and retries 15 s), never a
traceback to the owner.
