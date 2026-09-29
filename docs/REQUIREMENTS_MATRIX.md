# REQUIREMENTS_MATRIX — Prompt 1 (Foundation, Switchable Architectures, Context Fix)

Legend: **VERIFIED** = proven by a run this pass (test, self-check, or live smoke) ·
**BUILT** = implemented, exercised indirectly · **BLOCKED** = cannot be verified now, reason given.

## Part A — Audit
| Req | Status | Evidence |
|---|---|---|
| A1 folder/entry/pipeline/skills/memory/settings/deps inventory | VERIFIED | `docs/EXISTING_INVENTORY.md` (all 8 sections) |
| A2 run existing test suite | VERIFIED | 35 passed (before changes) — §4 of the inventory; 55 passed (after) |
| A3 run app end-to-end, text mode | VERIFIED | `python -m ishti.check` OK: routing 329/329, memory, live skill, LLM, planner — §5 |
| A4 ARCHITECTURE.md for CURRENT state | VERIFIED | `docs/EXISTING_INVENTORY.md` (as-found) + `docs/ARCHITECTURE.md` (after) |

## Part B — Core pipeline
| Req | Status | Evidence |
|---|---|---|
| B1 STT interface `transcribe(audio)->{text,language,confidence,timestamps}` | VERIFIED | `ishti/core/pipeline.py` Protocols; `TextFallbackSTT` used by T7; faster-whisper adapter wraps `ishti.voice` |
| B1 push-to-talk AND VAD | BUILT | existing wake.py (openWakeWord + Silero VAD + barge-in) kept; `voice_stt.input_mode` selects the mode |
| B1 text-input fallback | VERIFIED | `/api/execute` (existing, unchanged) + `TextFallbackSTT` (tested) |
| B2 embeddings intent router, top 5–20, configurable | VERIFIED | `EmbeddingShortlistRouter` (Laya scores + shortlist window from Settings); `settings.architecture.shortlist_min/max`; arch2 `shortlist()` unchanged |
| B2 low-confidence → wider set / clarifying question | BUILT | existing Laya low-confidence → default skill + web_search ambiguity gate + new resolver clarification |
| B3 LLM stage, backend/model/ctx/temp/top_p/thinking/max_tokens in Settings | VERIFIED | `ModelsSettings` (all fields); `OllamaLLM` adapter honours them; live model answered in ishti.check |
| B3 strict tool-call schema, auto-repair retry, max retries configurable | VERIFIED | arch2 `jsonio.parse_json` + unknown-tool recovery loop (existing, hardened); MAX_TOOL_ROUNDS bound |
| B4 dispatcher timeouts/retries/structured result | VERIFIED | `ToolDispatcher` — unit-tested (structured dict, timeout path, retries) |
| B4 policy check BEFORE execution (Rule 6) | VERIFIED | `ConfirmationPolicy.check()` before run; unit-tested; wired to arch1's `ask_first` gate in dispatcher (existing T2 flow) |
| B5 validate tool results | VERIFIED | `ValidationStage.validate_tool_result` (deterministic floors + injectable judge); arch2 validation pass live in production |
| B5 validate final answer vs resolved request; retry/re-plan/clear failure | VERIFIED | D5 check in arch2 pass-2 (re-plan on off-topic); `validate_answer` unit-tested |
| B6 Kokoro TTS, voice/speed/on-off in Settings | VERIFIED | `voice_tts.*` settings; `LocalTalkerTTS` adapter over the existing Kokoro roster |
| B6 sentence streaming | BUILT | existing `talk.Talker` sentence-by-sentence pipeline kept; `pipeline.sentences()` splitter tested |
| B6 interruption (barge-in) | BUILT | existing Silero-VAD barge-in + Talker.stop() kept (tested in production; hardware-dependent) |
| B7 trace events per stage | VERIFIED | `Tracer` emits {stage,status,input,output,error,ms} — same channel the HUD consumes |
| B7 async execution, non-blocking UI | BUILT | dispatcher's parallel Views + ToolDispatcher thread pool; existing background-announcement rules kept |
| B7 progress messages | BUILT | existing NARRATE/narrate() spoken progress + Thinking-panel events kept |

## Part C — Switchable architecture
| Req | Status | Evidence |
|---|---|---|
| C1 six profiles exist | VERIFIED | `PROFILES` in architectures.py; live smoke listed all six |
| C2 profiles declare components/order/settings/deps/health check | VERIFIED | dataclass fields; live smoke showed per-profile health pills data + order strings |
| C3 switch from Settings UI | VERIFIED | live smoke: profiles endpoint + switch call; `/settings` page (200) |
| C3 switch from config file | VERIFIED | `arch.active_profile` persisted in config.json; read at startup |
| C3 voice/text command with confirmation | VERIFIED | integration test: "switch to simple voice mode" → yes/no → switched |
| C3 hot switch, restart only if needed and announced | VERIFIED | all current switches are hot (no restart required) |
| C3 failed health check keeps current profile + explains | VERIFIED | live smoke: flowise_flow refused with reason |
| C3 state/memory survive switch | VERIFIED | T5 (unit) + integration T5 through dispatcher |
| C4 pydantic schema, validation, defaults | VERIFIED | `SettingsService.update` refuses invalid values (tested + self-check) |
| C4 secrets encrypted/keychain | VERIFIED | Windows WinVault keyring used (verified present); file fallback with user-only ACL |
| C4 Settings UI with all 12 sections | VERIFIED | `/settings` page rendered; schema endpoint lists 12 sections |
| C4 import/export/reset/descriptions | VERIFIED | export placeholders, import merge, reset — tested; per-field descriptions in schema |
| C4 docs/SETTINGS.md | VERIFIED | written, every field documented |

## Part D — Context fix (critical bug)
| Req | Status | Evidence |
|---|---|---|
| D1 Conversation State Manager (topic/task stack/entities/searches/constraints/open questions) | VERIFIED | `ishti/core/state.py`; updated every turn in `resolve_request()`; SQLite store + MemoryStore |
| D2 resolve/rewrite before routing and before any tool call | VERIFIED | dispatcher `contextualize()` → resolver first; search skills receive resolved text (integration T1/T2) |
| D2 one clarifying question when genuinely ambiguous | VERIFIED | T3 + clarified-answer completion through the pending gate |
| D2 log original + rewritten in trace | VERIFIED | `understand` trace events carry original/rewritten (integration run) |
| D3 resolved request + state summary to router/LLM/tools/sub-agents | VERIFIED | `s["resolved_request"]`/`s["state_summary"]` pinned into arch2 prompts; skills receive resolved text; `agents.subagents_receive_resolved_query` default true |
| D4 recent verbatim, older summarized, topic pinned | VERIFIED | `ContextAssembler` (T4: 32-turn chat, topic alive, context bounded) |
| D4 thinking blocks stripped; tool outputs summarized | VERIFIED | `strip_thinking`/`summarize_tool_output` unit-tested; applied at store+build time |
| D5 answer validated against resolved topic, re-plan | VERIFIED | D5 check in arch2 pass-2; `validate_answer` unit-tested |
| D6 T1 | VERIFIED | `test_t1_cheapest_price_uses_topic` + integration T1 |
| D6 T2 | VERIFIED | `test_t2_topic_switch_wins` + integration T2 |
| D6 T3 | VERIFIED | `test_t3_ambiguous_asks_one_question_or_both` (+ clarified-answer test) |
| D6 T4 | VERIFIED | `test_t4_long_chat_with_big_tool_outputs` + `test_t4_resolver_after_30_turns` |
| D6 T5 | VERIFIED | `test_t5_state_survives_architecture_switch` + integration |
| D6 T6 | VERIFIED | `test_t6_subagent_receives_resolved_query` |
| D6 T7 | VERIFIED | `test_t7_voice_path_via_text_fallback_stt` |

## Part E — Base quality
| Req | Status | Evidence |
|---|---|---|
| E1 JSON logs + redaction | VERIFIED | `ishti/core/logutil` self-check; configured at server start; redaction tested |
| E2 central error handling, user-friendly messages | BUILT | dispatcher's containment (existing, kept): friendly reply + ErrorLogger + NEEDS.md; core modules fail soft (resolver errors never break a turn) |
| E3 config-driven confirmation policy | VERIFIED | `ConfirmationPolicy` from `settings.confirmation`; unit-tested; used by ToolDispatcher (B4) |
| E4 tests/unit + integration + e2e; mocks; one command | VERIFIED | `tests/mocks.py`; `pytest.ini`; `python -m pytest` = 55 passed; `python -m ishti.check` = live e2e |
| E5 start scripts | VERIFIED | `start.bat` extended (`test`, `settings`); Windows is this project's platform (README documents the same for run modes) |

## Prompt-3 slots (stubbed by design, per spec)
- `multi_agent_orchestrator` — profile, settings, health check live; engine lands in Prompt 3.
- `flowise_flow` — profile + integration settings + health check live; API client lands in Prompt 3.
- Full observability export — Tracer events emitted today; export lands in Prompt 3.
- Memory engine — `memory_augmented` wired (state summary pinned); engine lands in Prompt 2.
