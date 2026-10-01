"""ISHTI channels — remote control without inbound ports.

The channel layer (this package) sits between a remote transport (Telegram long polling,
WhatsApp Web self-chat) and the SAME orchestrator pipeline the desktop chat uses
(`ishti.dispatcher.handle`). It adds what a remote front door must have:

  gate.py        the funnel: allowlist, rate limits, stale-command expiry, panic commands,
                 remote-forbidden classifier, T2 step-up approvals
  security.py    pairing, numeric-ID allowlist, DPAPI secret store, PIN + lockout, lock state
  policy.py      remote_policy.yaml (local-editable only) — what remote control may ever do
  approvals.py   single-use, expiring, action-bound approval records
  telegram_client.py  raw Telegram Bot API over httpx (getUpdates/sendMessage/...)
  poller.py      the single long-polling loop (offset + update_id idempotency, graceful stop)
  output.py      sensitive-output policy, message chunking, big-table attachments
  media.py       voice notes in (faster-whisper), images in (vision tiers), files out
  whatsapp_channel.py WhatsApp adapter — default OFF, self-chat only, human-paced
  service.py     lifecycle for the server (/api/remote/*): start/stop/health/pair/revoke

Rules that never bend:
  - no inbound ports, no tunnels: Telegram = outbound long polling only (R1, RESEARCH.md D1)
  - nothing here lowers a T2/T3 gate or widens a scope; it ADDS restrictions to the dispatcher's
  - only the authenticated owner's own messages are instructions; everything else is DATA
  - the policy file is edited locally only; a remote message can never change it
"""
