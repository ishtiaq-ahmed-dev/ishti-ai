<p align="center">
  <img src="assets/ishti_logo_dark.png#gh-dark-mode-only" alt="ISHTI: Personal AI Assistant" width="460" />
  <img src="assets/ishti_logo.png#gh-light-mode-only" alt="ISHTI: Personal AI Assistant" width="460" />
</p>

# ISHTI: a local-first personal AI assistant

ISHTI is a personal assistant that runs on one Windows PC. The language models run in a local
[Ollama](https://ollama.com) server, speech recognition (faster-whisper) and speech synthesis (Kokoro, Piper) run on
the machine, memory is stored in local SQLite and ChromaDB files, and there is no cloud model in the request path.
You use it from a desktop app (a window around a React dashboard), by voice, or, if you choose to pair it, from your
phone. It answers questions, searches the web, controls files and apps, keeps reminders and notes, and uses 124 skills
through one dispatcher. Actions with side effects (sending a message, deleting files, buying, running commands) stop
and wait for your explicit yes.

"No cloud at runtime" has one honest exception: skills whose job is to fetch the web (search, browsing, research) reach
the internet, and each skill's allowed network domains are declared in its manifest. Telegram, if you enable it, passes
messages through Telegram's servers. See [docs/SAFETY.md](docs/SAFETY.md).

## Quick start

On the project's PC, after the one-time setup in [docs/SETUP.md](docs/SETUP.md):

```bat
start.bat        :: opens the ISHTI desktop app (splash, then the dashboard); no console window
stop.bat         :: clean shutdown, releases the GPU
start.bat test   :: runs the offline test suite
```

The dashboard is at `http://127.0.0.1:8888`. Logs are in `%LOCALAPPDATA%\ISHTI\logs\ishti.log`. The microphone is OFF
until you switch it on.

There is no `requirements.txt` yet; the Python environment exists only as the git-ignored `venv\` folder, so a fresh
machine has to rebuild it by hand (package list in [docs/SETUP.md](docs/SETUP.md)).

## How it is built

Every input (typed, spoken, scheduled, remote) calls one function, `ishti.dispatcher.handle`. It tries the cheapest
checks first: a pending yes/no, instant reflex commands, deterministic skill triggers, then a small local router model
(Laya), then the default `chat` skill backed by the main language model. A VRAM governor keeps every model fully on the
GPU and downshifts to lighter models instead of ever splitting one across CPU and GPU.

| Architecture | What it is | Status |
|---|---|---|
| **arch1** (default) | Dispatcher cascade: reflex, rules, Laya router, 124 skills, a planner for multi-skill requests | Default, best tested |
| **arch2** | Two-pass "brain": intent router shortlists skills, the main model thinks and calls tools, a validation pass checks each tool run, the main model writes the answer; plus a four-layer memory | Implemented, switchable (config, dashboard card, or "use arch 2"); needs live Ollama |
| **Profiles** | Six named configurations in `ishti/core/architectures.py` (`simple_voice`, `router_single_agent`, `memory_augmented`, `multi_agent_orchestrator`, `flowise_flow`, `custom`) | Only `multi_agent_orchestrator` and `flowise_flow` change the request path; the other four run the arch1 path today |

The profile names `voice_lite`, `four_role`, `agent_team` and `flow_hybrid` that appear in some planning notes do not
exist anywhere in this repository.

## Documentation

| Read this | For |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Processes, request flow, architectures, memory, VRAM governor, lifecycle, desktop shell |
| [docs/SETUP.md](docs/SETUP.md) | Install, run, test, troubleshooting |
| [docs/SKILLS.md](docs/SKILLS.md) | The 124 skills by category, the skill contract, how to add one |
| [docs/SAFETY.md](docs/SAFETY.md) | Tiers T0-T3, confirmation gates, scope, kill switch, audit trail, remote control |
| [docs/API_CONTRACT.md](docs/API_CONTRACT.md) | HTTP and WebSocket contract (machine-checked by `tests/test_lifecycle_contract.py`) |
| [docs/MODEL_DECISIONS.md](docs/MODEL_DECISIONS.md) | Per-role model ladders measured on this machine |
| [docs/TESTING.md](docs/TESTING.md), [tests/README.md](tests/README.md) | Every test type and the per-skill harness |
| [docs/SETTINGS.md](docs/SETTINGS.md), [docs/LEARNING_INTERFACE.md](docs/LEARNING_INTERFACE.md), [docs/SESSION_SCHEMA.md](docs/SESSION_SCHEMA.md), [docs/UX_MOTION.md](docs/UX_MOTION.md) | Reference docs |
| [docs/THIRD_PARTY.md](docs/THIRD_PARTY.md), [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), [docs/VENDOR_PINS.md](docs/VENDOR_PINS.md) | Integrations, licenses, pinned upstream repositories |
| [NEEDS.md](NEEDS.md), [ISSUES.md](ISSUES.md) | Open owner decisions and blocked items; the verified issue log |
| [docs/build-history/](docs/build-history/README.md) | Per-session reports, audits, research and superseded docs (history, not reference) |

## Current status (as of 2026-10-02, branch `feat/repo-integration`)

What is recorded, with sources. I (the documentation pass) did not re-run the suites or start the backend.

- **Verdict at the last full report: READY WITH KNOWN ISSUES**, all remaining items owner-gated rather than code defects
  ([FINAL_REPORT_repo_integration.md](docs/build-history/repo-integration/FINAL_REPORT_repo_integration.md), 2026-10-02).
- **Offline test suite:** 445 collected, 445 passed on 2026-10-02 after the last wiring commit
  ([TEST_RESULTS.md](docs/build-history/qa/backend-2026-09-29/TEST_RESULTS.md), PART 3). Cycle-4 performance work recorded the
  suite green again afterwards ([PERFORMANCE_CYCLE4.md](PERFORMANCE_CYCLE4.md)). Today's default configuration
  collects 464 tests.
- **Per-skill harness:** 120 features recorded in `TEST_STATE.json`, 117 green and 3 red at their last run: `web_search`
  and `deep_research` (2026-10-01, network/GPU dependent) and `shopping` (2026-09-30).
- **Skills:** 124 manifests; 121 working, 3 shipped disabled by design (`desktop_control`, `internet_reach`, `security_scan`).
- **Performance (Cycle 4, MEASURED on the author's RTX 5070 Ti machine, [PERF_LOG.md](PERF_LOG.md)):** a loopback
  Ollama read dropped from 15.5 ms to 0.5 ms, `/api/system` from 150 ms to 2.5 ms, the UI JavaScript bundle on the wire
  from about 1.0 MB to 288 KB, and a skills-health check after restart from about 72-80 s to 0.56 s.
- **Not verified on this machine:** a live microphone turn (no working input device found), real Telegram/WhatsApp
  end to end (needs the owner's token and pairing), the `cua-driver` desktop-control backend (not installed), and
  anything on another PC or a smaller GPU. Windows is the only platform tested.
- **Known documentation/code gaps:** `docs/SETTINGS.md` describes `/api/settings` routes and a `/settings` page that
  are not present in the server code; `config.json` still contains absolute `D:\ISHTI\...` paths; two NEEDS files
  exist (`NEEDS.md` is the curated one, `md files/NEEDS.md` is the log ISHTI writes to at runtime; `NEEDS.md` N25).

## Credits and licenses

ISHTI is built on open-source components, listed with licenses in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
UI components in `interface/app/src/components/rare/` are from [Rare UI](https://rareui.com) (MIT + Commons Clause +
Attribution). The logo and icons in `assets/` are generated by `tools/generate_brand_assets.py`.
