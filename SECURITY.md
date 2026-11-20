# Security policy

ISHTI is a single-owner assistant that can read your files, mail and messages and act on your PC. Security problems
are taken seriously. This page explains how to report one and what the security model is.

## Reporting a vulnerability

**Do not open a public issue for a security problem.** Use GitHub's private
[security advisory](https://github.com/ishtiaq-ahmed-dev/ishti-ai/security/advisories/new) form, or message the maintainer
on GitHub ([@ishtiaq-ahmed-dev](https://github.com/ishtiaq-ahmed-dev)). Please include:

- the ISHTI version (`venv\Scripts\python -m ishti --version`) and whether you use Local or API mode;
- the steps to reproduce, and what an attacker gains;
- no real API keys, chats or personal data. Use synthetic values.

You will get an acknowledgement within 7 days. A fix or mitigation is targeted within 30 days for high-severity
issues, and you will be credited in the changelog unless you prefer otherwise.

## Supported versions

Only the latest release on `main` receives security fixes.

## Security model in one page

| Area | What ISHTI does |
|---|---|
| **Network exposure** | The backend binds to `127.0.0.1` only. Remote access is opt-in (Telegram bot or an ngrok tunnel) and needs a key or session. Every HTTP and WebSocket request is checked for Origin and Host (DNS-rebinding defence). |
| **Approvals** | Anything that sends, deletes, spends, installs or controls the PC is tier **T2** and waits for the owner's explicit yes. That yes is single-use and tied to the exact draft and recipient. **T3** actions (for example wiping a drive) are refused outright. |
| **Untrusted content** | Web pages, files, emails and chat messages are fenced as data and can never authorise an action. A message telling ISHTI to "skip confirmation" is refused. |
| **Code execution** | The terminal skill runs one allow-listed executable without a shell, and asks first for anything that can run code. Skill creation asks **before** model-written code is generated or run. |
| **Web fetching** | All page reads go through `tools/web/fetch.safe_get`: public hosts only, every redirect re-checked, size capped (SSRF defence). No headless browser. |
| **Inbound messaging** | WhatsApp and Telegram control accept only the owner's allow-listed numbers or IDs (exact international match). Telegram pairing codes expire and are burned after 5 wrong guesses. Unknown senders get no reply. |
| **WhatsApp transport** | WAHA runs in ISHTI's own container on `127.0.0.1`, with an API key the container only sees as a SHA-512 hash. Dashboard and Swagger are off. Sends are idempotent: an approved send is attempted once and never silently retried. |
| **Secrets** | Model API keys and the WAHA key live in the Windows Credential Manager or an environment variable, never in config, logs or git. The local API token file is owner-only (protected DACL). |
| **Kill switch and audit** | The stop control cancels running work at the next step. Every action is written to a local audit log with who started it. |
| **Privacy** | `tools/privacy_report.py` lists every host the code can reach and why. Known telemetry endpoints are blocked even inside skills with broad network scope. |

## Your data: copy, retention, erase

- **Copy of everything:** Settings → Privacy → **Export everything** (or `POST /api/privacy/export-all`) writes one
  ZIP with every store in the data folder, your saved chats and a readable memory export, locked to your Windows
  account. Delete it when you no longer need it - it is the most sensitive file on the PC.
- **Retention:** once a day ISHTI removes what is temporary by design - uploads older than 30 days, channel
  voice-note leftovers older than 7 days, deleted chats past their undo window. Telegram voice notes are deleted as
  soon as they are transcribed. Chats, memories, notes and the ledger are kept until you delete them.
- **Forget one thing:** "forget that", "forget everything about Ali", or Learning → Memory → Forget.
- **Erase everything** (deliberately manual, so no bug or injected instruction can trigger it):
  1. Quit ISHTI (tray → Quit).
  2. Delete the `data` folder inside the ISHTI folder, and `%LOCALAPPDATA%\ISHTI` (logs).
  3. In **Windows Credential Manager → Windows Credentials**, remove the entries named `ishti`,
     `ishti-sensitive-storage`, `ishti-model-provider`, `ishti-waha` and every `ISHTI:` entry (saved logins).
  4. WhatsApp: on your phone, **Linked devices → log out** the ISHTI device; then
     `docker rm -f ishti-waha` and `docker volume rm ishti_waha_sessions`.
  5. If you used a cloud model, its provider's own retention applies to what was already sent - check their policy.

## Known limitations

- ISHTI is not a sandbox. File and document readers run in separate processes with limits, but this is not an
  operating-system security boundary.
- WhatsApp access is unofficial (the WhatsApp Web protocol through WAHA). It can get a number banned. Use a number
  you can afford to lose, or leave it off.
- Any process running as the same Windows user is trusted, as with every desktop application.
