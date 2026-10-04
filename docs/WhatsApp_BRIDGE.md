# WhatsApp: pair once by QR (no browser)

ISHTI can talk to WhatsApp **two ways**:

| Transport | How it connects | Good for |
|---|---|---|
| **Bridge** (new, default when running) | scan a **QR code once** — Baileys | everything: messages arrive live, no Chrome, no page reading |
| **Browser** (unchanged) | Chrome Profile 9 already signed into WhatsApp Web | fallback when the bridge is off |

Both answer the same requests; the bridge is simply faster and never times out on a page load.

Built on **[Baileys](https://github.com/WhiskeySockets/Baileys)** — 11.2k stars, MIT, actively maintained;
the maintained open-source WhatsApp-Web WebSocket library. Not affiliated with WhatsApp: it is an unofficial
client, so it is worth knowing that WhatsApp's terms discourage automated/bulk use, and the official route
for a business is the WhatsApp Business API. For reading and replying to your own chats on your own PC it
behaves like WhatsApp Web.

## Setup (once)

```powershell
cd D:\ISHTI\tools\wa_bridge
npm install          # Baileys + express + qrcode-terminal (already done for you)
node index.js        # prints a QR and starts the local API
```

Then, on your phone: **WhatsApp → Linked devices → Link a device** → scan the code.
The code is also written to `data/wa_bridge/pair.txt`; `node index.js --qr-only` prints just the QR.

The pairing is saved in `data/wa_bridge/auth_info`, so **you only scan once**. It reconnects by itself
after a restart and only re-asks if you log out from the phone.

## What it does once linked

- **new messages as they arrive** — no page load, no polling a browser tab
- read any chat or group, with sender names
- send text and media, mark read, delete for everyone, edit, react
- `data/wa_bridge/inbox.jsonl` keeps a local record of everything

Sends are still gated: ISHTI shows the exact recipient and words and waits for your **yes**, exactly as
before. The bridge is the transport, never the decision.

## Talking to it

Just ask ISHTI:

- "Any new WhatsApp messages?" → straight from the live socket
- "What did Sara say in our last chat?" → the real transcript
- "Delete the last message to Ahmed" / "React to her last message with +1" / "Edit the message saying …"
- "Connect WhatsApp by QR" / "Is WhatsApp linked?" → the bridge's real state

## The local API (127.0.0.1:8788, this PC only)

```
GET  /status                  -> {connected, me, qr}
GET  /chats                   -> [{id, name, unread, last}]
GET  /messages?chat=<id>&limit=50
POST /send     {chat, text} | {chat, media, caption}
POST /markread {chat}
POST /delete   {chat, key}      POST /edit {chat, key, text}      POST /react {chat, key, emoji}
```

Python side: `ishti/wa_bridge.py` (`status()`, `connected()`, `chats()`, `messages()`, `send()`,
`mark_read()`, `delete()`, `edit()`, `react()`, `find_chat()`). It is a no-op when the bridge is not
running, which is why the WhatsApp skill keeps working either way.

Self-checks: `venv\Scripts\python -X utf8 ishti\wa_bridge.py` and `...\ishti\skills\whatsapp\skill.py`.