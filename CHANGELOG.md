# Changelog

All notable changes to ISHTI are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Until `0.1.0` the project had no version number at all: `ishti/version.py` was added on 2026-10-16,
and this file is seeded from the commit history rather than from release notes that were never
written. Entries before `0.1.0` therefore describe what is **in the tree at that revision**, not a
sequence of tagged releases. One consequence is stated plainly because it matters to a reader:
`docs/ACCURACY.md` describes the measured state, and several numbers in it were produced against
revisions that no longer exist.

## [0.1.0] - 2026-10-16

The first numbered version: the same code that was already in the repository, now stamped so that a
measurement can name the revision it came from. No git tag carries this number yet, so `0.1.0` means
the source hash `ishti/version.py` reports for the tree, not a release anyone downloaded.

### Added

- **One-decision brain** (`ishti/brain.py`). Every request takes one path — deterministic guards,
  then fixed routing rules with no model call, then **at most one** model call that either calls a
  skill or answers directly. The skill's own reply is the answer: no validation call and no rewrite
  call follows. Multi-part requests become a journaled workflow (`ishti/workflows.py`) that survives
  a restart and never reconstructs an approval from a previous run.
- **`memstore`** — semantic, episodic and procedural memory with confidence, supersession and
  attribution (`ishti/memstore/`), replacing the earlier `arch2` store.
- **Interoperability** — an opt-in stdio MCP server that exposes the skill catalogue read-only and
  runs only explicitly named, working T0 skills (`ishti/mcp_server.py`), and portable `SKILL.md`
  instruction guides following the Agent Skills spec (`ishti/instruction_skills.py`).
- **Business Desk** — an opt-in, local workspace over the owner's own invoice, lead and sales CSV
  data, with outcome receipts feeding the learning loop (`ishti/business/`).
- **Version identity** — `ishti/version.py` (single source of truth for `__version__` and a sha256
  source hash), `python -m ishti --version`, and `GET /api/version`.
- **Privacy report tooling** — `tools/privacy_report.py` and `docs/PRIVACY_REPORT.md`, which state
  every network destination reachable from the codebase for a given revision, and prove the
  denylist and loopback-only bind rather than asserting them.
- **Evidence tooling** — `tools/evidence.py` produces `reports/evidence.json` for one revision:
  the curated offline tests, the five offline eval suites, and the per-skill suites; every metric is
  either measured with a numerator and denominator or explicitly `unmeasured` with a reason. The
  human-readable form is the tracked `docs/EVIDENCE.md`, so a published number survives a `git clean`.
  `tests/task_suite/README.md` documents how to run the 300-task suite and what it does not grade.
- **CI** — a curated offline core on every push and pull request, and a nightly full offline suite.

### Changed

- Screen control is now `ui_read` (list the named controls of a window) and `ui_act` (act on one
  exact control after approval). ISHTI does not capture the desktop: there is no screenshot path.

### Removed

- The `planner`, `router`, `decision_loop`, `arch2`, `agents`/`agent_bus`, `automation`,
  `flowise_client` and `core/architectures` modules, and the `desktop_control`, `screen_control`
  and `screen_understand` skills, together with their tests. The one-decision brain replaced them.
- Internal working notes (`AGENTS.md`, `NEEDS.md`, `PROGRESS.md`, `MODEL_DECISIONS.md`) and the
  stale `docs/audit/` and `docs/build/` dumps. `docs/ACCURACY.md` keeps the numbers those dumps
  contained, in a file that is tracked.

### Security

- The egress guard (`ishti/privacy.py`) blocks any connection to a non-loopback host unless the
  owner asked for it, and refuses telemetry and analytics endpoints even inside a network-scoped
  skill. Every attempt is recorded in `data/outbound.log` as host, port, who and why — never a
  request body, query or header.
- The dashboard binds `127.0.0.1` only (`ishti/lifecycle.py`).
