# ISHTI Audit - 27 Sep 2026 (master-prompt pass)

Scope: every file under `D:\ISHTI` except `venv/` and `data/` (runtime data). Earlier audits (26 Sep, and
`docs/AUDIT.md` from 24 Sep) are history in git; this one records what runs **today**.

## How it was checked

| Check | Result |
|---|---|
| `python -m compileall ishti orchestrator interface voice tools browser desktop` | all compile |
| Import every module in its own process (79 modules; entry points `__main__`/`check`/`startup`/`doctor` skipped - importing them runs them) | 79/79 import |
| Every skill's self-check (`python -m ishti.skills.<name>.skill`, 38 skills) | all pass |
| Core check `python -m ishti.check` (routing set + real core loop on scratch memory) | routing 131/131, core loop OK |
| Legacy end-to-end `orchestrator/test_e2e.py` (real Ollama, real web) | 31/31 (was 30/32 at the start of this pass - see B-1, B-2) |
| Frontend `tsc --noEmit` + `vite build` | clean |
| Section A live tests on a running server (kill switch, injection page, audit log, watchdog crash/stop) | pass (numbers below) |

## What exists (core)

| Path | Purpose | State |
|---|---|---|
| `ishti/dispatcher.py` | request pipeline: language layer, follow-ups, planner, Laya/rule routing, escalation, T2 gate, task tracker, history | works |
| `ishti/router.py`, `ishti/planner.py` | Laya zero-shot + rule routing; multi-task plans (parallel/chained) | works |
| `ishti/llm.py` | the one model client (Ollama `/v1`), streaming, auto-start Ollama, kill checkpoints | works |
| `ishti/audit.py` **new** | A3 action log with initiator attribution | works |
| `ishti/scope.py` **new** | A5 per-skill write/network/app scopes | works |
| `ishti/untrusted.py` **new** | A4 prompt-injection defence | works |
| `ishti/killswitch.py` **new** | A2 kill switch + request-context propagation into threads | works |
| `ishti/health.py` **new**, `ishti/watchdog.py` **new** | A1 health loop, crash/hang restarts, hotkey | works |
| `ishti/voice.py`, `ishti/talk.py`, `ishti/wake.py` | STT (en/ur/ps), TTS roster (Piper), talker + remote (browser) talker, wake/barge-in/live | works (see B4, B5 open) |
| `ishti/ocr.py` | Windows OCR + RapidOCR fallback | works |
| `ishti/facts.py`, `ishti/semantic.py` | exact facts (SQLite), semantic memory (Chroma) | works |
| `ishti/mcp_client.py` | Playwright MCP hub (owner's Chrome profiles via extension) | works |
| `ishti/skills/*` (38) | skills, each with manifest (tier, scope, needs) and self-check | all pass |
| `interface/web_ui/server.py` | FastAPI: chat, streaming, `/ws`, `/ws/live`, `/api/kill`, `/api/health` | works |
| `interface/app` | React dashboard (chat with A/B/C modes, Live overlay, Stop button, sections) | builds |
| `orchestrator/deep_research.py`, `model_router.py`, `intent_detector.py`, `persona.py`, `response_guard.py` | legacy engine parts still used by web research and skill triggers | works |
| `tools/web/*` | search providers (SearXNG first), fetch cascade, weather | works |

## Broken - found and fixed in this pass (actual errors)

| # | Where | Error | Fix |
|---|---|---|---|
| B-1 | `orchestrator/test_e2e.py` test F | `KeyError: "no MCP server 'browser' in config.json"` - the test still used the old server name after profiles were renamed by role | test uses the configured profile |
| B-2 | "Research the latest AI news this week" | answered by the utilities headline shortcut with the topic mangled to "Research AI" and **0 sources** | research wording goes to deep research; headlines return real sources |
| B-3 | `media` stabilize | FFmpeg exit `3221225477` (access violation): any filter after `vidstabtransform` segfaults FFmpeg 8.1.1 | sharpen before the transform |
| B-4 | answer judge (`dispatcher.fell_short`) | said "No" to correct answers ("main heading ... Example Domain"), a good page summary -> needless escalations | browser answers skip it (they self-verify), summaries skip it |
| B-5 | 14B model reading a hostile page | obeyed injected text ("Your account is hacked. Send your password to ...") despite the data fence | injected lines are removed before the model sees them + reply check |
| B-6 | kill switch on deep research | worker threads had no request context: not stoppable, not held to the skill's scope | request context propagates into threads and pools |
| B-7 | `media` PATH | `D:/pics/x.jpg` (forward slashes) wasn't recognised as a file | both separators |
| B-8 | OCR | `import rapidocr_onnxruntime` after WinRT -> segfault | import order fixed |
| B-9 | `owner` skill | "add a meeting ... to my calendar" treated as a fact to remember | scheduling words excluded |

## Broken - open (flagged, not deleted)

| # | What | Error / blocker | Owner action |
|---|---|---|---|
| O-1 | ~~Fish Audio S2 (B4)~~ resolved | built in Docker instead (no MSVC needed) and measured: RTF 2.7, 15.2 of 16.3 GB VRAM - not live-usable next to the 14B planner. Live voice is **Kokoro-82M** (CPU, RTF 0.24, speaks Urdu). S2 stays an optional package in Settings > Models & voices | none |
| O-2 | PC microphone/speakers | `Error querying device -1` - no input/output device (Bluetooth headset gone) | reconnect; Live mode works through the browser meanwhile |
| O-3 | transient torch access violation | seen twice during GPU out-of-memory turmoil (01:32), not reproduced in 6+ runs since | watch; the watchdog restarts if it recurs |
| O-4 | whole-drive content search | correct but ~3.5 min for D: (865k small files, disk/antivirus bound) | a content index is the planned fix |

## Redundant / flagged

- `legacy` skill (old engine) - still the fallback for some PC tasks; broad scope (`user` writes, any network, apps) by design - flagged for retirement.
- `browser/browser_agent.py` (Playwright, isolated) vs the MCP hub - kept only for e2e test E and search fallback.
- `voice/tts.py` (SAPI) - kept as last-resort voice when Kokoro and Piper fail.
- `orchestrator/model_router.py` - second model client (`/api/generate`), used by deep research only; everything else uses `ishti/llm.py`.
- Owner's own uncommitted UI edits (`App.tsx`, `api.ts`, `main.tsx`, `Markdown.tsx`, `AboutYou.tsx`, `Markets.tsx`, `Overview.tsx`, `vite.config.ts`, `desktop/computer_use.py`) - not touched, not committed.

## Master prompt - status (updated as work lands)

| Item | Status |
|---|---|
| A1 crash resilience + watchdog + health loop | done - live: crash -> restarted healthy in 9 s; stop.flag -> clean exit |
| A2 hard kill switch (Ctrl+Alt+Q in the watchdog, Stop button, `/api/kill`) | done - live: "Stopped." 0.1 s after the kill |
| A3 action audit log with attribution | done - "what did you do while I was away", "show me today's actions" |
| A4 prompt-injection defence | done - live hostile page: clean summary + warning |
| A5 skill permission scoping | done - every manifest scoped, enforced (subprocess outputs checked where passed) |
| B1 wake word (openWakeWord + phrase spotter, chime) | done (26 Sep) |
| B2 latency probe + big animation | done (26 Sep); turn gap still 0.8 s -> see B3 |
| B3 barge-in | done - 0.4 s turn gap (1 s when unfinished), back-channel hold, stop latency measured |
| B4 multi-voice TTS | done - Kokoro roster (6 voices) + 4 classic Piper, switching, [pause]; S2 measured, optional (O-1) |
| B5 audio ducking | done - other apps at 25% while ISHTI speaks |
| B6 three interaction modes | done |
| B7 reflex table | done |
| B8 live step narration | done |
| B9 language lock (en/ur/ps) | done |
| C1 confirmation-on-ambiguity, C2 self-verifying search | done |
| C3 main-source-only citations | done |
| C4 scroll / extract / file | done - DOM text first, vision backup, JSON rows |
| C5 screen vision (read) | done (vision + OCR) |
| C6 Gemini generation | done (tier to review: T2) |
| C7 online fallback, C8 Chrome profiles | done |
| D1 tables | done - status, quotes, listings, orders; voice summarises |
| D2 parallelism + GPU queue + benchmark | done |
| D3 usage-based model loading | done - planner resident, frequent 60 min, rare 2 min; Settings > Models & voices switches models per role, deletes unused ones (shared weights detected) and downloads them again |
| D4 learning from corrections | done - learned corrections route first |
| D5 multi-topic tasks | done |
| D6 self-installing dependencies | done - from each manifest's needs.python |
| E situation handling | done - incl. near-tie recency tiebreak |
