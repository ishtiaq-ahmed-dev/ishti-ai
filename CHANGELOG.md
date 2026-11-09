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

## [0.2.0] - 2026-11-09

A hardening release: browser automation is gone, WhatsApp moves to a proper HTTP API, cloud models are first-class
with automatic local failover, and every turn makes fewer model calls.

### Added

- **AgentRouter provider** (`agentrouter`, OpenAI-compatible) in the provider list, the first-run card and
  `config.json`. API keys live only in the Windows Credential Manager or an environment variable.
- **Model failover with a circuit breaker** (`ishti/llm.py`). When the cloud provider refuses (bad key, allow-listed
  client, quota, network, Internet switch off), a light local model (`gemma3:4b`) answers, but only if Ollama is already
  running with it pulled. A refused provider is skipped for 20 s to 10 min instead of being called on every request.
  Provider failures reach the owner as one actionable sentence (`ModelUnavailable`), never a stack trace.
- **WhatsApp over WAHA** (`ishti/wa_bridge.py`). ISHTI runs WAHA (GOWS engine, image pinned by digest) in its own
  Docker container on `127.0.0.1` only. The API key is kept in Credential Manager and the container only gets its
  SHA-512 hash. Sends are idempotent per action id, media is read in a sandboxed worker, and the container stops
  with ISHTI.
- **Hard boundary for mass deletion:** wiping a drive, the whole PC or system folders is refused and audited.
- `tools/web/fetch.safe_get`: SSRF-guarded HTTP (public hosts only, every redirect re-checked, size cap).
- `SECURITY.md`, this changelog entry, and offline tests for WAHA, SSRF, provider failover and WhatsApp safety.

### Changed

- **Email** runs on the Gmail API: inbox, read, search, order tracking, invoices, send and forward, each send
  behind a yes. **Calendar** adds events through the Google Calendar API, or gives a prefilled link.
- **Extract** and **web_fetch** read pages over plain, SSRF-guarded HTTP. Web search verdicts and site-page checks
  take **one** model call instead of one per page.
- **System prompt** is ordered for provider prompt caching: the stable prefix comes first and the clock last. The
  measured self-model block (~500 tokens) is sent only for questions about this PC or ISHTI.
- Background fact extractors skip questions and task commands unless a memory cue is present. Research synthesis
  and query generation go through `ishti.llm` (the owner's provider), not a hard-wired local Ollama call.
- Idle-aware background work: the VRAM sampler polls every 5 s when idle (1 s during GPU jobs), reminders sleep
  until the next one is due, clipboard history checks the clipboard sequence number, and UI polls pause in
  hidden tabs.

### Removed

- All Playwright and browser automation: the browser skill, `browser_agent`, `mcp_client`, Obscura, and the
  `gemini`, `travel`, `contacts` and `online_fallback` skills, which only worked by clicking through websites.
- The Node/Baileys WhatsApp bridge and its installer step.

### Security

- The terminal skill asks before any command that can run code (`python -c`, `node -e`, `npm run`, `pytest`,
  git options that write or execute). Only a short read-only list runs without approval.
- Skill creation asks **before** model-written code is generated or run; "learn how to X" no longer builds a skill.
- WhatsApp control numbers must match on full international digits. A 10-digit suffix match let a number in another
  country through.
- Telegram pairing codes are burned after 5 wrong guesses. The login lockout uses the address the tunnel appended,
  not a client-supplied `X-Forwarded-For`. ngrok runs with `--inspect=false`.
- The API token file is owner-only (protected DACL). The persona no longer hardcodes the owner's name or hardware.
  Real phone numbers were removed from tests. `owner_profile.yaml`, `reports/`, `metrics/` and `audit/` are untracked.
- The email skill no longer returns invented invoices or test results.
- Tests no longer read the owner's saved provider choice or the OS credential store.
- **OWASP LLM Top 10 (2025) / Agentic Top 10 (2026) audit fixes:**
  - running code from the `dev` skill asks first, and remote channels can never trigger code execution;
  - a T1 workflow step fed another step's output (web, file or message text) asks first;
  - standing automations and scheduled tasks are approved at creation and rate-limited;
  - third-party skills are installed as T2 with no network scope, after package vetting;
  - memory is extracted only from the owner's words, and a model-proposed "forget" needs the owner's ask;
  - remote channels classify the raw text, and forwarded WhatsApp messages are treated as data;
  - webtools downloads go through the SSRF guard;
  - secrets are redacted by pattern in the audit and route logs;
  - approvals expire after 10 minutes and fail closed;
  - prompt-extraction requests, weapon instructions, money transfers and mass deletion are refused before any
    model call.
- **Cost and tokens:**
  - the decision prompt keeps an identical cacheable opening block for cloud providers;
  - the duplicate conversation-state block is gone;
  - Anthropic JSON mode no longer fails;
  - a malformed request no longer trips failover;
  - cached tokens are recorded per call;
  - memory extraction can run on the local light model (`api.background_local`).

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
