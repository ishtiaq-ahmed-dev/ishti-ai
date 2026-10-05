# ISHTI reliability and WhatsApp feature repair

This earlier checkpoint is superseded by [the 2026-10-05 audit report](docs/audit/FINAL_REPORT.md).

Date: 2026-10-04. Verdict: **NOT READY for the complete requested WhatsApp + CRM feature set.** Confirmed fixes and local checks are complete for this pass; remaining requirements are listed below instead of being described as finished.

## User-reported issue fixed

The recent saved conversation explicitly stated that Irfan is another brother and Akbar Khan is the grandfather, then confirmed ISHTI's restatement. The authoritative relationship graph did not contain either relation. I added only these two additive facts using explicit-user provenance. Verification afterward showed:

- Irfan — brother, `explicit_user`, confidence 1.0.
- Akbar Khan — grandfather, `explicit_user`, confidence 1.0.
- Existing brother edges (Bilal and Ahmed) were left in place.

The application also now reloads saved user/assistant turns when a saved chat is resumed, normalizes persisted assistant role names, bounds the history, skips unsafe assistant turns and avoids persisted-history reads while incognito is active. Chat answers use bounded earlier-turn context and keep the conversation-state summary pinned. Relationship replies report one or many siblings with matching grammar, and explicit compound declarations only claim successful facts after each graph write succeeds.

## Other fixes in this pass

- Prompt instructions now prioritize the owner's full request, relevant context, exact names/data, evidence-backed claims, tool-use correctness, concise writing and honest partial/failure reporting. Arch2 no longer treats malformed validator output as proof of success and has more room for multi-step tool requests.
- Chat UI refreshes saved sessions after cross-channel task completion and after WebSocket queue overflow. WebSocket writes are serialized. Slow fetches from older selected chats cannot replace the current chat.
- Chat has an attachment picker, drag/drop and clipboard paste using the existing local upload API. It handles up to five files / 50 MB total and surfaces upload errors; file signatures are checked, SVG is excluded, and OOXML archive member count, size, compression ratio, traversal and macro entries are bounded/rejected.
- WhatsApp transcript questions are answered from structured JSON evidence; message text is explicitly untrusted. The parser accepts 24-hour timestamps. Unknown and ambiguous relative references no longer silently fall back to a prior WhatsApp chat. Fake group action/member responses are removed. Text sends require a new exact outgoing-row match against a before-action snapshot; file preview closure is no longer treated as delivery proof, and document delivery requires a new outgoing row containing its filename. Edit, delete and react requests now state that no action occurred until target and result can be verified.

## Why earlier responses were poor or context-free

1. Resumed sessions were visible in the UI but dispatcher runtime history started empty, so the model saw no prior turns.
2. Even with a live history, chat passed only the latest 12 messages and did not use the existing context assembler/state summary; earlier details were silently dropped on long chats.
3. Family facts were split between a relationship graph and generic inferred memory. A compound sentence could be acknowledged while the authoritative relation was never committed, and the write path did not mark explicit statements with explicit-user provenance.
4. WhatsApp direct-read questions returned a transcript instead of answering the question. Some group operations returned fabricated success text, and broad post-action checks could mistake a normal idle send button for proof of success.
5. The prompt stack had inconsistent length rules and weak completion wording, which contributed to dumped snippets, missing parts, overconfident claims and empty/fragment responses. Prompt changes now distinguish actual evidence and completion from tool availability/attempts.

## Verification

Focused memory-context and WhatsApp safety tests passed (11 cases), Python syntax compilation passed, TypeScript typechecking passed, and focused lint completed with one warning in the existing chat queue effect. Full Python/FastAPI tests are blocked because the project's Python 3.11 virtual environment points to a missing interpreter and the available Python 3.12 cannot load the CPython 3.11 `pydantic_core` binary. No live WhatsApp actions, OCR/STT accuracy claims, or latency metrics were made. See `TEST_RESULTS.md`.

## Open work before production readiness

- Run the whole project suite under its matching Python runtime and add end-to-end restart/context tests.
- Connect the dedicated self-chat/test destination before any live WhatsApp smoke checks. Personal account automation still carries platform/account risk.
- Implement selector registry, startup drift self-test, idempotent action IDs, exact recipient/content approvals, fresh target selectors, postcondition evidence, and per-action audit evidence.
- Complete or formally block message forwarding/replies, edit/delete/react/star/pin/archive/mute, group actions, scheduled send, media understanding, native voice-note delivery, locked/view-once/disappearing constraints and Business-only capabilities.
- Build a local CRM with source message pointers, proposed-vs-confirmed extracted data, contact isolation, exports/delete-all and zero cross-client leakage tests.
- Add attachment thumbnails, true progress, server-side aggregate limits for non-UI callers, quarantine/sandbox scanning, retention/delete controls and an intent resolver. Existing chat attach support currently sends a fixed per-file instruction to existing skills.
- Decide and implement encrypted retention for local conversation/media/CRM data, then verify Windows lifecycle/watchdog/VRAM/remote-channel behavior.

Rollback: revert only this pass's code hunks and new report/test files. The two family edges are additive owner data; if the owner requests their removal, remove those exact two graph edges through the relationship-memory API. Do not reset or clean unrelated existing worktree changes.
