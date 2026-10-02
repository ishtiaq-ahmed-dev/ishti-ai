# SETTINGS — every setting documented (Prompt 1, Part C4)

> **Status note (2026-10-02, documentation pass).** The schema and service described here are real and in use:
> `ishti/core/settings.py` (`get_settings_service()`), imported by the agents, Flowise client, governed-search gateway,
> skill registry and others. The **REST routes and the `/settings` page described below are not present in the code**:
> no module defines `/api/settings`, `/api/settings/schema`, `/api/settings/export`, `/api/settings/import` or
> `/api/settings/reset`, and `interface/web_ui/server.py` has no `/settings` route (`start.bat settings` opens a path the
> server does not serve; `interface/web_ui/static/settings.html` calls the missing routes). The dashboard's own Settings
> page uses other endpoints (`/api/manage/*`, `/api/arch*`). The field tables below describe the schema as declared in
> `ishti/core/settings.py` and were not re-verified field by field. Also: the default port is **8888**, not 8000.

Open the screen: **http://127.0.0.1:8000/settings** (or `start.bat settings`).
REST: `GET/POST /api/settings`, `GET /api/settings/schema`, `GET /api/settings/export`,
`POST /api/settings/import`, `POST /api/settings/reset`.

Storage: `data/settings.json` (validated on load; defaults fill gaps). Secrets live in
`data/settings.secrets.json` — or the OS keychain when the `keyring` package is available
— and are never included in an export (placeholders only). Every change is validated
first: an invalid value is refused with the reason and never lands on disk.

## Architecture
| Field | Default | Meaning |
|---|---|---|
| active_profile | router_single_agent | Which of the 6 architecture profiles answers (simple_voice, router_single_agent, memory_augmented, multi_agent_orchestrator, flowise_flow, custom) |
| confirm_before_switch | true | Ask yes/no before a voice/text switch command |
| shortlist_min / shortlist_max | 5 / 20 | The 5–20 skill window shown to the LLM |

## Models
backend (ollama | llamacpp | vllm | openai_compat), base_url, main_model (qwen3:14b),
validation_model, fallback_model, coder_model, fast_model, context_length (32768),
temperature (0.3), top_p (0.95), thinking_mode (true), max_tokens (1024).
Backend swap = change backend+base_url; no code edits.

## Voice STT
engine (faster_whisper | text_fallback), whisper_model (turbo), device (cuda), compute
(float16), language (en), input_mode (push_to_talk | vad | wake_word), vad_threshold
(0.6), barge_in (true). `text_fallback` is the no-microphone/testing path.

## Voice TTS
enabled (true), engine (kokoro | piper | off), voice (roster name), speed (1.0),
stream_sentences (true — start speaking the first sentence while the rest generates;
barge-in stops it).

## Skills
enabled (empty = all), disabled (never routed to).

## Memory
enabled (true), state_db (empty = data/convo_state.db — the Conversation State Manager's
store), recall_k (6), max_distance (0.45).

## Learning
enabled (true — behavior-data collection; weights never change at runtime), proactive
(false — no volunteered suggestions).

## Agents
max_subagents (4), subagent_timeout_s (300), subagents_receive_resolved_query (true —
D3: sub-agents always get the resolved request + state summary, never the raw message).

## Integrations
flowise_url (empty — enables the flowise_flow profile once set), flowise_api_key (secret),
searxng_url (http://127.0.0.1:8890), hibp_api_key (secret).

## Confirmation (E3 / Rule 6)
require_for_verbs (send, delete, remove, pay, buy, order, purchase, post, publish, submit,
install, uninstall, shut down, restart, transfer, forward, empty, log in, sign in, kill) —
each match asks yes/no BEFORE running. confirm_t2_skills (true). repeat_guard_window_s (20).

## Privacy
redact_secrets_in_logs (true), redact_patterns (emails, PK phones, cards — extend freely),
remember_chats (true), allow_t3 (empty — T3 skills stay blocked unless listed).

## Observability
json_logs (true — data/logs/ishti.jsonl), log_level (INFO), trace_events (true),
log_file (empty = the default path).
