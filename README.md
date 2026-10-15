<p align="center">
  <img src="assets/ishti_logo_dark.png#gh-dark-mode-only" alt="ISHTI: Personal AI Assistant" width="460" />
  <img src="assets/ishti_logo.png#gh-light-mode-only" alt="ISHTI: Personal AI Assistant" width="460" />
</p>

# ISHTI: a personal AI assistant for Windows

ISHTI is a personal assistant for your Windows PC. You can type to it or talk to it. It answers questions, keeps
reminders and notes, finds files, opens apps, reads and writes documents, and translates to and from Urdu and Pashto.
It asks before it does anything that cannot be undone, such as deleting a file or sending a message.

ISHTI is **personal only**: one owner, one PC, one set of chats and memory.

Created by **[Ishtiaq Ahmed](https://github.com/ishtiaq-ahmed-dev)**. Licensed for non-commercial use only
(see [License](#license)).

## Business Desk

Business Desk is an opt-in workspace for one business owner on this PC. It observes accepted invoice, lead and sales
CSV data, finds overdue balances and neglected opportunities, explains the evidence, and consults the owner before
preparing local follow-up drafts. Outcome receipts teach it which categories deserve attention. It does not send
customer messages or control spending. Continuous observation can run independently through a current-user Windows
worker while the PC is awake and the user is signed in. Open **Business Desk** in the sidebar and follow
[the setup and operating guide](docs/BUSINESS_DESK.md). No business data is imported automatically.

## Choose how ISHTI thinks

Every request takes one path: safety guards and fixed rules first (no model), then at most one model call that
picks a skill or answers directly. Each skill still passes its approval gate. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Choose Local or API before installation (`install.bat --provider api` is the lightweight path).
The first-run card and saved provider choice are implemented. API credentials are currently read from the configured
environment variable or saved through the setup card to the OS credential store. They are never written to config or logs.

| Mode | What runs the AI | You need | Your messages go to |
|---|---|---|---|
| **Local** | A model on your own PC, through [Ollama](https://ollama.com) | An NVIDIA graphics card (8 GB minimum, 16 GB recommended), about 25 GB of disk | Model inference stays local; requested network tools use their declared services |
| **API** | A cloud model you pay for, through your own API key | Internet and an API key. No graphics card needed | The provider you chose (OpenAI, Anthropic/Claude, Google Gemini or OpenRouter) |

The decision stage uses at most one model call. Skills, translation and optional background memory extraction can
make additional calls; total cost depends on the complete task and the selected provider. Your key is read
from an environment variable and is never written to the config file or the logs.

Web search and any skill that fetches a web page use the internet in both modes. Each skill declares its network
use in its manifest (`scope.net`).

### What leaves this PC

That is a claim in a README until something checks it, so something does.
`venv\Scripts\python tools\privacy_report.py` audits the revision you are running and writes
[docs/PRIVACY_REPORT.md](docs/PRIVACY_REPORT.md): every host the code can reach and why, the telemetry
endpoints refused even inside a skill declaring `scope.net: ["*"]`, the loopback-only bind of every server,
the absence of a desktop-capture path, and an explicit list of what the report does **not** cover. How well
it works, and on exactly which source hash, is in [docs/EVIDENCE.md](docs/EVIDENCE.md).

## What you need

- Windows 10 or Windows 11
- **Local mode:** an NVIDIA graphics card (8 GB minimum; 16 GB for the best results) and about 25 GB of free disk space
- **API mode:** an internet connection and an API key from one provider
- Internet, for the first install

## Install

1. **Get the project.** Click **Code -> Download ZIP** on the GitHub page, or run `git clone`. Put the folder in a short
   path such as `C:\ISHTI`.
2. **Double-click `install.bat`.** It installs the Python packages, and in Local mode it also installs Ollama and
   downloads the models. The first run can take 20 to 40 minutes. If it stops, run it again; it continues where it
   stopped.
3. **Double-click `start.bat`** to start ISHTI.

### API mode, in short

Set your key as a Windows environment variable named `ISHTI_API_KEY` (or the name set in `api.api_key_env`), then set
the provider in `ishti/config.json` under `llm`:

```json
"llm": {
  "provider": "api",
  "api": { "provider": "anthropic", "base_url": "", "model": "claude-haiku-5-5", "api_key_env": "ISHTI_API_KEY" }
}
```

`provider` can be `openai`, `anthropic`, `gemini` or `openrouter`. Any other OpenAI-compatible endpoint can be set in
`base_url`. You can also override the provider for one run with the environment variable `ISHTI_LLM_PROVIDER=api`.

## First use

Try asking:

1. "What is the weather today?"
2. "Remind me to call Ali tomorrow at 5 pm."
3. "Find my files named invoice."
4. "Translate 'good morning, how are you?' into Urdu."
5. "Write a short, polite email asking for a day off."

## Safety

- **Permission levels.** Each skill has a tier. Read-only skills run freely. Anything that changes data or talks to
  another person waits for your yes. Blocked actions never run.
- **Audit log.** Every action is recorded with who started it.
- **Kill switch.** The stop control cancels running work at the next step.
- **Untrusted content.** Text from web pages, files and messages is treated as data, never as instructions.
- **Microphone off by default.** Voice control is off until you turn it on.
- **No screenshots of your desktop.** ISHTI never captures your screen. `ui_read` lists the named controls of a
  window, and `ui_act` clicks or types into one exact control after you say yes. Secret fields are refused. (The
  browser skill does save images of the *web pages* it visits — that is page content, not your screen.)

See [docs/SAFETY.md](docs/SAFETY.md) for the full model. ISHTI is not a sandbox: the file and document readers run in
separate processes with limits, but this is not an operating-system security boundary.

## Known limitations

This is an early public release. Please read these before you rely on it.

- **Accuracy is only partly measured.** The 300-task suite passed **292 tasks in live mode** on
  2026-10-04 — a run that has not been repeated since the tree changed. Voice accuracy in any language,
  and accuracy on your own real data, are **not measured at all**. Read [docs/ACCURACY.md](docs/ACCURACY.md)
  for exactly what is and is not measured before you rely on a number. Local models in particular can
  overstate what a file format can do. Check important answers.
- **Not every skill is verified on a live account.** WhatsApp (through an unofficial web bridge), Google sign-in,
  and some desktop automations were tested only offline or with synthetic data.
- **WhatsApp is unofficial.** The bridge uses the WhatsApp Web protocol. That breaks WhatsApp's terms and can get an
  account banned. Use a number you can afford to lose, or leave it off.
- **Uploaded files are not encrypted at rest yet.** Encryption helpers exist but are not wired into every upload path.
- **One owner only.** Memory is not isolated between several people or clients in this release.
- **Windows only** for now. Local mode is tested on one NVIDIA card; other hardware is unverified.

## Troubleshooting

**The window does not open.** Wait 20 seconds, then start ISHTI again. Still nothing? Run `stop.bat`, then `start.bat`.
If it keeps happening, run `install.bat` again.

**"Ollama not found" (Local mode).** Install Ollama from <https://ollama.com/download>, then run `install.bat` again.

**"No API key" (API mode).** Set the environment variable named in `api.api_key_env`, then restart ISHTI.

**The model is too big for my graphics card (Local mode).** Choose a smaller model in Settings -> Models & voices.

**ISHTI answers slowly.** Close games and other programs that use the graphics card, or use a smaller model.

**Logs.** They are in `%LOCALAPPDATA%\ISHTI\logs`. The installer log is `install.log` in the ISHTI folder.

**Health check.** Run `venv\Scripts\python tools\install.py --check` from the ISHTI folder.

**Shut down.** Use the **Shut down** button in the app, or close the window.

**Uninstall.** Delete the ISHTI folder. In Local mode, models are stored by Ollama; remove Ollama from Windows Settings ->
Apps to delete them too.

## Documentation

| Read this | For |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | Install, run, and test details |
| [docs/SAFETY.md](docs/SAFETY.md) | Permissions, limits, and the audit record |
| [docs/PRIVACY_REPORT.md](docs/PRIVACY_REPORT.md) | What this build sends, what it refuses, and what the report does not cover |
| [docs/EVIDENCE.md](docs/EVIDENCE.md) | The test, eval and per-skill results for one source hash |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How ISHTI is built |
| [docs/ACCURACY.md](docs/ACCURACY.md) | What accuracy is measured, and what is not |
| [docs/SKILLS.md](docs/SKILLS.md) | The skill catalog and how to add a skill |
| [docs/API_CONTRACT.md](docs/API_CONTRACT.md) | The HTTP and WebSocket contract |
| [docs/TESTING.md](docs/TESTING.md) | Every test type, and the one command that reports them |
| [CHANGELOG.md](CHANGELOG.md) | What changed, by version |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute and how to reach the maintainer |

## Contributing

Contributions are welcome: bug reports, skills, tests, documentation and translations. Start with
[CONTRIBUTING.md](CONTRIBUTING.md). Questions and ideas belong in GitHub Discussions; bugs belong in Issues.

## Interoperability and reliability

ISHTI can expose its native skill catalogue to a local MCP host and read portable `SKILL.md` instruction
guides. Execution is opt-in for exact T0 names; other catalogue entries require the owner's normal
ISHTI approval flow. See [interoperability](docs/INTEROPERABILITY.md).

The [October 13 audit fixes](docs/AUDIT_FIXES_2026-10-13.md) cover bounded boot recovery, Windows
disconnect logging, rotating Ollama output, connection cleanup and fresh test evidence. Model/voice
soak, signed installers and customer validation are separate release gates.

## License

ISHTI is released under the **[PolyForm Noncommercial License 1.0.0](LICENSE)**. You may read, run, modify and share
it for any **non-commercial** purpose, including personal, research, educational and hobby use. **Commercial use is not
permitted** without a separate written agreement with the author. Contact the author through
[GitHub](https://github.com/ishtiaq-ahmed-dev).

Commercial adoption and contributor ownership still require the review in [commercial readiness](docs/COMMERCIAL_READINESS.md).

Third-party software and their licenses are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
Interface components include [Rare UI](https://rareui.com), used under its attribution and license terms.

## Author

**Ishtiaq Ahmed** created ISHTI. Source and issues: <https://github.com/ishtiaq-ahmed-dev/ISHTI>.
