# ISHTI Fix Plan

Principle: fix the three architectural causes in `AUDIT.md §4`, not the nine symptoms.
Reuse what already works (search stack, fetch cascade, memory store, TTS, doctor). Add the
smallest number of new files that removes a whole failure class.

## New files (4)

| File | Why it earns its place |
|---|---|
| `orchestrator/persona.py` | The **single** system-prompt builder. Owner memory + capability facts + length rule, in one place. Kills the five divergent personas (S7) and the memory-injection gap (F2, F6) at once. |
| `orchestrator/response_guard.py` | The **trust boundary** on model output: refusal detection, hallucination-marker detection, history sanitising. Runs before the answer is shown *and* before it is persisted, so F5's poison loop cannot restart. |
| `tools/web/weather.py` | Open-Meteo geocode + forecast. Real data source, no key, no scraping, no LLM prose. Deletes F3 and F4 for the single most common live query. |
| `tools/web/relevance.py` | Source scoring + dictionary/definition-domain rejection. Makes "cite only what is relevant" enforceable rather than a prompt request. |

Everything else is edits to existing files.

## Fix order (each step independently testable)

### 1. Model + config (F1 cause A)
`config.json`: `primary_model` → `qwen2.5:14b`, keep `llama3.2:3b` as `fast_model`.
Benchmark data already in the file justifies it (100/100 quality, 2.51s).

### 2. Memory correctness (F6, S3, S6)
- Fix `get_relevant_memory` context matching: score on shared content words, not whole-value containment.
- Add `default_weather_city` + `response_style` to `Preferences`; set `response_length: short`, `role: AI student`.
- De-duplicate `projects.json` / `important_context.json`; make `add_project` dedupe by name.

### 3. `persona.py` — single LLM entry point (F1, F2, F5, F6, F7)
`build_system_prompt(user_prompt, depth, tools_available, evidence)` returns one block containing:
- who ISHTI is (plain, no "elite", no espionage register),
- the owner block from `UserMemory.format_for_llm` — **always**, on every route,
- the live capability list, so the model cannot claim it lacks a tool it has,
- the depth rule: SIMPLE ≤ 2 sentences · MODERATE ≤ 6 · DEEP report-only-if-asked.

`ModelRouter.generate(prompt, role, depth, timeout)` becomes the only place that calls
`/api/generate`. `crew.py` and `deep_research.py` lose their duplicated request blocks.

### 4. Depth classification (F7)
`IntentDetector.classify` gains `depth: SIMPLE | MODERATE | DEEP`.
DEEP requires an explicit research/report word — never inferred from topic size.
The Executive-Summary template moves from an unconditional instruction to a DEEP-only branch.

### 5. Live-data routing (F3, F4)
- `IntentDetector` gains a `WEATHER` mode.
- `crew.py` routes it to `tools/web/weather.py` with the city resolved from memory when the
  prompt names none. Answer shape: `Tomorrow in Peshawar: 34°C, sunny. [source]` — 1 sentence.
- Query enrichment: any local-intent query ("near me", "local", "here") gets the profile city
  appended before it reaches a search provider.

### 6. Source relevance (F4)
`deep_research` filters the source pool through `relevance.score()`; sources with no content-word
overlap are dropped, dictionary/definition domains are dropped unless the query *is* a definition
query. If 0 survive → say so. Never pad.

### 7. `response_guard.py` (F1, F5)
- Refusal phrases + hallucination markers ("classified", "Operation Phoenix", "intel briefing",
  "security protocols").
- On refusal **and** capability available → one forced retry through `persona.py` with the evidence
  restated; still bad → return the evidence directly rather than the refusal.
- `sanitize_for_history()` runs before `conv_mgr.add_message`, so a bad reply is never replayed.
- Purge the existing poisoned session files.

### 8. Verification (F9)
Replace the hardcoded `verified: True` with actual predicates:
search → non-empty + every result has an http URL · fetch → `status == success` and
`len(content) > 0` · weather → payload has temperature **and** the requested city ·
file write → exists and size > 0 · browser → page object returned and URL loaded.

### 9. TTS (F8)
`server.py` speaks the guard-cleaned answer (not `split("\n")[0][:120]`), respects
`preferences.voice_enabled`, and honours `mute` / `read that` / `stop reading` commands.

### 10. Registry + doctor honesty (S1, S5)
- `capability_registry.probe_all()` actually exercises each capability and writes real status;
  called at startup and by doctor.
- Doctor's browser and TTS checks launch Playwright and synthesise audio bytes instead of reading
  package versions.

### 11. Skill-learning hygiene (S2)
Only persist a learned skill when the plan had >1 stage **and** every stage succeeded.
Delete the existing junk skills.

## Acceptance
All 12 tests of the brief's §12 run against the real stack via `orchestrator/test_e2e.py`,
with output pasted into `docs/TEST_RESULTS.md`.
