# SKILLS: what ISHTI can do, and how skills are built

Counted from `ishti/skills/*/manifest.json`: **124 skills** have a manifest, **122 `working`**, **2 `disabled`**
(`internet_reach`, `security_scan`). Tiers: **T0: 68, T1: 41, T2: 15, T3: 0**. All 124 name a `test_file`.
Directories in `ishti/skills/` without a manifest (`legacy`, `_drafts`) are not counted as skills.

How a skill is chosen is in [ARCHITECTURE.md](ARCHITECTURE.md) section 3; what the tiers and scopes mean is in
[SAFETY.md](SAFETY.md).

## The skill contract

A skill is a folder `ishti/skills/<name>/` with:

- `manifest.json`: `name`, `version`, `description`, `route` (a plain sentence saying when the skill applies),
  `priority` (lower runs earlier among deterministic triggers),
  `needs` (`python`, `models`, `mcp`, `apis`), `status` (`working`, `disabled`, `broken`, `draft`, `retired`),
  `tier` (T0-T3), `scope` (`write`, `net`, `apps`), `category`, `test_file`.
- `skill.py`: `run(text, session) -> {"reply": str, "sources": [...]}` or `None` ("not mine", the dispatcher tries the
  next choice); optionally `match(text) -> bool` (a deterministic trigger); an `if __name__ == "__main__"` self-check.

A skill that fails to import or lacks a dependency is marked `broken` and skipped; nothing else breaks. Replacing a
skill means replacing its folder. Per-skill sign-off notes from the QA pass are archived under
[build-history/qa/skill-signoffs/](build-history/qa/skill-signoffs/).

Adding a skill: write the folder by hand; or ask ISHTI (`skillsmith` installs one from a repository or generates one,
`skill_creator` researches, designs, builds in a sandbox and vets it); both end in a T2 install gate. Regenerate the
per-skill test suites with `venv\Scripts\python tools\gen_skill_tests.py` after adding one (see `tests/README.md`).

## The 124 skills, by manifest category

Format: `name` for T0, `name` (T1) or (T2) otherwise; `[disabled]` where the manifest says so. The category names are
copied from the manifests as they are; several overlap (for example Finance / Finance & Commerce, Development /
Development & Code) because the manifests were written in different sessions. That is a cleanup item, not a typo here.

| Category | Count | Skills |
|---|---|---|
| Communication | 2 | `mailmerge` (T1), `sendlater` (T2) |
| Communication & Social | 4 | `chat`, `contacts` (T2), `email` (T2), `whatsapp` (T2) |
| Daily Life | 2 | `pantry`, `recipe` |
| Data | 6 | `abtest`, `chart` (T1), `dataprep`, `forecast`, `infographic` (T1), `stats` |
| Development | 2 | `devops`, `gitops` (T2) |
| Development & Code | 4 | `braintrain` (T1), `dev` (T1), `skill_creator` (T2), `skillsmith` (T2) |
| Documents & Files | 4 | `doclayout`, `pdftables` (T1), `redact` (T1), `resumeparse` (T1) |
| Files & Storage | 3 | `fileops` (T1), `files`, `folders` (T1) |
| Finance | 4 | `budget`, `goaltrack`, `networth`, `subscriptions` |
| Finance & Commerce | 6 | `clients` (T1), `find_leads`, `ledger` (T1), `markets`, `shopping`, `trading` (T2) |
| Health | 4 | `metrics`, `moodlog`, `sleeplog`, `symptomlog` |
| Health & Habits | 1 | `habits` |
| Knowledge & Intelligence | 6 | `deep_research`, `gemini` (T2), `learning` (T1), `memory` (T1), `owner` (T1), `relationships` (T1) |
| Learning | 4 | `citation`, `mnemonic`, `study`, `vocab` |
| Media & Creative | 15 | `audioclean` (T1), `audiomark`, `audioquality`, `capcut` (T1), `chapters`, `chromakey` (T1), `extract` (T1), `highlights`, `imgcrop` (T1), `imgquality`, `media` (T1), `meme` (T1), `palette`, `scanqr` (T1), `video` (T1) |
| Memory & Notes | 2 | `checklist`, `notes` |
| Office | 1 | `formula` |
| Productivity | 8 | `gantt`, `kanban`, `meetingcost`, `okr`, `prioritize`, `riskregister`, `standup`, `timeline` |
| Productivity & Planning | 10 | `calendar` (T1), `chat_digest`, `digest`, `docs` (T1), `focus` (T1), `lifelog` (T1), `planners` (T1), `reminders` (T1), `taskctl` (T1), `workflows` (T2) |
| Reasoning | 9 | `argumentcheck`, `bayes`, `calc`, `gametheory`, `montecarlo`, `optimize`, `quickcalc`, `sensitivity`, `sudoku` |
| Security | 2 | `phishing_check`, `vulnscan` |
| Security & Privacy | 1 | `security_scan` (T1) [disabled] |
| System & Desktop | 9 | `apps` (T1), `automation` (T1), `desktop` (T1), `meta`, `security` (T2), `system`, `terminal` (T2), `ui_read` (T0), `ui_act` (T2) |
| Utilities & Daily | 4 | `speedtest`, `travel` (T1), `utilities`, `weather` |
| Web & Internet | 6 | `browser` (T2), `internet_reach` (T1) [disabled], `online_fallback` (T2), `web_fetch`, `web_search`, `webtools` (T1) |
| Writing & Text | 4 | `legaldoc` (T1), `pressrel`, `textstats`, `writing` |

### T2 skills (always ask first)

`browser`, `contacts`, `email`, `gemini`, `gitops`, `online_fallback`, `security`, `ui_act`, `sendlater`,
`skill_creator`, `skillsmith`, `terminal`, `trading` (paper trading only; no real broker exists), `whatsapp`,
`workflows`.

### Skills with broad reach (from their manifest `scope`)

- Unrestricted network (`net: ["*"]`): `browser`, `deep_research`, `dev`, `digest`, `find_leads`, `ledger`, `markets`,
  `shopping`, `utilities`, `web_fetch`, `web_search`, `webtools`.
- May launch apps or drive mouse/keyboard (`apps: true`): `apps`, `capcut`, `desktop`, `terminal`, `video`.
- `ui_act` clicks and types only into one exact named control, after a yes; it is never reachable from a remote channel. It needs
  the external `cua-driver`; its availability must be checked on the target machine.

## Current skill information

The manifests under `ishti/skills/` and the dashboard's Skills view are the current catalogue.
The tables above are a dated snapshot; manifests and runtime status win if they differ.
`GET /api/skills` reports the loaded registry, including missing optional dependencies.

The retired catalogue and build-history documents are no longer required. Runtime gaps are stored in
`data/needs_runtime.md` (`ISHTI_NEEDS_LOG` can override the path). See [testing](TESTING.md) for the
collected offline suite and the separately run standalone/live checks. Credentials and integrations
must be validated on the target machine; passing offline tests does not prove a live connector works.

Each skill's suite lives in `tests/skills/<skill>/`, matching `ishti/skills/<skill>/`. `pytest.ini`
excludes that directory from collection, so those suites run only through
`venv\Scripts\python tests\run_skill.py all`, which sets `ISHTI_TEST_MODE=1`, caps each test with a
timeout, samples VRAM, and fails a suite as `SAFETY-FAIL` if the audit log gained a real T2 side
effect during a run that was supposed to be isolated. A skill folder with no test file is reported
`no-suite` and leaves the denominator rather than counting as a pass. A new skill without a suite is a
skill nobody can regress.

See [interoperability](INTEROPERABILITY.md) for the opt-in MCP catalogue and portable `SKILL.md` guides.
Instruction documents grant no permissions, and their scripts are never imported or executed.
