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
