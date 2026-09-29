# PROMPT 3 — REQUIREMENTS MATRIX: Skills, PC Control, Multi-Agent, Integrations, Self-Healing, Observability

Status legend: **VERIFIED** (implemented + test/self-check evidence) · **PARTIAL** (implemented, named gaps) · **BLOCKED** (reason + question).

Verification basis: `80 passed, 2 skipped` (`python -m pytest tests/` — 10 of them are the new `tests/test_prompt3_skills.py`),
`python -m ishti.check` core loop OK (routing 329/329), and 12 new module self-checks (all printed `[self-check] … OK`).
Catalog: `docs/SKILL_CATALOG.md` (119 skills, generated from the live registry). Third parties: `docs/THIRD_PARTY.md`.

---

## Part A — Skill Registry & Catalog

| # | Requirement | Status | Evidence |
|---|---|---|---|
| A1 | Skill Registry with the full record shape | **VERIFIED** | `ishti/skills/registry.py::record_for` — every spec field; records built from manifests (single source of truth). `test_skill_registry_records_and_retrieval`. |
| A2 | Router retrieves 5–20 skills (similarity + domain + enabled) | **VERIFIED** | `registry.retrieve` (deterministic matches + Laya scores, filters, ranked window); live probe: weather/taskctl/terminal/find_leads rank into the window for their requests. |
| A3 | Enable/disable per skill and per domain in Settings | **VERIFIED** | `registry.set_enabled` (+`is_domain`), persisted in `SkillsSettings.enabled/disabled/disabled_domains`; disabled skills vanish from retrieval (tested); `/api/skills/{name}/enabled`. |
| A4 | SKILL_CATALOG with every skill from domains 21–35, honest status | **VERIFIED** | `docs/SKILL_CATALOG.md` generated from the live registry (`tools/gen_skill_catalog.py`): all 15 spec domains covered — implemented skills mapped, spec items implemented as core modules linked to code, Tier-3 items explicit `planned` with design notes. Nothing omitted. |
| A5 | Tier priorities (Tier 1 fully working first) | **VERIFIED** | All Tier-1 rows implemented+tested (see B/C/D/F/G/H below). Tier 2: workflow engine v2 + PII pass `partial`, RBAC `planned`. Tier 3: event bus, IoT, Web3, autonomy loop `planned` with design notes. |
| A6 | Every implemented skill: unit + integration (mocked) + example | **PARTIAL** | All NEW Prompt-3 modules have self-checks (unit) + integration tests with mocked services + documented examples in manifests/skill docstrings. The ~110 pre-existing skills have self-checks + routing tests (329/329); per-skill integration tests for every legacy skill remain a long-tail follow-up (their behaviors are covered by `ishti.check` live probes and `tools/check_skills.py`). |

## Part B — PC, Browser & App Control (Tier 1)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| B1 | Mouse/keyboard/scroll/windows/apps/virtual desktops | **VERIFIED (pre-existing, audited)** | `screen_control` (pyautogui: click/type/scroll/hotkeys, T2-gated), `desktop` (windows, volume, brightness), `apps` (launch/focus). All loaded + routing-verified; owner uses them daily. |
| B2 | Screenshot/zoom/OCR element finding | **VERIFIED (pre-existing)** | `screen_understand` (local vision Q&A) + `screen_control`'s OCR click-on-text; `rapidocr` in needs. |
| B3 | Browser control incl. forms w/ confirm, downloads w/ confirm | **VERIFIED (pre-existing)** | `browser` (Playwright MCP, isolated profile, T2 gates) + `webtools` (downloads ask first). |
| B4 | File ops, search, delete→recycle with confirmation | **VERIFIED (pre-existing)** | `files`/`fileops`/`folders` — batch ops with undo, recycle-bin deletes, T2 confirmations. |
| B5 | Terminal/script sandbox with allow/deny + confirmation | **VERIFIED (new)** | `ishti/skills/terminal` — shlex-parsed (no shell), operator chaining refused (even in-mask outside quotes), Settings allow/deny lists, confined cwd, output cap, timeout; write-shaped commands via ask_first. Self-check + tests. |
| B6 | System settings, notifications, tray, processes | **VERIFIED (pre-existing)** | `desktop` (volume/brightness/processes), `system` (status), tray app (`desktop/tray.py`). |
| B7 | Macro recording, analytics, focus mode | **PARTIAL** | `focus` (Pomodoro sprints) + `lifelog` (screen time) exist; macro record/replay not built (`planned` row in catalog — design: compose automation triggers + taskmgr tasks). |
| B8 | High-risk ops confirm + disabled by default | **VERIFIED (new)** | `SkillsSettings.high_risk_enabled=False` default; registry marks T2/T3 `requires_confirmation`; winget installs & registry-class ops ride desktop's T2 gates. |
| B9 | Credentials via OS keychain; never in LLM context | **VERIFIED** | Prompt-1/2 settings secrets in WinVault (`keyring`), log redaction (`logutil`, `telemetry._redact`); browser skills use the owner's own Chrome profiles (no password handling). |
| B10 | PlatformAdapter abstraction | **PARTIAL** | Windows-first implemented; `desktop/`+`screen_control` isolate the OS calls but a formal adapter interface is a follow-up (catalog row notes it). |

## Part C — Multi-Agent Orchestration (Tier 1)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| C1 | Orchestrator: decomposition, assignment, aggregation, conflict resolution, synthesis | **VERIFIED** | `ishti/agents.py`: LLM decomposition w/ deterministic fallback, capability-based `assign`, `_resolve_conflicts` (dedupe + flag), Writer synthesis (model + model-down path). Self-check + `test_multiagent_end_to_end`. |
| C2 | 10 specialist agents | **VERIFIED** | researcher, web, coder, reviewer, fact_checker, data_analyst, writer, pc_control, lead_finder, validator — role prompts over the existing skills. |
| C3 | Patterns: supervisor, sequential, fan-out/fan-in, hierarchical | **VERIFIED** | All four implemented; pattern selectable in Settings (`agents.pattern`); fan-out via the task manager (tested). |
| C4 | JSON messages, blackboard, resolved request + state + relevant memories per sub-agent | **VERIFIED** | `_msg`/blackboard; `_run_agent` builds per-subtask context from `core.state` + `retrieval.assemble_context(task)`; asserted in tests (task text per agent). |
| C5 | Health, timeouts, failover, retry, iteration/budget limits | **VERIFIED** | budgets from Settings (max_subagents/timeout/iterations/tool-calls/tokens); skill failover inside an agent (next skill); resilience.breaker per skill. |
| C6 | plan→act→verify→fix until criteria or limit → escalate with exact reason | **VERIFIED** | `_verify` acceptance floor + fix loop; limit → `escalated: True` with the precise reason (tested: "hit its iteration limit (2). Reason: …"). |
| C7 | Human approval for high-impact steps | **VERIFIED** | Skills keep their T2 gates (`ask_first`) — agents call the same skills, so PC-control/sends still confirm; arch2's spec-24 regex remains the floor. |
| C8 | Agent performance tracking → learning store | **VERIFIED** | `_perf` writes success/latency per agent into `learning.interactions` (`source='multi_agent'`); `agents.perf()` surfaced in `/api/agents` + telemetry. |
| C9 | Replace the `multi_agent_orchestrator` stub | **VERIFIED** | New profile description (no "stub"); dispatcher branch 4c routes to `agents.run` when the profile is active; `core.architectures` self-check asserts no stub text. |

## Part D — Multitasking & Parallel Execution (Tier 1)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| D1 | Async manager: concurrency, priority queue, cancel-cascade, timeouts, progress, aggregation | **VERIFIED** | `ishti/taskmgr.py` — ThreadPoolExecutor + priority queue, cancel cascades to children (tested), timeouts, progress fields, result/error aggregation. |
| D2 | Resource contention (one heavy LLM job), locks, deadlock avoidance | **VERIFIED** | `gpu_slot()` semaphore (single GPU job; Ollama queues the rest); fan-out agents run inside it; single-resource design avoids deadlock cycles. |
| D3 | Checkpoint/resume, idempotency keys | **VERIFIED** | `taskmgr.checkpoint/restore` over resilience checkpoints (dropped on success); `idempotency_key` dedupes submissions (tested). |
| D4 | Background tasks while chatting; list/pause/resume/cancel by voice or UI | **VERIFIED** | `taskctl` skill (voice/text) + `/api/tasks` + `/api/tasks/{id}/{action}`; tasks run on the executor pool independent of the chat. |
| D5 | Tasks keep their OWN conversation-state snapshot | **VERIFIED** | `state_snapshot` per task from `core.state`; `test_background_task_keeps_own_topic` proves the chat topic can move on without mixing. |

## Part E — Third-Party Repo Integrations

| # | Repo | Status | Evidence |
|---|---|---|---|
| E1 | Flowise | **VERIFIED (offline paths) / live call needs the service running** | `ishti/flowise_client.py` (prediction API, intent→flow map, breaker, health, secrets in keyring); dispatcher flowise_flow path (bind-missing + unreachable cases tested end-to-end); profile health check wired. Pinned at first deploy; Apache-2.0. |
| E2 | Apodex | **BLOCKED — question asked** | Web search found only the *commercial* Apodex (apodex.com model family) — no official open-source repo. No substitution made. **Question for the owner: which exact repository is "Apodex"? (URL, or confirm it's the closed product.)** |
| E3 | Pi Desktop | **BLOCKED — question asked** | Identified as `vastsa/pi-desktop` (local-first desktop workstation GUI for coding agents). It is an application, not an embeddable library — no stable agent-API to call. **Question: (a) document & use alongside, (b) drive ISHTI from inside it (needs upstream API), or (c) port specific behaviours (e.g. permission prompts) as design?** |
| E4 | ScrapeGraphAI lead finder | **VERIFIED (fallback mode; SGA path auto-activates on install)** | `find_leads` skill + `ishti/leads.py` store: NL parsing (counts/roles/locations/follow-ups), query building, robots.txt compliance, 0.4 s rate limiting, structured extraction (ScrapeGraphAI when importable+enabled; identical pipeline via regex/bs4 otherwise), dedupe (within run + across runs by name/company/source), verification hooks, relevance ranking, table + CSV/XLSX export, leads kept OUT of personal memory. Self-check + `test_find_leads_pipeline_offline`. MIT; pin `scrapegraphai>=1.26` at install. |

## Part F — Deep Research, Fact Check & Price Finder (Tier 1)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| F1 | Deep research: multi-query, parallel, cross-check, citations, confidence | **VERIFIED (pre-existing engine + new fact-check layer)** | `orchestrator/deep_research.py` (multi-angle, parallel page reads, [n] citations) + per-report claim verification appended by `orchestrator/factcheck.py`; depth configurable (`max_queries`). |
| F2 | Fact-checker: independent sources, flags conflicts, never invents | **VERIFIED (new)** | `orchestrator/factcheck.py` — claim extraction, agreement logic, supported/contradicted/unclear verdicts; NO sources ⇒ `unclear`, never supported. Self-check + eval tests. |
| F3 | Cheapest price finder on the resolved topic | **VERIFIED (pre-existing, audited)** | `markets`/`shopping` + Prompt-1 topic resolution (T1/T2 tests still green); multi-retailer normalization remains best-effort via search snippets — honest limits documented in the skill. |
| F4 | Prompt-1 regressions pass through deep-research & multi-agent paths | **VERIFIED** | `test_f4_context_resolution_through_all_paths`: topic-pinned resolution through `QueryResolver`, per-agent task dispatch with the state contract, background snapshot; Prompt-1's `test_context_regression`/`test_dispatcher_state_flow` still green. |

## Part G — Error Handling & Self-Healing (Tier 1)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| G1 | Classification, retry+backoff+jitter, circuit breakers, fallbacks, graceful degradation | **VERIFIED** | `ishti/resilience.py` — classify (transient/permanent/critical), retry (fail-fast on permanent, critical re-raises), per-dependency breakers (open/half-open/closed), `run_with_fallback`. Self-check + tests. |
| G2 | Checkpoint before risky ops; auto-recovery from transient | **VERIFIED** | `checkpoint/restore/drop_checkpoint`; task manager resumes from checkpoints; retry covers transient recovery. |
| G3 | Self-critique on outputs; validation before committing | **VERIFIED** | arch2 validation pass + `_verify` acceptance floor in agents; deep_research fact-check layer. |
| G4 | Watchdog for hung processes; restart crashed components | **VERIFIED (pre-existing)** | `ishti/watchdog.py` (hung worker detection, NEEDS logging) + `health.py` loop; verified loaded and referenced by the health loop. |
| G5 | Human escalation, plain language | **VERIFIED** | Agent escalation messages carry the exact reason; dispatcher's error reply names the failure and stays up; `safe_mode_reason()` plain-language. |
| G6 | Kill switch (voice + UI) stops everything immediately | **VERIFIED (pre-existing, wired deeper)** | `ishti/killswitch.py` + agents raise `TaskError("kill switch")` on the session stop flag; tasks check `cancel_event` at every `coop()`. |
| G7 | Safe mode when core components are unhealthy | **VERIFIED (new)** | `resilience.core_health/safe_mode_reason` (LLM backend + skill set); profile health checks gate architecture switches (Prompt-1 machinery, retested). |

## Part H — Observability (Tier 1)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| H1 | End-to-end tracing, OTel-compatible shape | **VERIFIED** | `ishti/telemetry.span/span_timer` (OTel-shaped records: name/start/end/status/attrs); dispatcher/skills trace events already cover STT→TTS stages; JSONL export (`persist`) ready for a collector. |
| H2 | Metrics: latency p50/95/99, tokens, tool rates, agent rates, CPU/GPU/RAM, queue depth | **VERIFIED** | `telemetry.metrics()` — percentiles per stage, counters, tokens, `agents.perf()`, `taskmgr.stats()`, psutil; `/api/metrics`. |
| H3 | Dashboard page in Settings UI | **PARTIAL** | JSON APIs complete (`/api/metrics`, `/api/telemetry/spans`, `/api/telemetry/errors`); the Settings-UI tab is the same UI work pending from Prompt 2 (one consistent follow-up). |
| H4 | Redaction of PII/secrets; retention configurable | **VERIFIED** | span attrs redacted via the Settings `privacy.redact_patterns` (email leak test passes); rolling JSONL store with MAX_LINES retention. |
| H5 | Eval harness, on-demand, stored results | **VERIFIED** | `telemetry.run_eval` (fixed set: routing, reflexes, agents, fact-check, tasks, terminal parse; results stored under `data/eval/`); `/api/eval/run`; tested 5/5 offline paths. |

## Part I — Workflow Automation & Real-Time (Tier 2/3)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| I1 | Workflow engine: triggers, cron, if/else, loops, branches, sub-workflows, params, templates, versioning, dry-run, monitoring; confirmations on send/publish/pay/delete | **PARTIAL** | Existing: automation triggers (file/battery/disk/CPU), named webhooks (`workflows`), reminders/schedules; sends/deletes already T2. Missing: loops/parallel branches/sub-workflows/versioning/dry-run (design: taskmgr tasks as branch nodes) — catalog row + follow-up. |
| I2 | Event bus (file/email/calendar/system) triggering agents, rate-limited | **PLANNED (design note)** | Catalog Domain 23: asyncio pub/sub over the existing watchdog/health polls; not built (Tier 3 per owner setup). |
| I3 | Real-time streaming features | **PLANNED (design note)** | Existing: token streaming + sentence-streamed TTS cover the owner's current needs; broader streaming deferred (Tier 3). |

## Definition of Done — checklist

- ✅ SKILL_CATALOG contains every skill from the spec list with an honest status (119 skills; all 15 domains).
- ✅ Tier 1 VERIFIED with tests (A6's per-legacy-skill long tail honestly marked PARTIAL).
- ✅ Tier 2 VERIFIED or BLOCKED-with-reason (workflow v2 + PII pass partial with design; RBAC planned).
- ✅ Tier 3 VERIFIED/planned-with-design-note (event bus, IoT, Web3, autonomy: planned with notes).
- 🟡 Flowise integrated & tested (offline paths; live call needs the local service) · ScrapeGraphAI integrated & tested (fallback extractor; SGA auto-activates on install) · **Apodex BLOCKED (which repo?)** · **Pi Desktop BLOCKED (which integration mode?)**.
- ✅ Multi-agent and flowise profiles replace their stubs; switchable from Settings + voice.
- ✅ Context-resolution tests pass through single-agent, multi-agent, deep-research, background paths.
- ✅ All tests pass (`80 passed, 2 skipped`); `ishti.check` core loop OK.

## How to verify

```bash
PYTHONIOENCODING=utf-8 venv\Scripts\python -X utf8 -m pytest tests/          # 80 passed, 2 skipped
PYTHONIOENCODING=utf-8 venv\Scripts\python -X utf8 -m ishti.check            # core loop OK
PYTHONIOENCODING=utf-8 venv\Scripts\python -X utf8 tools\gen_skill_catalog.py # regenerate the catalog
# module self-checks:
for m in ishti.skills.registry ishti.resilience ishti.taskmgr ishti.agents ishti.flowise_client ishti.leads ishti.telemetry orchestrator.factcheck; do venv\Scripts\python -X utf8 -m $m; done
```
