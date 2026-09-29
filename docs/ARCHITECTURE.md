# ARCHITECTURE — ISHTI after Prompt 1 (2026-09-29)

Before this pass: see `docs/EXISTING_INVENTORY.md` (the audit of the state as found) and
`PROJECT_MAP.md` (the deeper per-module map, still accurate for arch1 internals).

## 1. What changed in this pass

The two-generation design is kept and **composed**, not replaced:

- **arch1** (dispatcher → rules/Laya → 80+ skills) stays the default execution path.
- **arch2** (two-pass brain) stays switchable, unchanged in its own right.
- A new **core layer** (`ishti/core/`) now sits under both: conversation state, query
  resolution, context assembly, pipeline interfaces, the architecture registry, settings,
  structured logging, and the tool/validation stages. Every part is interface-first and
  swappable from Settings (Part C of the spec).

```
                 ┌──────────────────────────── ishti/core ────────────────────────────┐
                 │ state.py        ConversationStateManager (D1) + topic/task/entities │
                 │ resolver.py     QueryResolver: follow-up → standalone request (D2)  │
                 │ context.py      ContextAssembler: pin topic, summarize history (D4) │
                 │ pipeline.py     STT/Router/LLM/TTS stage Protocols + Tracer (B)     │
                 │ tools.py        ToolDispatcher + ConfirmationPolicy + Validation(B4/B5/E3) │
                 │ architectures.py 6 profiles, health checks, hot switch (C1-C3)      │
                 │ settings.py     pydantic schema, secret split, import/export (C4)   │
                 │ logutil.py      JSON logs + PII/secret redaction (E1)               │
                 └─────────────────────────────────────────────────────────────────────┘
                                        │
   request (text/voice) ──► dispatcher.handle() ──► resolve_request() ──► route ──► skill
                                   │                                      (arch1 or arch2)
                                   └── every component receives the RESOLVED request + state summary (D3)
```

## 2. The conversation-context fix (the critical bug)

Root causes and their fixes (details of the old behaviour in EXISTING_INVENTORY §6):

| Root cause | Fix |
|---|---|
| Only explicit follow-up phrasings were rewritten | `dispatcher.contextualize()` now calls `ishti.core.resolver.QueryResolver` FIRST: any task-carrying or reference-carrying message is rewritten from state (no LLM needed); the old LLM rewriter remains as fallback |
| No session topic state | `ConversationStateManager` (SQLite `data/convo_state.db`) stores active_topic, topic_history, task stack, entities, constraints, last searches, per session; updated EVERY turn inside `resolve_request()` |
| Searches not recorded | `web_search` and `deep_research` call `record_search()` after each search (query + result URLs) so "those results" anchors |
| No constraints remembered | deterministic budget/currency/country/date extraction merged every turn; carried into every rewritten request |
| History truncation pushed the topic out | `ContextAssembler`: pinned state block first, recent turns verbatim, older summarized once, thinking blocks stripped, tool outputs summarized with links (D4) |
| Answers could drift off-topic | D5 validation: deterministic topic check in arch2 pass-2 (re-plan on failure); same `ValidationStage` available to arch1 |

Ambiguity (T3): a "the cheapest one" ask with two live candidates returns ONE short
clarifying question through the existing pending-gate; the owner's answer completes the
resolution. It never routes half-resolved.

Regression protection: `tests/test_context_regression.py` (T1–T7 + units, offline) and
`tests/test_dispatcher_state_flow.py` (T1/T2/T5 through the real `dispatcher.handle()`).
These must never be removed; they are the contract.

## 3. Architecture profiles (Part C)

`ishti/core/architectures.py` declares the six profiles (components, order, settings,
required dependencies). Switching paths — all health-check gated, all hot:

1. Settings screen (`/settings` → Architecture cards, health pill per profile)
2. REST (`GET /api/arch/profiles`, `POST /api/arch/profiles/switch`)
3. Voice/text command ("switch to multi agent mode") — asks yes/no first
4. `config.json → arch.active_profile` (read at startup)

Failed health check → the current profile stays and the reason is returned/spoken.
`arch.active` (legacy arch1/arch2 backend selector) and `arch.active_profile` coexist;
both registries remain single owners of their key. Conversation state lives outside any
profile, so switches never lose the topic (proven by T5).

Profile → runtime mapping today: `router_single_agent` runs arch1's dispatcher+Laya path;
`memory_augmented` additionally pins the state summary into prompts; arch2's two-pass
brain remains available via the legacy `arch.active=arch2` selector and is the target
implementation for the orchestrator/Flowise slots in Prompt 3.

## 4. Settings (Part C4)

`ishti/core/settings.py` — one pydantic schema, 12 sections (Architecture, Models,
Voice STT/TTS, Skills, Memory, Learning, Agents, Integrations, Confirmation, Privacy,
Observability), every field described. Non-secrets in `data/settings.json`; secrets in
`data/settings.secrets.json` (or the OS keychain when `keyring` is installed) — never
exported (placeholders only). Validate-then-apply: an invalid value is refused with the
reason. Import/export/reset in the UI and via REST. Documented field-by-field in
`docs/SETTINGS.md`.

## 5. Interfaces (Part B)

Stage Protocols in `ishti/core/pipeline.py` (`STTStage`, `IntentRouterStage`, `LLMStage`,
`TTSStage`) with adapters wrapping the existing engines (`LocalWhisperSTT`,
`TextFallbackSTT`, `EmbeddingShortlistRouter`, `OllamaLLM`, `LocalTalkerTTS`). A new
engine implements the Protocol and is selected in Settings — no pipeline code changes.
`Tracer` emits `{stage, status, input, output, error, ms}` events per stage (B7) on the
same channel the HUD Thinking panel already consumes. `ToolDispatcher` (B4) adds
timeouts/retries/structured results `{tool, args, status, output, error, duration_ms}`
and runs every candidate action through the config-driven `ConfirmationPolicy` (E3/Rule 6)
BEFORE execution.

## 6. Test & run layout (Part E)

```
tests/
  mocks.py                      MockLLM / MockSearchTool / FakeSkill / TracingSession
  test_context_regression.py    T1-T7 + state/context/tools/settings units (offline)
  test_dispatcher_state_flow.py T1/T2/T5 + arch command through the real dispatcher
  ... (existing 17 files unchanged)
pytest.ini                      one command: python -m pytest
start.bat test                  the same, for Windows users
start.bat settings              opens the Settings screen
```

Live end-to-end remains `python -m ishti.check` (Ollama-dependent, run on the owner's PC).

## 7. What is deliberately stubbed (until Prompts 2–3)

- `memory_augmented`: slot wired (state summary pinned into prompts); the full memory
  engine arrives in Prompt 2.
- `multi_agent_orchestrator` and `flowise_flow`: profile + health check + settings exist;
  the engines are Prompt-3 work. Selecting them today either falls back to the proven
  path (orchestrator) or reports "not configured" (Flowise health check fails cleanly).
- Full observability/export (Prompt 3) builds on the Tracer events emitted here.
