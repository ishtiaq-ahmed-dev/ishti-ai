# THIRD_PARTY — external repos & integrations (Part E)

Rule followed (spec E): for each repo, confirm the official URL, check licence, record
version/commit, wrap behind an adapter, add a Settings toggle + health check + tests.
A repo that cannot be positively identified is **BLOCKED with the exact question for the
owner** — never substituted with a different project.

| Integration | Official URL | Licence | Pinned | Adapter | Toggle / health | Status |
|---|---|---|---|---|---|---|
| **Flowise** | https://github.com/FlowiseAI/Flowise | Apache-2.0 (Node, MIT-licensed deps bundled) | Owner runs latest release via npx/Docker — record exact version at first deploy (`/api/v1/version` or docker tag) | `ishti/flowise_client.py` (prediction API, intent→flow mapping, circuit breaker) | Settings → Integrations → `flowise_url`, `flowise_api_key` (secret), `flowise_flows` map; health via `GET /api/v1/ping` surfaced in `/api/flowise/flows` + the flowise_flow profile check | **VERIFIED (offline paths)** — config, intent→flow pick, breaker fallback, health, and the dispatcher's flowise_flow path are tested (`ishti.flowise_client` self-check, `tests/test_prompt3_skills.py::test_flowise_profile`). A live flow call needs Flowise running locally (`npx flowise start`); the breaker degrades ISHTI to its own skills when it's down. |
| **ScrapeGraphAI** | https://github.com/ScrapeGraphAI/Scrapegraph-ai | MIT | **Not installed yet** — pin `scrapegraphai>=1.26` on install; it pulls playwright+graphsdk | `ishti/skills/find_leads/skill.py::_scrapegraph_extract` (used when importable AND `integrations.scrapegraph_enabled`); identical pipeline otherwise via the built-in regex/bs4 extractor | Settings → Integrations → `scrapegraph_enabled` (default true; auto-degrades when the package is absent); local Qwen via Ollama in the graph config | **VERIFIED with fallback** — parsing, robots compliance, politeness delay, extraction, dedupe, store, export all tested with the fallback extractor (`ishti.skills.find_leads.skill` self-check + tests). The ScrapeGraphAI path is a drop-in: install the package, the same `find()` picks it up automatically. |
| **Apodex** | **UNRESOLVED** — web search finds only the *commercial model family* (apodex.com, "Apodex 1.1", a closed product) and social posts about it; **no official open-source GitHub repository found** | n/a | n/a | none built — spec E2 forbids substituting a different project | none | **BLOCKED — question for the owner:** *Which exact repository is "Apodex"? Please give the GitHub URL (or confirm it's the apodex.com product, which is not open-source and cannot be ported). Until then nothing was integrated under that name.* |
| **Pi Desktop** | https://github.com/vastsa/pi-desktop — **CONFIRMED** as "PI-Desktop: local-first AI coding agent / desktop workspace" (GUI app: persistent projects/sessions, Plan/Goal modes, permission controls, bring-your-own model) | Verify at pin time (repo README shows a licence badge; confirm before any code reuse) | Pin the release/commit at integration time | **none built — deliberately**: it is a standalone desktop *application* (Electron-style workstation for coding agents), not a library with an API ISHTI can call. The useful overlaps (agent permissions, audit, workspace) already exist natively in ISHTI (scope.py, audit.py, taskmgr). | none | **BLOCKED — question for the owner:** *PI-Desktop is a GUI app, not an embeddable component. What do you want from it — (a) just document/use it alongside ISHTI (nothing to integrate), (b) drive ISHTI from inside it (needs its agent-API surface, which the repo doesn't expose as a stable public API), or (c) port specific behaviours (e.g. its permission prompts) as design inspiration? (c) is doable; (b) needs an upstream API.* |

## Compliance notes (E4, leads)

- robots.txt is checked per page (`urllib.robotparser`); disallowed pages are skipped.
- One polite fetch per page (0.4 s spacing), capped page budget per run, public pages only.
- No login-walled content, no CAPTCHA circumvention, no account creation.
- Every lead row carries its `source_url` and `date_found`; leads live in `data/leads.db`
  (work data), never in personal memory (Prompt 2's store).

## Add a new integration (the pattern)

1. `ishti/<name>_client.py` or a skill folder — adapter with: `configured()`, `health()`,
   breaker-wrapped calls, secrets read from the settings *secrets* store only.
2. Settings field(s) under `integrations` (+ enable toggle), surfaced in `/api/settings`.
3. Health check registered in the profile `HEALTH` map if it gates an architecture profile.
4. Tests: offline paths with injected transport; live paths documented as needing the service.
