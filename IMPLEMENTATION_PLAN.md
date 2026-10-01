# IMPLEMENTATION_PLAN.md — Remote Control (Telegram + WhatsApp) + cua features

Written **before any feature code**, per the master prompt. Phase 1 evidence: `RESEARCH.md`
(5 parallel agents, `research/R1..R5.md`). Session 2026-10-01. Machine: Windows 11 Pro 25H2,
RTX 5070 Ti 16 GB, 64 GB RAM, Python 3.11.9 venv.

## 0. Deviations from the master prompt, documented

1. **One working tree, exclusive file ownership** (established repo protocol since the skill_creator
   session): agents do not get separate git branches; isolation is by exclusive file claims, the
   integrator rejects overlaps. The prompt's "one branch per agent" is unworkable with a single
   checkout that must keep serving the owner's desktop app.
2. **`channels/` location**: the prompt says `channels/`; ISHTI's importable package root is `ishti/`,
   so the channel machinery lives at `ishti/channels/` and a thin `channels/README.md` points at it.
   Same pattern as the documented skill_creator deviation (skills live at `ishti/skills/<name>/`).
3. **cua feasibility vs 8-attempt rule**: the driver install requires executing an upstream PowerShell
   installer (read-before-run) and a live Ollama for grounding measurements — **Ollama is down right now**
   (measured). WP-B therefore produces the full protocol + the offline-capable parts, and marks the
   live-hardware rows BLOCKED with the exact need (N24) rather than burning attempts.
4. **Real-network tests need the owner's bot token** (N22). All 13 WP-A tests are designed to run against
   a **mock Telegram Bot API server** (in-process httpx MockTransport + a localhost FastAPI stub) so the
   gate is runnable now; the real-bot run is a repeat of the same suite with `ISHTI_TELEGRAM_TOKEN` set.

## 1. Goals

| # | Goal | Success looks like |
|---|---|---|
| G1 | The owner controls ISHTI from his phone through one authenticated channel, with the same skills, tiers, audit and memory as the desktop chat. | Paired Telegram message → dispatcher reply on the same channel; every step in the audit log attributed to channel + message id. |
| G2 | Nothing remote can bypass or lower a gate. | Default remote scope T0/T1; T2 needs single-use expiring approval; remote-forbidden actions refused even with approval; `remote_policy.yaml` not remotely editable; test 1–4, 10 green. |
| G3 | Offline/stale/duplicate messages cannot cause stale actions. | update_id idempotency across restart; queued-while-off messages expire (default 300 s) and ask to resend; zero duplicate executions in tests 5, 7. |
| G4 | Panic controls always work, including under load and while locked. | `/kill`, `/lock`, `/status` tested under load (test 6); kill reuses the existing killswitch. |
| G5 | Output honours Telegram's limits and the privacy honesty rule. | Long replies chunked; big tables as CSV/PDF attachment; sensitive categories redacted with "open on PC"; no secrets in logs/git (tests 8, 9, 10). |
| G6 | WhatsApp adapter exists, is default OFF, self-chat-only, human-paced. | Adapter dormant until the owner flips `remote_policy.yaml` locally; messages in other chats never read (test 11). |
| G7 | cua components get an evidence-based GO/PARTIAL/NO-GO per component. | `CUA_FEASIBILITY.md` with measured rows where hardware allows, BLOCKED+exact-need where not (N24/N22), no AGPL/cloud components. |
| G8 | Everything lands in the repo's verification system. | API contract §11, FEATURE_CATALOG, TEST_MATRIX, self-model updated; full offline suite green 3× at the end. |

## 2. Non-negotiable constraints (inherited, measured)

| Constraint | Evidence | Consequence |
|---|---|---|
| T2 gate = `ask_first` yes/no; test mode blocks at the last step | `ishti/skills/__init__.py:216-220`, `ishti/test_mode.py` | channel layer adds its gate **in addition**, never instead |
| Audit is `audit.record(action, detail, skill, initiator, tier, confirmed, session, request)` with channel in thread context | `ishti/audit.py:58-66` | remote rows set `channel="telegram"/"whatsapp"`, session `tg:<chat_id>` |
| Kill switch: `killswitch.kill(reason)` bumps generation, runs hooks, ends tool processes | `ishti/killswitch.py:46-66` | `/kill` calls it; nothing new invented |
| Dispatcher: `handle(text, session_id=None, channel="text", on_token=None, on_event=None)` | `ishti/dispatcher.py:508` | remote calls use `channel="telegram"/"whatsapp"`, session `tg:<chat_id>` / `wa:self` |
| Server auth: bearer `API_TOKEN`; remote-looking clients must present it | `interface/web_ui/server.py:57-66`, `ishti/check.py:check_remote_auth` | the Telegram poller is an outbound client — it presents the token to localhost APIs like any remote client would |
| No inbound ports/tunnels; Ollama NOT started by tests; Ollama currently down | RESEARCH R1/R5 (measured 2026-10-01) | long polling only; live VRAM rows BLOCKED (N24) |
| httpx 0.28.1 present; no telegram/aiogram libs | pip list 2026-10-01 | raw Bot API client (D2) |
| Skills are manifests + `skill.py`; new skills start DISABLED | `ishti/skills/__init__.py:168-187`, master prompt | `desktop_control` skill (WP-B) lands `status:"disabled"` if built |

## 3. Architecture

```
phone (Telegram app)
  |  TLS, Telegram cloud (NOT local — disclosed in UI)
  v
TelegramPoller (daemon thread; outbound long polling, offset file + processed-set in SQLite)
  |  updates
  v
ChannelGate (ishti/channels/gate.py)
  |- allowlist check (numeric user id; private chat only)
  |- rate limiter (token bucket) + unknown-sender alert (capped)
  |- stale-command expiry (queued-while-off > 300 s => ask to resend)
  |- panic commands: /kill /lock /unlock /status /tasks /cancel /pause /resume /digest /help
  |- remote-forbidden classifier (deleting files, shutdown, finance, policy edits, secrets...)
  |- T2 step-up: recap + Approve/Deny inline keyboard -> single-use expiring approval store
  v
dispatcher.handle(text, session_id="tg:<chat_id>", channel="telegram")
  |  (same skills, tiers, audit, memory, background tasks as the desktop chat)
  v
SensitiveOutputFilter (ishti/channels/output.py)  -> chunker (3800 chars) -> TelegramAdapter.send
                                                   -> big tables as CSV/PDF attachment

WhatsAppAdapter (ishti/channels/whatsapp_channel.py): default OFF; reads only the designated
(self-)chat via the existing whatsapp skill's tab machinery; human-paced T2-gated sends.

DesktopControl (WP-B, gated by cua feasibility): ishti/skills/desktop_control/ manifest
status:"disabled", T1 inspect / T2 act via cua-driver MCP, per-app allowlist, remote use OFF.
```

## 4. Work packages (owner, exclusive files, dependencies, proof, rollback)

**File-ownership rule: no two agents edit the same file.** Shared-file edits (server.py, App.tsx,
contract docs) are sequenced at merge time, owned by the Integrator.

| WP | Deliverable | Owner-agent | Files it may touch (exclusive) | Depends | Proof it works | Risk | Rollback |
|---|---|---|---|---|---|---|---|
| WP-A1 | Bot API client + poller (offset, idempotency, stale expiry, graceful stop) | Channel-Core | `ishti/channels/__init__.py`, `ishti/channels/telegram_client.py`, `ishti/channels/poller.py` | WP0 | mock-server: offset advance, duplicate skip, offline queue expiry, clean shutdown, no orphan threads | Low (new files) | delete files |
| WP-A2 | Auth: DPAPI secret store, pairing, allowlist, rate limiter, lock state | Security-Gate | `ishti/channels/security.py`, `ishti/channels/policy.py` | WP-A1 | pairing round-trip; wrong-user ignored+logged; 429/alert caps; lockout after 5 bad PINs | Medium (security-critical) | delete files |
| WP-A3 | Gate: panic commands, remote-forbidden classifier, T2 step-up approvals | Security-Gate | `ishti/channels/gate.py`, `ishti/channels/approvals.py` | WP-A2 | tests 2,3,6 green (mock) | Medium | delete files |
| WP-A4 | Output: sensitive-output filter, chunker, table→CSV/PDF, voice-in/out | Channel-Core | `ishti/channels/output.py`, `ishti/channels/media.py` | WP-A1 | tests 8,10 green; STT via existing voice.transcribe | Low | delete files |
| WP-A5 | WhatsApp adapter (default OFF, self-chat only) | WhatsApp-Adapter | `ishti/channels/whatsapp_channel.py` | WP-A1..A4 | test 11 green (fake tab snapshot); default OFF verified | Medium | delete file |
| WP-A6 | Service wiring + `/api/remote/*` + lifecycle | Integrator | `interface/web_ui/server.py` (claimed at merge), `ishti/channels/service.py`, `docs/API_CONTRACT.md` §11 | WP-A1..A5 | `/api/health` shows remote state; `/api/remote/pair` flow; starts/stops with the app | Medium | `git checkout -- interface/web_ui/server.py` |
| WP-A7 | Settings UI: Remote Control section (toggles, pairing, policy viewer, activity) | UI-Settings | `interface/app/src/sections/Pages.tsx` (claimed at merge), `interface/app/src/lib/api.ts` | WP-A6 | npm build exit 0; toggles default OFF visible | Low | git checkout |
| WP-B1 | cua feasibility protocol (offline parts: UIA probe, license audit, install-script read) | cua-Integrator | `CUA_FEASIBILITY.md`, `research/` scratch | WP0 | protocol table with GO/PARTIAL/NO-GO + evidence rows | Low | delete file |
| WP-B2 | `desktop_control` skill skeleton (manifest T1/T2, allowlist, remote OFF) — **only if B1 = GO/PARTIAL for the driver** | cua-Integrator | `ishti/skills/desktop_control/` | WP-B1 | manifest-conform (`tools/check_skills.py`), starts disabled, tier rules asserted | Medium | delete folder |
| WP-C1 | Tests: `tests/test_channels.py` (13 acceptance tests, mock Telegram) + mock server | Tester | `tests/test_channels.py`, `tests/mock_telegram.py` | WP-A1..A5 | 3 consecutive green runs | Low | delete files |
| WP-C2 | Docs: FEATURE_CATALOG, TEST_MATRIX, self-model note, NEEDS, PROGRESS | Integrator | those files only | all | rows added with evidence links | Low | git checkout |
| WP-D | Full suite + `TEST_RESULTS.md` + `FINAL_REPORT.md` | Tester/Integrator | those files | all | raw command output in reports | Low | — |

## 5. Schedule

```
WP0 commit checkpoint (in-flight skill_creator work) + tag
  |
  +-- PARALLEL BAND 1 (no shared files, no GPU)
  |     Channel-Core:     WP-A1 -> WP-A4
  |     Security-Gate:    WP-A2 -> WP-A3 (after A1 lands the client interface)
  |     WhatsApp-Adapter: WP-A5
  |     cua-Integrator:   WP-B1
  |
  +-- SEQUENTIAL MERGE (integrator; full offline suite after each merge)
  |     WP-A1 -> A2 -> A3 -> A4 -> A5 -> A6(server) -> A7(UI) -> WP-C1 tests -> WP-B2 -> WP-C2 -> WP-D
  |
  GPU WINDOW (only if owner starts Ollama; else BLOCKED): R5/WP-B live VRAM rows (N24)
```

## 6. Acceptance criteria (the gate — mirrors the master prompt)

READY requires: tests 1–13 green (mock Telegram; real bot = same suite with token, else BLOCKED);
zero ungated T2 actions via any channel; zero secrets in logs/git (grep test); no inbound ports
(poller = outbound only — asserted); zero orphan pollers/processes after shutdown; cua components
integrated-with-tests or NO-GO with evidence; zero partial-offload observations during any run;
every Critical/High issue fixed or in NEEDS.md with the exact missing thing.

## 7. Owner decisions needed (NEEDS.md, never assumed)

- **N22** bot token via @BotFather + pairing produces the numeric ID (real-network tests stay BLOCKED until then).
- **N23** WhatsApp mode decision (self-chat mode built but OFF; nothing enabled without the owner).
- **N24** start Ollama/ISHTI once so the cua/grounding live measurements can run.
- **N25** (owner-approval gate) enabling any AGPL cua perception component — default: excluded.
- Nothing in this plan deletes data, changes a T2/T3 rule, or needs a purchase.
