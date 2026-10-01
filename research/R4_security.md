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
