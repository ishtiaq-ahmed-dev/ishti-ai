# RESEARCH.md — Remote Control (Telegram + WhatsApp) + trycua/cua features

Session 2026-10-01. Five parallel research agents (R1-R5); each wrote its own research/R<n>.md,
concatenated verbatim below. Claims are VERIFIED (source + date) or UNVERIFIED (marked inline).
Live machine measurements carry the producing command; BLOCKED items name the exact need in NEEDS.md.

## Decision summary (evidence in the sections below)

| # | Decision | Grounds |
|---|---|---|
| D1 | Telegram transport = outbound long polling, single poller, offset+update_id idempotency, stale-command expiry | R1 A |
| D2 | Library = raw Bot API over httpx (zero new deps; no asyncio framework fight) | R1 D |
| D3 | Bots are NOT E2E (cloud chats) ⇒ sensitive-output redaction policy is mandatory | R1 C |
| D4 | WhatsApp = self-chat DOM over the owner's existing tab, default OFF; Cloud API documented-only; unofficial libs refused by default | R2 |
| D5 | cua: driver = feasibility candidate; omni/som/perception excluded (AGPL); Lume NO-GO (Apple Silicon); Fleets excluded (cloud) | R3 |
| D6 | Grounding = UIA tree first, existing local vision tiers as fallback, no external detector models | R5 |
| D7 | Auth = numeric-ID allowlist + pairing + DPAPI/Credential Manager + single-use expiring approvals + rate limits + panic controls | R4 |

---


# R1 — Telegram Bot API for a personal remote-control bot (Channel-Core agent)

Session: 2026-10-01. Every external fact carries a source URL + retrieval date. Local facts carry the
command that produced them.

## A. Transport: long polling vs webhook (VERIFIED)

| Question | Answer | Evidence |
|---|---|---|
| Which transport for a home PC with no public address? | **Long polling.** Webhooks require Telegram to reach a public HTTPS endpoint on ports 443/80/88/8443 with a valid SSL certificate — i.e. an inbound listener + tunnel/port-forward, which the ISHTI design forbids ("no inbound ports, no tunnels"). | https://core.telegram.org/bots/faq (retrieved 2026-10-01): "Ports currently supported for Webhooks: 443, 80, 88, 8443", "You need a valid SSL certificate for webhooks to work." |
| One poller per token? | **Yes — effectively one.** A second concurrent `getUpdates` consumer and an active webhook both conflict with polling (HTTP 409). | https://gramio.dev/telegram/methods/getupdates (retrieved 2026-10-01): "Cannot coexist with a webhook… returns a 409 error if a webhook is active"; Telegram Bot API `getUpdates` reference: "getUpdates… must not be called simultaneously from two points" (same reference, same date). |
| Confirmation / idempotency | An update is consumed only when the next `getUpdates` carries `offset > update_id`. `offset = last_processed_id + 1`. Failing to advance re-delivers the same updates forever. | gramio.dev getUpdates page + core.telegram.org/bots/faq "Long polling gives me the same updates again and again!" (both retrieved 2026-10-01). |
| Persisting the offset | The offset lives server-side once confirmed, so after a crash/restart the bot resumes where it left off **if** it re-advances the offset. For exactly-once execution ISHTI additionally records each processed `update_id` locally (SQLite) before executing, and skips already-seen IDs — protects against a crash between receive and execute. | Bot API semantics above; design decision D1 in IMPLEMENTATION_PLAN.md. |
| Messages while the PC is offline | Telegram queues updates server-side for at least 24 h and delivers them on the next `getUpdates` (that is the documented basis of "an update is considered confirmed as soon as…"). So queued messages **do arrive later** — which is exactly why WP-A must expire stale commands (default age > 5 min ⇒ not executed, owner asked to resend). | core.telegram.org/bots/faq + api reference (retrieved 2026-10-01). Startup flush: `getUpdates(offset=-1)` peeks at the newest update without consuming others — used to measure queue age at boot. |
| Flushing instead of executing | At startup ISHTI drains with a **negative offset trick**: peek newest update_id, confirm with `offset = newest_id + 1` when the whole queue is older than the stale threshold — nothing old executes, nothing is silently left pending. | gramio.dev getUpdates "Negative offset tricks" (retrieved 2026-10-01). |
| Message size limits | Bots can **send** files of any type up to 50 MB and **download** via `getFile` up to 20 MB. Text messages: the practical safe send length is 4096 characters (Bot API `sendMessage` `text` field limit — enforced by the API with 400 on longer). WP-A chunks at 3800 chars with a safety margin. | core.telegram.org/bots/faq "Handling Media" (retrieved 2026-10-01); 4096 from the Bot API `sendMessage` reference (UNVERIFIED-EXACT — masked by chunking well below it; the mock-server test asserts our chunker, not Telegram's limit). |
| Rate limits | ~1 message/second per chat sustained (bursts tolerated, then HTTP 429 with `retry_after`). WP-A enforces ≥1.1 s spacing per chat + honours 429 `retry_after`. | core.telegram.org/bots/faq "Broadcasting to Users" (retrieved 2026-10-01). |

## B. What the bot receives (and what we ignore) (VERIFIED)

- Private chats: a bot receives **all messages** in private chats with users (FAQ "What messages will my bot get?" #1) — good: the owner pairing flow is a private chat; everyone else's private messages arrive too, so the **allowlist check is ours to enforce** (WP-A: allowlist by numeric user ID, everything else ignored + logged + rate-limited alert).
- Groups/channels: privacy-mode bots only get commands/replies aimed at them; regardless, WP-A ignores any chat whose `chat.type != "private"` — an owner would never control his PC from a group.
- Inline queries: only if enabled via @BotFather; WP-A ignores them (and never enables the flag) — defence in depth.
- `forward_origin` present ⇒ the message is DATA-only: never an instruction, never routed (master-prompt rule; tested in WP-A test 1).
- Bots never see other bots' messages (FAQ "Why doesn't my bot see messages from other bots?") — removes one reflection/vector class.

## C. Bot chats are NOT end-to-end encrypted (VERIFIED — owner's belief confirmed)

- Bots work in normal **cloud chats**; secret chats (the E2E ones) are device-to-device and do not support bots. Evidence: https://telegram.org/faq ("Telegram's special secret chats use end-to-end encryption…") — secret chats are a private-chat feature between human clients; the Bot API has no secret-chat concept (api.telegram.org reference contains no secret-chat method; retrieved 2026-10-01). Third-party summary agreeing: https://stackoverflow.com/questions/48150276/ (retrieved 2026-10-01).
- Consequence: remote-control traffic traverses Telegram's servers (TLS in transit, but Telegram-operated). This is stated in the settings UI verbatim ("Telegram messages pass through Telegram's servers — not local") and drives the sensitive-output redaction policy (WP-A).

## D. Library decision (feeds WP-A)

| Option | Verdict | Reasons (evidence) |
|---|---|---|
| `python-telegram-bot` v21+ | considered | mature, asyncio; but brings its own scheduler/JobQueue conventions that would fight the dispatcher's thread-per-request model; an extra dependency + upgrade path for exactly 6 API methods we actually call. |
| `aiogram` 3.x | considered | same asyncio-first fit problem; larger framework surface (Routers, FSM) than a personal 1-user bot needs. aiogram docs themselves steer webhook-style scale ("If you will need to scale your bot, you should use webhooks" — https://docs.aiogram.dev/en/latest/dispatcher/long_polling.html, retrieved 2026-10-01) — scaling is not our problem (one owner). |
| **Raw Bot API over `httpx` (CHOSEN)** | **CHOSEN** | The Bot API is a plain JSON/HTTPS interface; we need: `getUpdates`, `sendMessage`, `answerCallbackQuery`, `editMessageText`, `getFile`+`GET /file/bot<token>/<path>`, `sendDocument`, `sendPhoto`. `httpx` 0.28.1 is already installed (pip list 2026-10-01) and already ISHTI's HTTP client elsewhere. Zero new dependencies, no framework event loop to reconcile with the dispatcher's threads (poller thread + worker thread match the existing voice/wake pattern), full control of offset handling, retries and timeouts, and the mock-server tests run against our own client with no library version drift. A 1-user bot needs none of the libraries' value (middleware, routers, broadcast machinery). |

**Blocked pieces (owner-provided, NEEDS.md N22):** the bot token (created by the owner in @BotFather) and
the owner's numeric Telegram ID (produced by the pairing flow). Until then every real-network test is
BLOCKED; the full mock-server suite runs offline and is the gate.

---

# R2 — WhatsApp as a control channel (WhatsApp-Adapter agent)

Session: 2026-10-01. Sources + retrieval dates inline. Verdict feeds WP-A: WhatsApp adapter is built
**second**, default OFF, and limited to the owner's own already-open WhatsApp Web tab.

## A. The three possible modes

### Mode 1 — DOM-first self-chat over the existing WhatsApp Web tab (RECOMMENDED, default OFF)
- **What:** the owner's own already-logged-in WhatsApp Web tab (Chrome Profile 9) is read with the
  existing browser snapshot machinery; ISHTI answers only in the owner's **own self-chat** ("Message
  yourself") or one designated chat. Human-paced sends, everything else ignored.
- **Why it fits:** zero new dependencies (the repo already ships `ishti/skills/whatsapp/skill.py` which
  reuses the open tab and never reloads it — issue 6 fix, see skill docstring); no new accounts; no
  cloud; no inbound ports; local-only preserved. Reading is DOM parsing of a page the owner already has
  open — the same class of operation as the existing T0 "unread" read path.
- **Risk (honest):** it is still browser automation of a consumer account. Meta's enforcement historically
  targets unofficial protocol implementations (mass messaging, credential reuse, non-human pacing);
  DOM automation of one's own session at human pace in one's own chat is the lowest-risk automation
  posture available, but **not zero risk** — a false step could reach a wrong chat if the DOM changes.
  Mitigations in WP-A: chat-title assert before every send, self-chat allowlist, human pacing (≥3 s
  between actions), T2 gate for every send, default OFF, one flag flips it on.

### Mode 2 — Official WhatsApp Business Cloud API (possible, owner opt-in only)
- **Verified:** it is Meta's sanctioned automation path (no ban risk from automation per se), but it
  requires a Meta business account + app, a **public HTTPS webhook** for inbound messages, and Meta-hosted
  processing — three direct conflicts with ISHTI's design (no inbound ports/tunnels; local-only; no cloud
  in the loop). Evidence: Meta Cloud API docs require a webhook subscription endpoint for receiving
  messages (https://developers.facebook.com/docs/whatsapp/cloud-api — structure verified 2026-10-01,
  webhook requirement cross-checked via https://chatydesk.com/blog/can-i-automate-whatsapp-without-getting-my-number-banned
  and https://sporesec.com/en/blog/whatsapp-unofficial-api-ban-risk, both retrieved 2026-10-01, which
  contrast the official API's requirements against unofficial libraries).
- **January 2026 policy note:** Meta formally banned open-ended AI "assistant" bots on the WhatsApp
  Business Platform (only pre-approved categories) — https://sporesec.com/en/blog/whatsapp-unofficial-api-ban-risk
  (retrieved 2026-10-01). A personal remote-control bot is not an approved category ⇒ even the official
  path is likely a policy non-starter for this use, independent of the infrastructure cost.
- **Verdict:** OFF by default; only if the owner explicitly chooses it after reading the warning
  (breaks no-inbound-port design + needs a Meta business presence). Not implemented this pass.

### Mode 3 — Unofficial protocol libraries (Baileys / whatsapp-web.js / WAHA / Evolution API) (BLOCKED by default)
- **Ban risk is real and current:** unofficial libraries "impersonate a real phone"; sources report bans
  within weeks; one 2026 study cited at 68% of businesses banned within 12 months using similar tools.
  Sources: https://sporesec.com/en/blog/whatsapp-unofficial-api-ban-risk (2026-10-01),
  https://socialmate.app/blog/baileys-whatsapp/ (2026-10-01), https://www.pipes.bot/blog/baileys-vs-whatsapp-cloud-api-ban (2026-10-01).
- **Supply-chain risk is current, not historical:** the Baileys npm ecosystem had a credential-stealing
  "anti-ban" package (April 2026) and a large malicious-fork campaign (September 2026) — recorded in this
  repo's own NEEDS.md N12 (researched 2026-09-29 with sources there) and consistent with the pages above.
- **Policy:** stays OFF unless the owner explicitly approves after reading the warning; if ever approved:
  genuine package from the official repo, exact version pinned, source reviewed, no telemetry, isolated
  install, and the session ends if any third-party script tag appears in the tab. **Nothing of this is
  built now.**

## B. What IS built (mode 1, default OFF — the WP-A WhatsApp adapter)
1. Reuses the existing `whatsapp` skill's tab-reuse helpers (`open_whatsapp`, `unread`, `parse_messages`)
   for reading; never navigates/reloads the owner's tab when it can avoid it.
2. Reads **only** the designated chat (default: the owner's self-chat, "Message yourself"); any other
   chat's content is never parsed into context, never answered. Chat-title is asserted before reading
   and before every send.
3. Sends are: T2 gated, human-paced (≥3 s between automation actions, jittered), self-chat only,
   max length respected (chunked like Telegram), and dry-run under `ISHTI_TEST_MODE=1`.
4. `remote_policy.yaml` ships with `whatsapp_enabled: false` + the risk text that the settings UI shows.
5. Auth model: the designated chat IS the channel; there is no pairing (anyone who controls the owner's
   unlocked PC browser could type there — which is why every send is still T2 and why the adapter is OFF
   until the owner flips it locally).

## C. Decision table (feeds IMPLEMENTATION_PLAN.md WP-A)

| Mode | Default | Owner action to enable | Built this pass |
|---|---|---|---|
| 1 — self-chat DOM over existing tab | OFF | flip `remote_policy.yaml` locally in Settings | YES (adapter + tests) |
| 2 — official Cloud API | OFF | explicit owner choice (needs public webhook; breaks local-only) | no — documented only |
| 3 — unofficial libraries | BLOCKED | explicit owner yes after reading warning + pinned genuine package | no — refused by default |

NEEDS.md entry added (N23): the owner decides whether mode 1 may ever be switched on; until then the
adapter is dormant code with tests, zero effect on the running app.

---

# R3 — trycua/cua packages (cua-Integrator agent)

Session: 2026-10-01. Primary source read directly: https://github.com/trycua/cua (official repo,
retrieved 2026-10-01). Rule: official repo only — many forks exist; nothing is installed from a fork.

## A. Package-by-package findings

| Package | What it is (verified from README) | License (verified) | Windows support | Local-only fit | Verdict for ISHTI |
|---|---|---|---|---|---|
| `cua-driver` | "Background computer-use driver for any agents… inspect and operate native desktop apps and browsers on macOS, Windows, and Linux… through the CLI, MCP, or typed SDKs. Background delivery lets agents work without moving your pointer or taking focus when the app and platform support it" | MIT (repo LICENSE; LICENSING.md lists per-dir exceptions) | **Windows stable** ("Today we're bringing Cua Driver to Windows… now stable", trycua on X 2026-05-27; blog "inside-windows-computer-use.md" in the repo) | Yes — local driver, MCP over stdio, no cloud dependency for operation | **Feasibility-protocol candidate** (WP-B: install pinned, 5-task protocol, GO/PARTIAL/NO-GO with evidence) |
| `cua-agent` | The agents-loop package (planning agent around a computer) | MIT core; **`cua-agent[omni]` pulls ultralytics = AGPL-3.0** (repo README: "cua-som… licensed under AGPL-3.0-or-later. Its Ultralytics dependency retains its own license") | works where the driver works | yes | **EXCLUDE `omni` extra** (AGPL); plain agent optional later |
| `cua-sandbox` | Sandboxes incl. Windows + Linux for running agents isolated | MIT (per LICENSING.md) | claimed; Windows Sandbox on this box is **disabled** (`InstallState=2`, measured 2026-09-30, RESEARCH.md of the skill_creator session) — enabling it is a Windows-features change = owner decision | yes (local sandbox mode) | Feasibility probe; Windows edition check first |
| `cua-computer-server` | Server side of computer control (used by sandboxes/Fleets) | MIT | n/a for a single-PC personal setup | yes | Not needed (we are the host, not a fleet) |
| `cua-bench` | "Build computer-use tasks, evaluate agents, and export trajectories for training. Start with a simulated task that requires no VM, Docker, or model API key." Python 3.12/3.13 + uv | MIT | tool runs where Python runs; needs **Python 3.12+** — this venv is 3.11.9 (measured) ⇒ isolated tool venv, not ISHTI's | yes (simulated tasks need no VM/model key) | Feasibility probe for local benchmarking of ISHTI's own skills |
| `CUA-S1` | "family of small, specialized System 1 models for computer use… The first research profile focuses on forms… The GitHub component is an early, source-only research release; model weights are hosted separately on Hugging Face. The source is MIT-licensed." | MIT source; **model/dataset cards carry their own licenses** (check before use) | weights = HF download; VRAM cost unmeasured | yes once weights local | Watch; not a dependency now (forms-only profile, early release) |
| `Lume` | "Create and manage local macOS and Linux VMs on Apple Silicon" | MIT | **No** — Apple Silicon only; this is an RTX 5070 Ti Windows box | n/a | **NO-GO** (hardware mismatch, no measurement needed) |
| `Fleets` | "Provision isolated cloud desktops at run.cua.ai" | commercial cloud | n/a | **No** — cloud, excluded by the master prompt | **EXCLUDED** |

## B. Security-relevant facts for WP-B integration
- The driver speaks **MCP over stdio** — fits ISHTI's existing `ishti/mcp_client.py` pattern (Playwright MCP already used that way).
- Background delivery ("without stealing focus/pointer") is **platform-and-app dependent** — must be measured on this PC, not assumed (WP-B test 3 of the master prompt).
- Install method is `irm https://cua.ai/driver/install.ps1 | iex` on Windows — **read-before-run rule applies**: the installer script must be downloaded, read and only then executed, and the version pinned. Until the owner-visible vetting pass, this is treated as a blocked install.
- The `cua-perception` extension (OmniParser detector) is **AGPL-3.0-only** and downloaded from signed GitHub release assets — excluded from any default install (master-prompt license rule).
- Windows support status "stable" is the vendor's claim (their own channels) — the 5-task protocol on this PC is the real evidence WP-B produces.

## C. Pre-decided exclusions (no further research needed)
Lume (Apple Silicon only), Fleets (cloud), `cua-agent[omni]`/cua-som/cua-perception (AGPL unless the owner approves), any cloud model provider. This is a master-prompt rule, not a judgement call.

## D. What WP-B must measure on this PC (the feasibility protocol, evidence → CUA_FEASIBILITY.md)
1. Pinned `cua-driver` install (installer read first) + `--version` + MCP server mode reachable.
2. 5 sandbox tasks × 10 runs: Notepad typing, Calculator, file rename in Explorer (sandbox folder), local-page form in Chrome, two-window copy/paste. Success rate, steps, latency, CPU/RAM.
3. Background claim: type into another window while ISHTI acts — focus stolen or not.
4. Local grounding: UI Automation tree first; screenshot+local-vision fallback measured for accuracy/latency (no partial offload).
5. `cua-sandbox` Windows edition check + startup time + host-isolation probe.
6. `cua-bench` custom ISHTI task + trajectory export (isolated venv).

---

# R4 — Security design for remote control of a PC from a phone (Security-Gate agent)

Session: 2026-10-01. Threat model: the owner's phone (or anyone holding it / SIM-swapping him) talks to
his PC; Telegram's servers are an untrusted intermediary in the middle; any text that is not the owner's
own authenticated message is attacker-controlled DATA (prompt injection).

## 1. Identity & authentication (who may speak to ISHTI remotely)

| Control | Design (feeds WP-A) | Rationale |
|---|---|---|
| Allowlist by numeric Telegram user ID | The single source of truth is the paired `owner_telegram_id` (a positive integer Telegram assigns, not the @username — usernames are reusable and spoofable by lookalikes). One ID by default; multiple allowed in `remote_policy.yaml`. | A username is changeable; the numeric ID is stable. Allowlisting beats passwords for a 1-user system (no secret travels in messages). |
| Private chat only | Updates from groups/channels/inline are ignored by chat type before anything else runs. | Group membership is a social vector; private chat is the minimal surface. |
| Pairing | Desktop UI "Pair Telegram" → 6-digit one-time code, 10-minute expiry, single use, stored hashed → owner sends `/pair <code>` from his account → ISHTI records the sender's numeric ID. Re-pair/unpair only from the desktop. | Binds the channel to a Telegram account the owner proved possession of, via a code that never leaves the LAN except through the owner's own keyboard. |
| Token storage | Bot token in **Windows Credential Manager via win32crypt DPAPI** (CurrentUser scope) — never in config files, env, logs, or git. Plaintext only in memory while in use. | DPAPI binds the secret to the Windows user account; a stolen `remote_policy.yaml` file alone is useless. (pywin32 312 installed — measured pip list 2026-10-01.) |
| Replay protection | (a) update_id processed-set persisted; (b) approval IDs are single-use, bound to one action, expiring (default 120 s); (c) pairing codes single-use + expiring; (d) `answerCallbackQuery` consumes the callback id — a second press answers an already-consumed query. | Each layer kills one replay class: re-executed updates, re-used approvals, re-used codes. |
| Stolen-phone / SIM-swap | Telegram-side: the owner must enable Telegram 2FA (cloud password) — we surface this as advice in the settings UI. ISHTI-side: `/lock` (needs PIN to unlock), optional local PIN as second factor for T2 approvals, all sessions revocable from the desktop UI, and every remote action is audit-attributed so a compromise is reconstructible. | A phone can be stolen; the PC must be able to lock its own front door from the PC side. |
| Rate limiting | Per-sender token bucket (default 20 msg/min, burst 5) + unknown-sender alert cap (3 alerts/hour) + Telegram 429 `retry_after` honoured. | Bounds brute-force/pair-guessing and alert spam; keeps us inside Telegram's own 1 msg/s/chat guidance (R1). |

## 2. Authorization (what a remote message may cause)

- **Default remote scope: T0/T1 only.** T2 requires step-up approval (below). T3 unchanged (never).
- **Remote-forbidden list** (refused even with approval, unless enabled locally in `remote_policy.yaml`):
  file deletion, PC shutdown/restart, trade/finance execution, installing/enabling skills, changing
  tiers/scopes/policy/config, reading secrets, desktop control (WP-B), screenshots.
  `remote_policy.yaml` itself is **local-only-editable** — no remote message can change it (tested).
- Enforcement point: the channel layer wraps the dispatcher; the dispatcher's own T2 gate still runs
  (defence in depth — the channel gate is not a replacement, it is an additional AND).

## 3. Step-up approval for T2 (bound, single-use, expiring)

1. ISHTI replies with an **exact recap** (what will happen, on which target, with what value) + inline
   **Approve / Deny** buttons (Telegram) or a one-time code requirement (WhatsApp / high-risk).
2. The approval record: `{action_id, recap_hash, channel, expires_at=now+120s, used=False}`.
3. Approve consumes it: only the matching `action_id`, only before expiry, only once, only from the
   authenticated sender. Approving action A does not approve action B (the callback data carries the
   action_id; a mismatched pair is rejected + audited).
4. Optional second factor: `local_pin` in Credential Manager; 5 wrong entries ⇒ 15-minute lockout
   (persisted, so restarting ISHTI does not reset it).
5. Every step audited: `approval.requested / approved / denied / expired / replayed / mismatch`.

## 4. Panic controls (always available remotely, never gated)
- `/kill` — the existing `ishti.killswitch.kill()` (stops speech, generation, browser steps, tool subprocesses; bumps generation so in-flight requests cancel).
- `/lock` — refuse everything except `/unlock <pin>`; lock state survives restart (persisted flag).
- `/status` — read-only snapshot (VRAM, loaded models, running tasks) — explicitly T0 and safe to answer even while locked (it reveals nothing sensitive; sizes/percentages only).

## 5. Untrusted content discipline (prompt-injection defence, remote edition)
- Only the owner's own authenticated messages are instructions. Forwarded/quoted text, files, web pages,
  and WhatsApp messages from anyone else are DATA — never executed, never routed.
- Remote replies follow the **sensitive-output policy**: client data, finance, chat digests, anything
  marked sensitive ⇒ minimized/redacted with "open on PC" instead; secrets and the owner profile/self-model
  are never included, per category override only locally.
- Remote commands older than `stale_command_max_age_s` (default 300) are expired, not executed — a queued
  message sent while the PC was off must be re-confirmed by the owner (it cannot be trusted to still
  reflect intent, and its context may have changed).

## 6. Audit & observability
- Every remote action carries `channel="telegram"|"whatsapp"`, `session=<channel>:<chat_id>`, and the
  message/update id in the audit detail — the existing `ishti.audit.record()` attribution extended, not
  bypassed (audit rows use the same `actions` table the repo already queries).
- Unknown-sender attempts: logged + rate-limited alert to the desktop (and to the paired channel if one
  exists) — never answered in-chat (no engagement with attackers).
- Remote sessions visible in the desktop UI; "revoke all sessions" clears pairings + pending approvals.

## 7. Residual risks, stated honestly (go to NEEDS.md / settings-UI text)
1. Telegram cloud chats are not E2E (R1 §C): a Telegram-side compromise sees remote traffic. Sensitive-output redaction limits exposure but cannot eliminate it.
2. A fully compromised owner phone (malware reading the Telegram session) is inside the allowlist by definition. Mitigations: T2 recaps are exact; panic `/kill`/`/lock` exist; sensitive categories stay minimized; the desktop can revoke. This is inherent to any remote control — the honest answer is "limit blast radius", not "impossible".
3. The pairing code transits Telegram once (owner sends it to the bot). 10-min single-use expiry bounds the window.
4. DPAPI protects against file theft, not against a live attacker running as the Windows user. Out of scope for a personal box; noted for completeness.

---

# R5 — Local screen-grounding / vision for computer use on 16 GB VRAM (Grounding-Tester agent)

Session: 2026-10-01. Machine state at research time measured live (commands inline).

## A. Machine state measured now
- `nvidia-smi --query-gpu=memory.used,memory.total`: **1522 / 16303 MiB** (idle-ish, other apps hold ~1.5 GB) — 2026-10-01.
- Ollama: **not running** (`curl http://127.0.0.1:11434/api/version` → connection refused, 000; no ollama.exe in tasklist). Per the safety rules I do not start the model server un-supervised ⇒ **every VRAM/model measurement below that needs a live server is BLOCKED**, with the exact need in NEEDS.md (N24: "owner starts ISHTI/Ollama once so grounding can be measured").
- `faster-whisper` 1.2.1 installed (pip list 2026-10-01); the local turbo STT build lives in
  `data/models/whisper-large-v3-turbo` (repo voice docs; path checked) — voice notes arriving via Telegram
  will transcribe through the existing `ishti.voice.transcribe()` path, no new model needed.

## B. Grounding strategy (design, consistent with the master prompt's "UI Automation tree first")

| Layer | Mechanism | Cost | Status |
|---|---|---|---|
| 1. UI Automation / accessibility tree | Windows UIA via pywin32/UIAutomation — text pattern of buttons/fields/values, exactly like the repo's browser `browser_snapshot` text approach | CPU-only, ms-class | AVAILABLE (pywin32 312 present). This is the primary grounding: it gives element names + coordinates without any vision model. WP-B measures real coverage on the 5 protocol tasks. |
| 2. Screenshot + local vision model | Existing repo vision tiers: `gemma3:4b` (tier 2) / `gemma3:12b` (tier 3, needs 95% budget — NEEDS.md N3 measured 8973 MiB) describe screenshots; coordinates then come from the UIA tree or asked-for region logic | 4B ≈ 3 GB / 12B ≈ 9 GB VRAM (both previously measured on this GPU per MODEL_DECISIONS/NEEDS.md N3, 2026-09-29) | PARTIAL: mechanism exists in-repo; live accuracy/latency re-measure BLOCKED with Ollama down (N24) |
| 3. CUA-S1 / OmniParser-style detector | trycua's small grounding models / cua-perception extension | ONNX on GPU/CPU; extension is **AGPL-3.0-only** (R3) | EXCLUDED by default (license). CUA-S1 weights: early research release, separate HF license — watch-list only, not built now |

**Design decision (feeds WP-B):** grounding = UIA tree first (no VRAM), screenshot+existing vision tiers
as fallback (already governed by the VRAM governor), external detector models excluded on license grounds
unless the owner approves. No partial offload rule applies to any model used — verified by the existing
`/api/ps` sampler in the concurrency suite (0 partial offloads in 663 samples, CONCURRENCY_REPORT.md).

## C. What can be tested right now (no GPU needed) — and what was tested
1. UIA availability probe (pywin32 `win32com.client` + `IUIAutomation`) — run in WP-B feasibility, CPU-only.
2. Voice-note transcription pipeline offline: `ishti.voice.transcribe` on a synthesized opus/ogg file — covered by existing `tests/test_voice_note_generation.py` + STT tests; the channel test reuses those fixtures.
3. Image-in pipeline: routed to the existing vision skill chain behind the governor (measured in the skill loop, video 9.4 GB shared VRAM flat — PROGRESS.md QA table).
4. VRAM governor integration: the channel layer itself loads NO models; it only triggers existing governed paths ⇒ no new partial-offload risk surface (asserted by test 13 in TEST_RESULTS).

## D. cua-driver's own grounding (for WP-B)
The official README (retrieved 2026-10-01) says background delivery "when the app and platform support it";
on Windows the driver's accessibility/UIA usage is the vendor's approach — WP-B measures it on the 5 tasks
rather than trusting the claim. License facts in R3 §A.

---
