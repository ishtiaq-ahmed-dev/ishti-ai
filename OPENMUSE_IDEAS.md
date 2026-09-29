# OPENMUSE_IDEAS.md - what ISHTI should take from OpenMuse (2026-09-30)

Source studied: github.com/CopilotKit/openmuse (MIT, alpha). Verdict on adopting it as a dependency:
**NO-GO as a dependency** - every mode requires a CopilotKit Intelligence server key (cloud
persistence for threads), and Gmail/Calendar need Google OAuth (cloud). That breaks ISHTI's
"100% local at runtime" rule. But several of its IDEAS are worth reimplementing locally, in ISHTI's
own style, with attribution. License/attribution note: MIT - copying ideas is fine; if any code is
copied verbatim (none is proposed), keep the MIT notice.

## Feature-by-feature scoring

| OpenMuse feature | Verdict | Why / how ISHTI would do it locally |
|---|---|---|
| Durable task engine (SQL leases, crash recovery) | **ADOPT idea** | ISHTI already persists in-flight requests (`INFLIGHT_FILE`) and restarts cleanly; add a `task_leases` table: owner + heartbeat + lease expiry so a crashed task's work is claimable after restart. Fits `taskmgr.py` + `agents.py`. |
| Visible follow-up queue | **ADOPT idea** | A dashboard panel listing everything ISHTI owes the owner (pending questions, T2 confirmations waiting, failed-and-retryable). ISHTI has the data (pending gates, taskmgr, NEEDS log) but no single view. |
| Approvals + saved receipts | **ADAPT** | The T2 gate already asks; add a persistent receipt: who approved, when, the exact action, the outcome hash - appended to `audit.db` (it already records confirmations; make receipts immutable + viewable). |
| "Ideas" suggestions with source evidence | **ADAPT** | ISHTI's learning dashboard suggests lessons; extend with an "ideas" card: observations from patterns (e.g. "you ask for the same export every Monday - automate it?"), each with the source interactions cited. Owner confirms before anything is created. |
| Goals/milestones + recurring page monitors | **ADAPT** | Recurring page monitor (price threshold, text appears, change detection, dedup alerts, failure backoff) is genuinely useful and local: implement in `ishti/skills/webtools/` as a scheduled T0 reader with net scope; alerts land in the follow-up queue. Goals/milestones duplicate `goaltrack`/`okr` - skip. |
| Document workflow (attachment -> PDF -> fill form -> review -> receipt) | **ADAPT** | ISHTI has docs/pdftables/fileops pieces but no pipeline; wire a review-first flow where every fill is shown before applied (T2), receipts in audit. Higher effort - schedule after monitors. |
| Finance CSV -> spending summary | **SKIP (exists)** | `budget`/`ledger`/`networth` already cover local money summaries. |
| Review-before-send email/calendar | **SKIP (exists, stricter)** | ISHTI's T2 gate is already review-before-send; email drafts never auto-send. Adding Google OAuth is banned (cloud). |
| AG-UI event protocol | **ADAPT (protocol study)** | Open protocol for streaming agent events; compare with ISHTI's `/ws` + `/ws/events` (boot stages, shutdown_progress, model_switch, show_window). ISHTI's contract already covers its needs; borrow the idea of a documented, versioned event envelope (seq numbers already exist) if the UI grows more event types. |
| CopilotKit runtime / cloud threads | **SKIP - conflicts** | Cloud key required in every mode; violates local-first. |
| Docker Linux computer | **SKIP** | ISHTI controls the real Windows PC via T2-gated skills; a sandbox VM duplicates `test_sandbox` needs at high complexity. |
| PGlite/PostgreSQL storage | **SKIP (exists)** | SQLite + Chroma already serve memory; Postgres adds ops burden for no local gain. |

## Top 3 to build (order, with attribution)

1. **Follow-up queue view** (small, high value, pure local UI over existing state).
2. **Task leases + crash-recovery claims** (hardens multitasking; builds on INFLIGHT).
3. **Recurring page monitors with dedup + backoff** (T0 reader, alerts to the queue).

Attribution: implement as ISHTI's own modules; add one line in README ("durable-task/monitor design
inspired by OpenMuse (MIT)") when items 1-3 land. No CopilotKit code or services.
