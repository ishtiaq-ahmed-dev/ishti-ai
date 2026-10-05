# Audit implementation plan

Base `e9d2559`; branch `mac/audit-20261005`. Started 2026-10-05, resumed 2026-10-06 (Asia/Karachi). Fixes precede features. Use existing skills, local models, stdlib/existing dependencies, disposable test stores and no real accounts. Preserve T2/T3 gates and `ishti-*` models. The baseline API contract is 2.0.0, already newer than the requested 1.0.0.

## Ordered repair batches

| Order / change ID | Audit IDs | Change | Validation | Status |
|---|---|---|---|---|
| 1 / M01 | S06 | Make discovery read-only; installation requires explicit approved call, blocked in test mode. | Fake installer never invoked by loader; explicit missing dependency result. | Planned |
| 2 / M02 | S05, S08 | Isolate vault tests/storage; fail closed on corrupt index/key and retain failed deletes. | Disposable vault, owner sentinel, corruption and failed-unlink tests. | Planned |
| 3 / M03 | S02 | Resolve filesystem scope targets; guard keyword and descriptor-relative mutations. | Symlink/keyword/dir_fd escape denial plus permitted writes. | Planned |
| 4 / M04 | S01 | Enforce tier, enabled state, scope/audit and test guards in ARCH2/specialists. | Fake T3/disabled never runs, scoped allowed run, T2/test denial. | Planned |
| 5 / M05 | R01 | Fail closed on VRAM governor errors, recheck recovery admission. | Mock GPU admission failure; no generation; guards released. | Planned |
| 6 / M06 | S03 | Confirm exact persistent memory/relationship deletions. | No mutation before yes; no/replay/ambiguity safeguards. | Planned |
| 7 / M07 | S04 | Bind vault purge to an expiring one-use exact-target approval; update existing UI. | Missing target, expiry/replay/changed state tests; TS/build. | Planned |
| 8 / M08 | R02, R08 | Reject unverified adapter promotion and invalid training prerequisites/base mapping. | Synthetic candidate dirs/HTTP spies; current adapter unchanged on rejection. | Planned |
| 9 / M09 | R03, R04 | Bind temporary lessons to originating session and retain exact applied-lesson attribution. | Two-session separation, legacy-unbound denial, snapshot-only rating. | Planned |
| 10 / M10 | R05 | Validate model-produced planner envelopes and step fields. | Malformed JSON shapes/types produce safe fallback; existing valid plans work. | Planned |
| 11 / M11 | R06 | Serialize cached router initialization and preserve offline policy. | Concurrent constructor tests; no download fallback/env mutation. | Planned |
| 12 / M12 | R09, P01 | Unicode-safe corrections and deterministic conflict behavior. | English/Urdu/Pashto/blank/legacy/tie regression cases. | Planned |
| 13 / M13 | S07 | Fence stored client content at model consumption. | Captured hostile context fenced; same-client-only rows retained. | Planned |
| 14 / M14 | R07, research R03 | Bound WS sends and cancel queued MCP work after timeout. | Slow synthetic socket and queued fake tool tests; no auto-retry of mutation. | Planned |

Each logical repair receives a small conventional commit, focused regressions and Ruff. Run new/changed safety tests three consecutive times as required by AGENTS. Typecheck/build frontend when applicable, then run the safely isolated default suite. Do not weaken old tests or edit configs/models for a green result. PC-only observations are marked NEEDS PC VERIFY.

## Features by value/effort (after repair batches)

| Priority | Existing subsystem improvement | Value / effort | Acceptance boundary | Status |
|---|---|---|---|---|
| F01 | Extend offline routing evaluation with versioned correction replay and a promotion report. | High / M | Dataset hash, per-case route, conflicts/disabled skills, baseline/candidate delta; scoring never runs skills or writes production stores. | Planned |
| F02 | Correlated local request/step traces and bounded export. | High / S–M | Redacted metadata and monotonic durations through all thread entry points. | Deferred pending end-to-end PC trace fixture |
| F03 | Task-wide progress/loop detection in existing ARCH2/browser executors. | High / M | Stops repeated unchanged failures without breaking legitimate polling. | Deferred pending real tool/task acceptance fixtures |
| F04 | Durable scheduler checkpoints/idempotency and restart recovery. | High / L | Reads can recover; T2 mutations require fresh approval and never automatically resend. | NEEDS PC VERIFY: restart/transport outcomes |
| F05 | Real OS sandbox for generated code and remaining media edits. | High / L | Restricted Windows/container roots/network; no owner profiles mounted. | NEEDS PC VERIFY: isolation backend |
| F06 | Canonical CRM memory namespace/migration and progressive document retrieval. | High / L | Reviewed legacy-row ownership and full two-client ingest/search/consolidation/export matrix. | Deferred: safe migration fixture/spec missing |
| F07 | Independent generated-skill acceptance fixtures and staged reflected-policy evaluation. | High / M | Owner corrections retained; inferred code/policies remain disabled until eval and owner review. | Deferred: actual artifact-serving validation missing |

Deferred work is explicit backlog, not a claim of completion. Research comparisons and sources are in RESEARCH.md. Missing prerequisites are carried into NEEDS.md; no hosted subscription or new heavyweight resident model is introduced.

## Handoff requirements

MAC_CHANGES.md lists every commit and changed file, Mac evidence, exact Windows PowerShell commands, expected result, risks and rollback commands. Do not merge to main. Push only `mac/audit-20261005`; confirm remote HEAD matches local HEAD. Audit/research paths explicitly requested by the owner override the general AGENTS markdown location rule.
