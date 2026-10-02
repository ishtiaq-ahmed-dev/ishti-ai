# SKILLS: what ISHTI can do, and how skills are built

Verified against `ishti/skills/*/manifest.json` on 2026-10-02: **124 skills** have a manifest, **121 `working`**, **3
`disabled`** (`desktop_control`, `internet_reach`, `security_scan`). Tiers (MEASURED by counting manifests): **T0: 68,
T1: 41, T2: 15, T3: 0**. 124 manifests name a `test_file`; 12 set `laya: true` (the rest are matched by their own
deterministic triggers and are not offered to the Laya router). Directories in `ishti/skills/` without a manifest
(`legacy`, `_drafts`) are not counted as skills.

How a skill is chosen is in [ARCHITECTURE.md](ARCHITECTURE.md) section 3; what the tiers and scopes mean is in
[SAFETY.md](SAFETY.md).

## The skill contract

A skill is a folder `ishti/skills/<name>/` with:

- `manifest.json`: `name`, `version`, `description`, `route` (a plain sentence Laya compares requests with),
  `priority` (lower runs earlier among deterministic triggers), `laya` (true = offered to the Laya router),
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
| Computer Control | 1 | `desktop_control` (T1) [disabled] |
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
| System & Desktop | 9 | `apps` (T1), `automation` (T1), `desktop` (T1), `meta`, `screen_control` (T2), `screen_understand`, `security` (T2), `system`, `terminal` (T2) |
| Utilities & Daily | 4 | `speedtest`, `travel` (T1), `utilities`, `weather` |
| Web & Internet | 6 | `browser` (T2), `internet_reach` (T1) [disabled], `online_fallback` (T2), `web_fetch`, `web_search`, `webtools` (T1) |
| Writing & Text | 4 | `legaldoc` (T1), `pressrel`, `textstats`, `writing` |

### T2 skills (always ask first)

`browser`, `contacts`, `email`, `gemini`, `gitops`, `online_fallback`, `screen_control`, `security`, `sendlater`,
`skill_creator`, `skillsmith`, `terminal`, `trading` (paper trading only; no real broker exists), `whatsapp`,
`workflows`.

### Skills with broad reach (from their manifest `scope`)

- Unrestricted network (`net: ["*"]`): `browser`, `deep_research`, `dev`, `digest`, `find_leads`, `ledger`, `markets`,
  `shopping`, `utilities`, `web_fetch`, `web_search`, `webtools`.
- May launch apps or drive mouse/keyboard (`apps: true`): `apps`, `capcut`, `desktop`, `desktop_control`,
  `screen_control`, `terminal`, `video`.
- `desktop_control` is disabled and also flagged `x_remote_forbidden` (never reachable from a remote channel); it needs
  the external `cua-driver`, which is not installed (`CUA_FEASIBILITY.md`).

## Where the longer descriptions are

| Document | What it is | Generated by |
|---|---|---|
| [SKILL_CATALOG.md](SKILL_CATALOG.md) | One row per skill from the live registry, with spec-domain mapping (124 loaded, 121 implemented, 3 partial/broken, as of 2026-10-02 06:38) | `tools/gen_skill_catalog.py` |
| [../FEATURE_CATALOG.md](../FEATURE_CATALOG.md) | Per-skill and core-feature status: works / partial / broken / unknown / MISSING | `tools/gen_feature_catalog.py` |
| [build-history/skills/SKILLS_INVENTORY.md](build-history/skills/SKILLS_INVENTORY.md) | The 1,000+ item catalogue ledger (BUILT / BUILT-CORE / PARTIAL / BLOCKED), 2026-09-28 | hand-kept; a dated snapshot |
| [build-history/skills/SKILLS_ROADMAP.md](build-history/skills/SKILLS_ROADMAP.md) | The 35-domain comparison and batch log, 2026-09-28/29 | hand-kept; a dated snapshot |
| [../REPO_FEATURE_MATRIX.md](../REPO_FEATURE_MATRIX.md) | Every feature of the five integrated repositories and its disposition | audited by `tools/audit_matrix_status.py` |

The two generated catalogs are the source for per-skill descriptions; this page deliberately does not copy them, so it
cannot drift from them. If a generated catalog and a manifest disagree, the manifest wins.

## Known gaps worth knowing

- `NEEDS.md` N26 (2026-09-30) recorded 119 of 121 manifests without `test_file` and 10 skills without a self-check;
  every manifest now names a `test_file` (counted today), but I did not re-run the 120 per-skill suites for this
  document. Their last recorded state is in `TEST_STATE.json` (written by `tests/run_skill.py`; read on 2026-10-02):
  120 features recorded, **117 green and 3 red** - `web_search` (2026-10-01 22:25), `deep_research` (2026-10-01 22:22,
  timeout) and `shopping` (2026-09-30 14:24). Those three need the live network and a free GPU; I did not re-run them.
- Some skills need things this machine does not have (Home Assistant, ComfyUI, Ghostscript/Tesseract, a Telegram bot
  token, the `cua-driver`). Those gaps are listed with the exact blocker in `NEEDS.md` and `md files/NEEDS.md`.
