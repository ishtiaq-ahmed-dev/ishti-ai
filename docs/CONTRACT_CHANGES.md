# CONTRACT v2 changes

| Date | Version | Change |
|---|---|---|
| 2026-10-06 | 2.0.0 -> 2.1.0 | Added §14 Contract v2: chats/messages/runs, run event log with global `seq` + `since_seq` replay on `/ws/events`, feedback, profiles, connector registry, agents, learning/proposals, `GET /api/prompt/layers`. `/ws/events` gains optional `since_seq` query param (absent = legacy behavior unchanged). |
