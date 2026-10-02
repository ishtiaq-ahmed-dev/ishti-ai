# SAFETY: tiers, confirmation gates, kill switch, audit, network scope

Verified against the code on 2026-10-02 (`ishti/audit.py`, `scope.py`, `killswitch.py`, `untrusted.py`,
`test_mode.py`, `mic_control.py`, `channels/`, `dispatcher.py`, skill manifests). Numbers are labelled MEASURED
(counted or read by me today) or SOURCED (from a named file I did not re-run). I did not run any of the safety
tests for this document; their last recorded results are cited where relevant.

Related: [ARCHITECTURE.md](ARCHITECTURE.md), [SKILLS.md](SKILLS.md), [API_CONTRACT.md](API_CONTRACT.md) section 11
(remote channels).

## 1. The model in one table

| Mechanism | What it decides | Where it lives |
|---|---|---|
| **Tier** (T0-T3 in each manifest) | Whether a skill's action needs the owner's yes, or is refused | `manifest.json` `tier`; checked in `dispatcher._run` |
| **Scope** (`write`, `net`, `apps` in each manifest) | What a skill can touch at all, whatever its tier | `ishti/scope.py` |
| **Confirmation gate** | The yes/no question before a T2 action runs | `ishti/skills/__init__.py::ask_first`, answered in `dispatcher._handle` |
| **Audit trail** | A record of every action, and who initiated it | `ishti/audit.py`, `data/audit.db` |
| **Kill switch** | Stops everything now, no confirmation | `ishti/killswitch.py` |
| **Untrusted-text defence** | Treats web pages, files, emails, messages as data, never instructions | `ishti/untrusted.py` |
| **Test mode** | Blocks every T2 side effect during automated tests | `ishti/test_mode.py` (`ISHTI_TEST_MODE=1`) |
| **Remote gate** | What a message from a phone may do | `ishti/channels/` |

## 2. Safety tiers

Definition used by the code and by the self-description in `ishti/selfmodel.py`: **T0** read-only, **T1** reversible
(typically local writes inside the skill's scope), **T2** needs the owner's explicit yes, **T3** refused.

Counts in `ishti/skills/*/manifest.json` today (MEASURED): **T0 68, T1 41, T2 15, T3 0**.

- **T0 / T1** run without a question. Their blast radius is bounded by their scope (section 4).
- **T2** skills: `browser`, `contacts`, `email`, `gemini`, `gitops`, `online_fallback`, `screen_control`, `security`,
  `sendlater`, `skill_creator`, `skillsmith`, `terminal`, `trading`, `whatsapp`, `workflows`. The yes/no question is
  raised by the skill through `ask_first` (it stores a pending action; the reply ends with "(yes/no)"). The
  dispatcher's own T2 handling is: the pending question is resolved before anything is routed; the same sensitive
  request twice within 20 seconds triggers a "you asked for exactly this a moment ago" question; and under test
  mode a T2 skill does not execute at all. I did not audit each of the 15 T2 skills to confirm every side-effecting
  path calls `ask_first`; the per-skill suites assert that the tier is legal and test T2 gates through the real
  dispatcher in test mode (`tests/README.md`).
- **T3**: the dispatcher refuses a T3 skill unless `allow_t3` in `ishti/config.json` maps its name to a truthy value
  (no `allow_t3` key exists in the shipped config, and no skill has tier T3). Note a mismatch to fix: the settings
  schema declares `allow_t3` as a list of names, while the dispatcher reads it as a name-to-value mapping. A list
  would raise at `.get`, so use the mapping form until one of them is changed.
- `skill_creator` never lowers a tier, gives T2 to anything with side effects, and refuses T3-class designs outright
  (`ishti/skills/skill_creator/design.py`).
- Trading is **paper only**: the `trading` skill (T2) keeps a simulated ledger. A request to connect a real or live
  broker is refused in code and never sent to a model. Real trading would be a T3 design that needs the owner's
  approval first (`md files/NEEDS.md` #18).

## 3. What always asks first

A gate is a stored pending action. It runs only when the owner's next message is an explicit yes (`yes`, `y`, `yeah`,
`go ahead`, `do it`, `ok`, `confirm`, `sure`, `proceed`, and a few hand-off words). `no`, `cancel`, `stop` and the like
cancel it ("Cancelled - nothing was done."). Any other message drops the question and is routed normally. The kill
switch also drops pending questions. The gate always runs on the owner's own message, not on text from a page or file.

Actions the T2 skills describe as gated in their manifests (read on 2026-10-02): sending email or WhatsApp messages and
scheduled sends (`email`, `whatsapp`, `sendlater`); browser clicks that buy, send, submit, post or delete (`browser`);
real mouse and keyboard control (`screen_control`); running terminal commands, in a restricted subprocess with an
allow-list (`terminal`); state-changing git commands - add, commit, push, pull, checkout, stash, merge, reset, clean
(`gitops`); merging contacts (`contacts`); uploading to a third-party online tool (`online_fallback`); generating media in
the owner's signed-in Gemini session (`gemini`); running saved webhooks (`workflows`); encryption and the credential
vault (`security`); and installing or generating skills (`skillsmith`, `skill_creator`).

Under `ISHTI_TEST_MODE=1` the side effect is blocked *after* the yes and recorded as
`TEST-MODE BLOCK <tier> <skill>: <gate>`, so the whole real gate path is exercised without any real effect.

## 4. Scope: what a skill can touch

Each manifest declares `scope` (read from the manifest at run time and enforced by `ishti/scope.py` while a skill
runs, identified through `audit.context`):

- `write`: where it may create, change, delete or move files. `data` = ISHTI's `data/` folder; `user` = the owner's
  home folder and non-system drives; `home:<sub>` = one folder in the home; or an explicit path. Always writable:
  `data/`, the temp folder, `orchestrator/memory`, a few tool caches.
- `net`: which internet domains the skill's Python code may call (`[]` none, a domain list, or `["*"]`). Loopback
  (Ollama, SearXNG, the local server) is always allowed.
- `apps`: whether it may launch or close apps, kill processes, or drive the mouse and keyboard.

Protected from every skill: the Windows and Program Files folders, `ProgramData`, `~/.ssh`, Windows' Roaming
Microsoft folder, and ISHTI's own `ishti/`, `interface/`, `orchestrator/`, `venv/` and `.git` folders. Two narrow
exceptions exist: `skillsmith` may write under `ishti/skills` after the owner's yes, and `skill_creator` may write
only under `ishti/skills/_drafts`, so a draft cannot make itself live. System processes (`csrss`, `lsass`, `svchost`,
`explorer`, `ollama`, and so on) cannot be stopped by a skill.

MEASURED from the manifests: 12 skills have `net: ["*"]` (`browser`, `deep_research`, `dev`, `digest`, `find_leads`,
`ledger`, `markets`, `shopping`, `utilities`, `web_fetch`, `web_search`, `webtools`); 7 have `apps: true` (`apps`,
`capcut`, `desktop`, `desktop_control`, `screen_control`, `terminal`, `video`). Everything else is confined to its
declared domains or to no network at all.

Limits, stated plainly:

- Enforcement works by hooking Python's `open`, `os`/`shutil` and `requests`/`urllib` while a skill runs. A subprocess
  (FFmpeg, robocopy) cannot be hooked; skills pass their output paths to `scope.check_write` where they know them.
  A skill that bypasses those APIs (for example through `ctypes`) would not be stopped by scope. Tiers and the
  quality gate (`orchestrator/quality_gate.py`, run over skill installs) are the other layers.
- Code outside a skill (the dispatcher, the server, background loops) is not restricted by scope.
- A violation raises `ScopeError`; the dispatcher reports "I stopped: ..." and the audit log records it.

## 5. Network: what leaves the PC

The rule: **nothing of yours is sent to a third party.** The only internet traffic is what the owner asks for through a web skill
(search, weather, markets, ...), a model or voice download the owner starts in Settings, or a remote channel the owner turned on.
It is enforced in code (`ishti/privacy.py`, tests in `tests/test_privacy.py`) and visible in Settings > Privacy.

- **Egress guard.** Every connection from ISHTI's Python process to a non-loopback, non-LAN address is blocked unless a skill that
  declares the host in its manifest `scope.net` is running, an owner-started download is active, or the owner enabled that channel.
  A library thread with no skill context (a telemetry or update-check thread) is therefore refused. Known telemetry hosts are
  refused even inside a skill that may reach `*`. DNS lookups of outside names are judged before they resolve.
- **Master switch** (Settings > Privacy, `data/privacy.json`): with Internet for skills OFF, nothing can reach outside at all.
- **Ledger.** `data/outbound.log` records host, port, who and allowed/blocked for every outside attempt. It never stores a query,
  URL path, header or body. Settings > Privacy shows it live, with the list of programs that can reach the internet.
- **Hardening.** Telemetry and update pings of the libraries ISHTI uses are switched off before they load (Hugging Face offline,
  ChromaDB/posthog, Gradio, OpenTelemetry, LangSmith, CrewAI, npm). Ollama cloud models are disabled. The dashboard window's
  WebView2 runs with background networking, sync and updates disabled and a loopback-only resolver.
- **Ollama's tray app** checks ollama.com for updates every hour and Ollama offers no setting to stop it; ISHTI runs its own
  `ollama serve`, so at every start it closes the tray app and moves its Startup shortcut to the Recycle Bin.
- Model calls are local (Ollama on `127.0.0.1:11434`); speech recognition and synthesis are local; there is no cloud model in the
  request path. Web search goes to the local SearXNG container first; the governed search gateway (`ishti/gateway.py`) accepts only
  loopback endpoints, validates and bounds the query, audits it with `risk=external`, and returns fenced untrusted data.
- The 12 `net: ["*"]` skills (browser, deep_research, dev, digest, find_leads, ledger, markets, shopping, utilities, web_fetch,
  web_search, webtools) can reach any site the owner asks for; the others are limited to their manifest hosts or none.
- Telegram, if the owner pairs it, passes messages through Telegram's servers (policy acknowledgement `risk_ack.telegram_cloud`).
  WhatsApp automation is implemented but off by default.
- **Limits, stated plainly:** the guard is Python-level. Ollama's own model downloads, Docker/SearXNG, Node/Playwright, FFmpeg,
  the owner's Chrome and native extensions that open their own sockets are outside it; they are covered by settings and by
  `tools/gate_no_cloud.py`, which samples their connections. Docker Desktop's own telemetry cannot be separated from SearXNG's
  traffic: turn "Send usage statistics" off in Docker Desktop. A Windows Firewall rule for `ollama.exe` / Docker would close the rest.

## 6. Audit trail

`ishti/audit.py` writes every action to `data/audit.db` (table `actions`: time, session, channel, initiator, skill,
action, detail, tier, confirmed-at, request). Each row says **who** initiated it:

| Initiator | Meaning |
|---|---|
| `owner` | The owner did it directly (a button, an edit in "About you", a yes to a question) |
| `instructed` | ISHTI did it on the owner's explicit request (the skill the request was routed to) |
| `judgment` | ISHTI chose it mid-task (planner steps, escalation, the browser engine's own clicks, fall-through) |
| `standing` | A rule or schedule the owner set earlier |

T2 rows record the request and when the owner confirmed. Secrets are masked before writing (`audit.record` replaces
registered secrets with bullets; a secret the owner typed is also kept out of history and memory). The log is
queryable by voice or text ("what did you do while I was away", "show me today's actions"). It is a SQLite log in WAL
mode with `synchronous=NORMAL`: an app crash loses nothing, a power cut may lose the last few rows (a documented
choice in the code). The log can be written by ISHTI's own process, so it is evidence of behavior, not tamper-proof.

Separate from this, the *build* history of these documents has a hash-chained log at
`docs/build/AUDIT_LOG.jsonl`, verified by `tools/verify_audit.py`. It records documentation and launcher work, not
runtime actions.

## 7. Kill switch

`ishti/killswitch.py::kill(reason)` bumps a generation counter and runs every registered stop hook: it ends speech,
model generation (checked in the token loop and before each model call), browser steps, tool subprocesses (FFmpeg,
robocopy, yt-dlp, code runners; never the Playwright MCP servers, Ollama or the watchdog), recordings and pending
yes/no questions, and it sets the global microphone OFF. It is not barge-in (which pauses to listen and keeps context).
Threads and thread pools started while handling a request inherit the kill generation, so deep-research workers stop
too (`killswitch.propagate`).

Triggers: the dashboard Stop button (`POST /api/kill`); the global hotkey (config key `kill_hotkey`, default
Ctrl+Alt+Q) held by the watchdog process when one is running, or in the server process otherwise; remote `/kill`
from a paired phone (always allowed, even when the remote channel is locked). With the desktop launcher there is no
watchdog process, so the hotkey lives in the server process and does not work if that process hangs.

## 8. Untrusted text and prompt injection

Text ISHTI did not write (web pages, search results, PDFs, emails, chat messages) is fenced between
`<<EXTERNAL ...>>` markers and the models are told never to obey it. `untrusted.scan` looks for instruction-like text
aimed at an AI ("ignore previous instructions", fake system turns, "send the password to"); a hit is flagged in the
live trace and the audit log, removed from the text the model sees, and the reply is cleaned a second time so nothing
from the injected text survives (`clean_reply`). The owner is told when it happened. Links are only opened if they are
plain `http`/`https` with a host. This is pattern-based defence, so a novel phrasing can pass; it is one layer, with
tiers, scope and the gates behind it.

## 9. Microphone and voice

The global microphone switch defaults to **OFF** on first run and persists in `data/mic_state.json`. With it off, no
audio is captured or processed and the wake word is disabled. The kill switch and shutdown force it OFF. The Live
voice session (`/ws/live`) is separate: an explicit session the owner opens, closed when the socket closes or the mic
is switched off. The browser-side microphone is transcribed locally (the Web Speech API that sends audio to Google was
removed, SOURCED: README audit P19, grep of `interface/` found no use).

## 10. Remote control (phone)

Everything a message from Telegram or WhatsApp may do is decided in `ishti/channels/gate.py`, in this order: private
chats only; pairing with a locally issued single-use code (10 minutes); allowlist of numeric user IDs (strangers get
nothing in the chat, only a capped alert to the owner); forwarded content is data; stale messages expire (300 s by
default); rate limit; lock state (when locked only `/kill`, `/unlock`, `/status`); a fixed command set; then a
**remote-forbidden classifier** that refuses, even for the owner, requests to delete files, shut down or restart the PC,
execute trades or purchases, install or enable skills, change policy or settings, reveal secrets, take over the
desktop, or take screenshots. T2 replies get Approve/Deny buttons bound to a single-use, expiring approval; the
dispatcher's own gate still has the final say. The bot token is held in Windows Credential Manager via DPAPI.
Defaults are safe: everything is off except read-only panic commands. The policy file is local-edit-only; no remote
message can write it. `desktop_control` is never remotely controllable. Contract: [API_CONTRACT.md](API_CONTRACT.md)
section 11; setup for tunnelled access: `remote/tunnel_setup.md`.

The local HTTP server trusts only unproxied loopback requests. Anything proxied or non-local must present the token in
`data/api_token` (header, cookie or `?token=`), compared in constant time (`server.authorized`). Shutdown, Ollama,
VRAM and focus endpoints also refuse requests that come from foreign web origins (`lifecycle_api.GUARDED`).

## 11. Data at rest

`data/` (databases, tokens, logs, models) is git-ignored. The arch2 memory DB and the relationship graph encrypt
sensitive fields (Fernet; key in the OS keychain through `keyring`, or a file under `data/keys/`). The learning DB and
conversation state are plaintext. Private relationship nodes are never exported to client-facing context or training
data. Incognito mode makes a conversation write nothing to memory. Secrets typed into a request are masked in history,
saved chats and the audit log.

## 12. What this document does not prove

- It describes mechanisms, not guarantees. The last recorded evidence for them is: the offline suite (464 tests
  collected today with `pytest --collect-only`; 445/445 passed at the 2026-10-02 commit `9e6003b` per
  `docs/build-history/qa/backend-2026-09-29/TEST_RESULTS.md` PART 3; I did not re-run the suite); the independent
  security scan in `docs/build-history/repo-integration/REVIEW_SIGNOFF.md` (23 medium, 0 high findings at that date);
  and `ISHTI_TEST_MODE` safety assertions in `tests/run_skill.py`.
- Real Telegram/WhatsApp end-to-end behavior and live microphone behavior were never verified on this machine (needs the
  owner's token/pairing and a working input device: `NEEDS.md` N7, N22, N23).
- No external penetration test exists.
