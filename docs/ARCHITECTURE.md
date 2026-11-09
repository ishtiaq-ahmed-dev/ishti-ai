# Architecture

ISHTI has one routing architecture. The decision stage calls the model at most once; selected skills,
translation and optional memory extraction may make additional calls. Complete task cost is measured separately.

## 1. The request path

Every input (chat, voice, WhatsApp or Telegram, scheduled jobs) reaches `ishti.dispatcher.handle()`. It runs these
steps in order and stops at the first one that answers:

1. **Guards (no model).** A pending yes/no confirmation is answered first. Then reflex commands (time, date, volume,
   stop talking), boundaries (for example, a request to skip confirmation is refused), memory commands, and the
   language commands.
2. **Rules (no model).** `ishti/tool_routing.py` picks a skill from its own triggers, cue words, learned corrections
   and the skill shortlist. If a rule owns the request, the skill runs directly through the dispatcher's skill runner.
   Measured on a labelled set of 331 requests (counted from the source on 2026-10-16). The figure this line
   used to quote - 114/120, 95% - came from a list less than half that size and has **not** been re-measured
   against the current one, so do not repeat it as the present accuracy. The eval in `ishti/check.py` is the
   live gate (bar 85%) and prints its own count, accuracy and median, still with no model call.
3. **The one model call (`ishti/brain.py`).** For everything a rule does not own, the model sees the conversation, the
   retrieved memory, and up to 12 candidate skills. It replies with either:
   - one or more tool calls, `{"calls": [{"tool": "<exact name>", "input": "<standalone request>"}]}`, or
   - a direct answer in prose (greetings, explanations, timeless facts, questions about the conversation).

   Each tool call runs through `ishti/skill_execution.py`, which applies the skill's scope, its tier (T0 to T3) and the
   owner's approval for T2 actions. The skill's own reply is the answer. No validation call and no rewrite call follow.
4. **Reply and record.** The reply is checked for claims that no tool backed up, saved to the chat, and logged to the
   audit trail. Memory extraction and learning run in the background. Memory extraction only runs for turns that can
   state something lasting (`memstore/consolidate.py: worth_extracting`), so ordinary turns cost no extra call.

Measured on the owner's PC with the local planner model (`ishti-spark2.5-q8:4b`, 17 prompts, three runs): tool or
direct choice was 17 of 17 each run, with exactly one model call per turn and about 0.9 s per decision. That set is
small. It does not measure accuracy on the full skill catalogue.

## 2. Model providers

The model is set in `ishti/config.json` under `llm`:

- `"provider": "local"` (default): Ollama on this PC. The VRAM governor (`ishti/vram.py`) keeps the model fully on the
  GPU and never splits it between GPU and CPU.
- `"provider": "api"`: any OpenAI-compatible endpoint (`api.provider`: `openai`, `anthropic`, `gemini`, `openrouter`, or a
  custom `base_url`). One selected model serves the configured roles; skills and background extraction may make additional calls. The key is read from the
  environment variable named in `api.api_key_env` and is never written to config or logs.

`ISHTI_LLM_PROVIDER` overrides the provider for one run. In API mode the boot skips the local engine and proves the key
with one short call before the first request.

## 3. Skills

A skill is a folder with `manifest.json` (name, description, route, tier T0-T3, status, scope, dependencies) and
`skill.py` (`run(text, session)` returns `{"reply", "sources"}`, optionally `match(text)`). Skills are loaded by
`ishti/skills/__init__.py`. A skill whose status is `disabled`, `broken`, `draft` or `retired` is never routed to.

Tiers: T0 read-only, T1 reversible local change, T2 needs the owner's yes before it runs, T3 blocked unless the
owner enables it in config. Skills that act on the PC use accessibility, not screenshots: `ui_read` lists the named
controls of a window (T0), and `ui_act` clicks or types into one exact named control after the owner says yes (T2).
Secret fields are refused.

## 4. Memory

| Store | Module | Holds |
|---|---|---|
| Exact facts | `ishti/facts.py` (SQLite) | One value per (entity, attribute); updates keep the previous value |
| Recall index | `ishti/semantic.py` (local Chroma / API SQLite) | Local mode: Ollama vectors; API mode: versioned local lexical index, no embedding download |
| Conversation state | `ishti/core/state.py` | Active topic, task, entities, constraints |
| Layered memory | `ishti/memstore/` (SQLite) | Semantic, episodic and procedural memories with confidence; contradictions supersede |
| Relationship graph | `ishti/rgraph.py` | People and typed edges; private nodes are never exported |

`ishti/memstore` is the memory store only. The two-pass architecture it used to belong to has been removed. Its
settings are under `memstore` in `config.json`.

## 5. Removed

The following were removed to leave one architecture: the two-pass arch2 brain and its validation and rewrite calls,
the architecture switch and its endpoints, the multi-agent orchestrator and its agent bus, the Flowise flow adapter,
the architecture profile registry, the Laya classifier and its router, the LLM planner that split requests into steps,
the LLM reference rewriter, the LLM tie-breaker in the rule router, the critic loop in the decision module, the
Business profile, and the screenshot and OCR screen control (`screen_control`, `screen_understand`, `desktop_control`,
the multi-screen agent).

## 6. Where to look

| Concern | File |
|---|---|
| Entry point and guards | `ishti/dispatcher.py` (`handle`, `_handle`, `_run`) |
| Rules and shortlist | `ishti/tool_routing.py` |
| The one model call | `ishti/brain.py` |
| Skill execution gates | `ishti/skill_execution.py` |
| Model provider and roles | `ishti/llm.py`, `ishti/config.json` (`llm`) |
| Boot stages | `ishti/lifecycle.py` |
| HTTP API | `interface/web_ui/server.py`, `ishti/contract_api.py` |
