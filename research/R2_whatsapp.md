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
