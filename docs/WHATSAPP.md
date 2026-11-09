# WhatsApp in ISHTI (WAHA)

ISHTI talks to WhatsApp through **[WAHA](https://waha.devlike.pro)** (WhatsApp HTTP API), running in ISHTI's own
Docker container. There is no browser automation and no Node bridge. ISHTI calls a local, authenticated HTTP API.

> WhatsApp access is **unofficial**: WAHA speaks the WhatsApp Web protocol, and WhatsApp's terms do not allow it.
> A number can be banned. Use a number you can afford to lose, or leave WhatsApp off.

## What it can do

| Ask | What happens |
|---|---|
| "any new WhatsApp messages?" | Chats with unread messages, or recent incoming chats if the engine does not report unread counts |
| "what did Ahmed say?" / "catch me up on the family group" | Reads the chat and answers from it. Message text is untrusted data. |
| "send *running late* to Ahmed on WhatsApp" | Resolves the recipient **exactly**, shows the draft and recipient, and sends only after your **yes** |
| "send report.pdf to Sara" / "send a voice note to Ali saying..." | Same gate. The file hash is pinned at approval, so a changed file is refused. |
| "what does the PDF Ali sent say?" | Reads one exact image or PDF in a sandboxed worker with no network access (OCR / text extraction) |
| WhatsApp control (optional) | Your allow-listed numbers can message ISHTI from the phone, after an explicit risk acknowledgement |

Editing, deleting and reacting to messages are deliberately not enabled. Group admin actions are not implemented.

## Setup

1. Install and start **Docker Desktop**.
2. In ISHTI open **Settings → Connectors → WhatsApp → Link**. ISHTI then:
   - generates an API key, stores it in **Windows Credential Manager** (`ishti-waha`), and gives the container only
     its SHA-512 hash;
   - starts the container `ishti-waha` from the image pinned in `ishti/config.json → whatsapp.image`;
   - publishes it on **127.0.0.1:3747 only** (never on the LAN), with the dashboard and Swagger off;
   - creates the `default` session and shows its QR code.
3. On your phone: **WhatsApp → Linked devices → Link a device**, then scan the QR code.

The session lives in the Docker volume `ishti_waha_sessions`, so you scan once. ISHTI stops the container when it
shuts down and starts it again on the next link or connect.

### Configuration (`ishti/config.json`)

```json
"whatsapp": {
  "waha_url": "http://127.0.0.1:3747",
  "session": "default",
  "engine": "GOWS",
  "container": "ishti-waha",
  "image": "devlikeapro/waha@sha256:<digest>",
  "volume": "ishti_waha_sessions"
}
```

GOWS is WAHA's Go engine. It needs no browser, which keeps the container light; ISHTI also caps it at 768 MB.

## Safety design

- **Exact recipients.** A phone number, a full chat id, or exactly one chat or contact with that name. A fuzzy match
  is offered as a numbered choice and never sent to on a guess.
- **One yes, one send.** Every approved item gets its own action id. An append-only ledger
  (`data/wa_bridge/actions.json`) records the attempt *before* the HTTP call, so an approved send is never repeated.
  If the outcome is uncertain, ISHTI says so and does not retry.
- **Inbound control is allow-listed.** Numbers must match the saved E.164 control numbers exactly (full
  international digits). Unknown senders get no reply and raise only a rate-limited desktop alert.
- **No key leakage.** Requests go to loopback without a proxy, redirects are refused, and media is fetched only
  from this instance's `/api/files/` path.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Docker Desktop is required" | Install or start Docker Desktop, then click **Link** again |
| "WAHA refused ISHTI's API key" | The container was created with an older key. Run `docker rm -f ishti-waha` and link again (the volume keeps the session). |
| QR code never appears | Check `docker logs ishti-waha`, or **Settings → Remote → Logs → wa_bridge** |
| Messages missing | WAHA syncs history after linking; give it a minute on first link |

Code: [`ishti/wa_bridge.py`](../ishti/wa_bridge.py) (transport),
[`ishti/skills/whatsapp/skill.py`](../ishti/skills/whatsapp/skill.py) (skill),
[`ishti/channels/wa_inbound.py`](../ishti/channels/wa_inbound.py) (owner control).
Tests: `tests/test_wa_bridge.py` (fake WAHA server), `tests/test_whatsapp_safety_regressions.py`.
