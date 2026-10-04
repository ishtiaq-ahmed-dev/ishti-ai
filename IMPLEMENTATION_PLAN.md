# ISHTI implementation plan

Created after the read-only audit and research, before feature code changes, on 2026-10-04.

## Scope

Fix the confirmed chat-history hydration, compound relationship persistence, false WhatsApp success, chat refresh and
stale-session races, and safely connect existing uploads to the chat. Build mock-backed tests and documentation for
the larger WhatsApp/CRM feature request. Preserve the app's existing T0-T3 gates, incognito behavior, private-person
scope, audit hooks, kill switch, local inference and VRAM rules. No live account is used during automated tests.

No implementation can make an unsupported or unavailable WhatsApp Web capability “perfect.” Unsupported actions
must be reported as unsupported rather than simulated. Personal-account web automation is opt-in and carries account
and terms risk.

## Work packages

| WP | Owner | Files / focus | Dependencies | Tests and acceptance | Risk / rollback |
|---|---|---|---|---|---|
| A: conversation restore | primary | `ishti/dispatcher.py`, `orchestrator/memory/conversation.py`, context assembly | none | saved turns hydrate once; `ishti` role maps to `assistant`; no cross-session history; incognito and safe-history rules hold | duplicate turns; revert hydration helper |
| B: memory correctness | primary | `ishti/skills/relationships/skill.py`, tests, graph helpers if required | A for cross-session check | multi-fact statements save each recognized relation before success reply; fresh session retrieves; ambiguous kinship is surfaced; no personal data test fixtures | accidental duplicate/edge mutation; only additive graph facts and rollback patch |
| C: arch2 and dispatcher correctness | primary | `ishti/arch2/pipeline.py`, `ishti/dispatcher.py`, focused tests | A | shortlist includes deterministic match plus core/recovery skills; failed validator is unverified; tests cover >3 required actions ending with explicit pending work | changes to routing/gates; maintain existing T2 boundary |
| D: UI real-time and races | primary | `interface/app/src/App.tsx`, `interface/app/src/sections/Chat.tsx`, `interface/web_ui/server.py`, tests | A | completion events trigger refresh; one ordered WS writer or safe resync; stale fetch cannot replace latest selected session | event storms; refresh only on relevant session events and debounce |
| E: upload pipeline and UI | primary | existing files API, `Chat.tsx`, chat components, tests | none | attach/drop/paste or attach control uses existing local endpoint; files are validated by content and bounded; upload cards have progress/error/remove; uploaded text remains untrusted | parser attack; strict formats/limits, never execute |
| F: WhatsApp reliable core and matrix | primary | `ishti/skills/whatsapp/skill.py`, manifests, tests, mock page, selector registry/docs | A/B for contact resolution | remove fake-success branches; real supported read/send/reply paths report truthful outcomes; safe mock tests cover exact target/approval/action result; all unavailable capabilities documented | terms/account risk; disable live writes unless owner enables and confirms test destination |
| G: CRM and attachment intent | primary | reuse `clients`, `relationships`, `chat_digest`, files/chat APIs; architecture docs | audit of current client isolation | record existing support and implement only clearly isolated local enhancements; facts proposed until owner confirms; no CRM cross-client leakage | duplicating stores; avoid new CRM database until existing clients contract is understood |
| H: verification/review | primary | targeted test files and requested reports | A-G | focused regression suite, static checks, mock QA; record pass/fail/blocked evidence and known gaps | don't run against owner data; test stores under temporary directories |

## Proposed order

1. Phase 0 audit (complete): `WHATSAPP_AUDIT.md`.
2. Phase 1 research (complete): `RESEARCH.md`.
3. Phase 2 plan (this document; complete before code).
4. Implement A-C first to fix missing conversation context and family-memory correctness.
5. Implement D-E for UI synchronization and the requested attachment flow.
6. Implement F-G only to the extent supported by current local design and tests; record unsupported items in the feature matrix.
7. Run focused tests and mock-only QA, then review changes and publish `TEST_RESULTS.md`, `REVIEW_SIGNOFF.md`, `NEEDS.md`, and `FINAL_REPORT.md`.

## Explicitly blocked / not authorized by current inputs

- Live WhatsApp actions/tests: owner stated a dedicated test account can be provided, but no destination/account details
  or connected authenticated tab were supplied. Do not send, edit, delete, or read real-person chats.
- Personal-account WhatsApp automation compliance cannot be guaranteed by code. The app must disclose the current
  official terms risk and default external actions off.
- Business-only labels/catalog/quick replies are not available because the owner selected Personal account.
- OCR/voice-note/platform feature claims need synthetic accuracy and live UI evidence before they can be called verified.

## Rollback

Changes are limited to source, tests, and documentation under this repository. Revert only the new patch hunks/files;
do not reset the user's working tree or touch runtime chat databases. No schema migration is planned. Temporary test
databases and mock page data must be isolated under pytest temp directories or `_work/`.

## Execution status

| WP | Status | Notes |
|---|---|---|
| A: conversation restore | Implemented; partial verification | Role hydration, bounds, unsafe-history filtering and incognito logic added. Focused isolated checks pass; full dispatcher import test is blocked by the Python runtime mismatch. |
| B: memory correctness | Implemented for explicit compound relationships | Exact reported facts were added to the owner's graph. Existing facts are retained. Scratch graph tests cover compound writes/provenance and retrieval grammar. |
| C: arch2 and dispatcher correctness | Implemented; partial verification | Deterministic shortlist and validator fallback fixed; >3-action plan behavior has not been end-to-end measured. |
| D: UI real-time and races | Implemented; source/typecheck only | Completion events, socket serialization/resync and selection generation guards are in place. No live multi-window run yet. |
| E: upload pipeline and UI | Partial | Selection/drop/paste, per-composer limits and signature/archive checks implemented; no HTTP endpoint suite due Python mismatch, and thumbnails/progress/sandbox cleanup are open. |
| F: WhatsApp core and matrix | Partial, not live-verified | False group success removed; transcript safety and verification tightened. Several actions are paused or blocked; no selector registry or authenticated test tab. |
| G: local CRM | Not implemented | Existing client/relationship stores were audited; source-linked CRM proposal/confirmation and isolation acceptance remain open. |
| H: verification/review | Partial | 11 offline behavior tests, syntax, TypeScript and lint checks run; full suite, 3 consecutive runs, media accuracy and live smoke tests are blocked/not run. |
