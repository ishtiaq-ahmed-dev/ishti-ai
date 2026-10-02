# ISHTI session schema v1 (`ishti.session.v1`)

WP-ECC, matrix row **ECC-09** (from ECC's harness-neutral `ecc.session.v1` contract,
`vendor/ecc/docs/architecture/session-adapter-contract.md`). One envelope for monitoring
whatever runs a session — ISHTI's own turns and skills, a Claude Code / ECC harness, or a
sub-agent — so the overlay pills (`desktop/overlay/pills.py`), the dashboard and any future
adapter read the same fields.

Implementation: [`ishti/session_schema.py`](../ishti/session_schema.py) — `make()`, `validate()`,
`conforms()`, `emit()`. The overlay hook envelope (`ishti/hooks_relay.py`, kind `hook`) carries
the same `agent`/lifecycle vocabulary over the named pipe; this schema is the bus/session-level
view of the same states.

## Envelope

```json
{
  "v": 1,
  "session_id": "s-20261002-1",
  "agent": "claude-cc",
  "state": "working",
  "ts": 1759390000.123,
  "parent": "ishti",
  "label": "Bash",
  "detail": "pytest -q",
  "tool": "Bash",
  "tier": "T1",
  "source": "ishti"
}
```

| Field | Required | Type | Rule |
|---|---|---|---|
| `v` | yes | int | must be `1` |
| `session_id` | yes | str | non-empty, ≤ 120 chars |
| `agent` | yes | str | `^[a-z0-9-]{1,24}$`; invalid producers fall back to `ishti` |
| `state` | yes | str | one of `idle`, `thinking`, `working`, `finished`, `error`, `ratelimit`, `question` |
| `ts` | yes | number | epoch seconds (not a bool) |
| `parent` | no | str or null | parent session/agent id for sub-agents |
| `label` | no | str | ≤ 120 chars — what is happening now (tool name, step) |
| `detail` | no | str | ≤ 500 chars — bounded, never a secret |
| `tool` | no | str | ≤ 60 chars |
| `tier` | no | str | `T0`–`T3` when known (advisory; gates stay where they live) |
| `source` | no | str | `ishti` / `claude-cc` / harness name |

## Producer / consumer contract

* Producers call `make(...)` — total, never raises, coerces invalid values (bad agent → `ishti`,
  bad state → `idle`) and bounds strings. `emit(envelope)` publishes on `ishti.events` with
  bus type `"session"`; non-conforming envelopes are dropped (`None`), never raised.
* Consumers call `validate(envelope)` and get a list of problems (`[]` = conforming) — strict,
  total, and safe on junk input (`None` → `["event must be an object"]`).
* The lifecycle states intentionally match the overlay pills' `EVENT_STATE` map, so
  `SessionStart → idle`, `UserPromptSubmit → thinking`, `PreToolUse/PostToolUse → working`,
  `Stop → finished`, failures → `error`, `Notification → question`, `RateLimit → ratelimit`.
* Security: everything here is metadata. An envelope never carries tool arguments, page text
  or secrets; `detail` is bounded and callers redact before filling it (same rule as the
  overlay log scrubber, `desktop/overlay/log.py`).

## Test contract

`tests/test_session_schema.py` proves: every state conforms; junk/envelopes with missing or
mis-typed fields produce problems; `make()` coerces instead of raising; `emit()` reaches a bus
subscriber as type `session`; bounded lengths; and that this document names every field the
validator enforces.
