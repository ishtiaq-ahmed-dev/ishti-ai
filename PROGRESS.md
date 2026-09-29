# PROGRESS — Master prompt: scan → plan → fix → 24h Chat Digest → verify

Session date: **2026-09-29**. Machine: Windows 11, RTX 5070 Ti 16GB, 64GB RAM.
Working tree at start: clean, `main` @ `4a6dc3e` (rollback point for every phase below).

Legend: [x] done · [~] in progress · [ ] not started · BLOCKED = needs the owner (see NEEDS.md)

## Phase 1 — Discover (read-only)
- [x] Project mapped: 121 skill folders, `ishti/` (209 .py), `interface/` (React UI + 58KB FastAPI server),
      `orchestrator/`, `desktop/`, `browser/`, `voice/`, `tools/`, `tests/` (60 files), `data/` (7.4 GB).
      Evidence: directory listings in this session; `PROJECT_MAP.md` (prior pass) still current.
- [x] Existing docs read: `ISSUES.md` (35 KB, prior audit), `NEEDS.md` (N1–N11), `VERIFICATION_REPORT.md`,
      `MODEL_DECISIONS.md`, `MAC_CHANGES.md`, `docs/PROGRESS.md`.
- [x] Secret scan of the new surface: no credentials in the files touched by this pass (only `data/api_token`,
      already git-ignored; not read into any report).
- [x] Gap confirmed by search, not assumption: **`chat_digest` did not exist** (`search_codebase` → no results),
      and the chat UI has **no GFM table support** (`interface/app/src/components/Markdown.tsx`).
- [x] Export toolchain probed: `python-docx 1.2.0` ✅, `playwright 1.63.0` ✅, `pypdfium2` ✅ (page → PNG),
      Edge + Chrome binaries present ✅, `reportlab` **missing** (fallback only, not required).

## Phase 2 — Plan
- [x] `PLAN.md` written: work packages, ownership, files, proof test, risk, rollback.

## Phase 3 — Execute
- [x] WP1 `chat_digest` core — `ishti/skills/chat_digest/{manifest,store,window,analyze,report,skill}.py`
      (6/6 module self-checks pass: store, window, analyze, report, export, skill)
- [x] WP2 exports — `export.py`: markdown, DOCX (landscape + `w:tblHeader` + RTL runs), PDF
      (**verified live: 3-page PDF produced by `msedge.exe` headless printing**, every page rasterised with
      pypdfium2 and looked at; `reports/*_chat_digest.pdf_p1..p3.png`)
- [x] WP3 chat UI tables — `interface/app/src/components/Markdown.tsx`: escaped-pipe-aware `splitRow`,
      rectangular rows, sticky header, copy-table button, wrapped cells, `dir="auto"` for RTL.
      `npm run build` (tsc -b + vite) exit 0; the served `interface/app/dist` was rebuilt with it.
- [x] WP4 tests — `tests/test_chat_digest.py`: **17 tests** (N1–N12 + 5 regressions for bugs this pass found)
      all passing, plus `tools/chat_digest_sample.py` which writes the sample reports into `reports/`.
- [x] WP5 re-scan + docs — 12 defects found by running the code, all fixed with a regression test where
      code could fix them; `ISSUES.md` CD-01..CD-18, `NEEDS.md` N12–N20, `TEST_RESULTS.md`, this file.
      Two remain OPEN for the owner/environment: CD-17 (the shared GPU makes one dispatcher test fail with
      its own `GpuBusy` message) and CD-18 (a missing hygiene test referenced by `tests/conftest.py`).
- [x] WP6 adversarial review — **the sub-agent tool returned an authentication error**, so the falsification
      pass was run by me and is labelled self-review, not independent verification: forged source ids, table
      torture, relationship invention, chat-budget coverage, owner-name config (all in `TEST_RESULTS.md`).
- [ ] WP7 commit + tag

## Bugs this pass actually found (all by running, not by reading)
| ID | What went wrong | Fix | Regression test |
|---|---|---|---|
| CD-01 | the owner's own promise outranked the client waiting on him | owner messages rank last and are labelled "you said:" | `test_regression_owner_promise_never_outranks_someone_waiting` |
| CD-02 | "by 30 Sep" (the commonest form) was not a deadline | `MONTHS` gained 3-letter abbreviations | `test_regression_dates_are_not_guessed` |
| CD-03 | **every** Urdu message raised `IndexError` | the Urdu branch of `REL_DAY` now captures its group | `test_regression_urdu_relative_day_does_not_crash` |
| CD-04 | "clause 7.3" became a 7-March deadline | document-reference blacklist + date-context rule | same as CD-02 |
| CD-05 | after that fix, "submission **deadline** 02/10/2026" lost its date | the blacklist needed a word boundary | same as CD-02 |
| CD-06 | Pashto "سبا" was in the lookup table but not in the pattern | both lists aligned | same as CD-03 |
| CD-07 | the usefulness rubric never reported a deadline | `deadline_ts` now reaches the rubric | same as CD-01 |
| CD-08 | 21 identical action rows from one chat | duplicates collapse, sources kept | `test_regression_duplicate_actions_collapse_but_keep_sources` |
| CD-09 | the PDF printed `**Window:**` literally | bullets go through the inline parser | `test_regression_html_keeps_bold_in_bullets` |
| CD-10 | the chat UI broke on `\|`, ragged rows, no sticky/copy/RTL | `DigestTable` + `splitRow` | N8 asserts source + built bundle |

## Measured numbers (not estimates)
- Analysis + render, offline, no model loaded: **10 chats 0.006 s · 50 chats 0.027 s · 200 chats 0.108 s**.
- Synthetic sample: 11 chats found, 9 analyzed, 2 skipped (each with a reason), 239 messages, 9 action items,
  1 decision, 1 injection attempt flagged and ignored, 1 chat with sensitive content, 1 media item not
  analyzed, **0 markdown table problems**.
- Routing: `python -m ishti.check` → **333/333 correct, median 0.2 ms** (329 before this pass; the 4 new
  labels are the digest's own phrasings).
- Skill contracts: `python tools/check_skills.py` → **120 skills, 232 assertions, 329 labelled requests, 0
  problems** — including "no matcher steals another skill's request" (`check my whatsap ... unread` still goes
  to `whatsapp`).
- Frontend: `npm run build` → exit 0, 2328 modules, `dist/assets/index-*.js` 1,011 kB (gzip 289 kB).

## Notes for resuming after context loss
1. Everything new lives under `ishti/skills/chat_digest/`, `tests/test_chat_digest.py`,
   `tools/chat_digest_sample.py`, `reports/`.
2. Run: `venv\Scripts\python -m pytest tests/test_chat_digest.py -q` (offline, no Ollama needed).
3. The skill never sends/replies/reacts/deletes anything — T0, read-only; sending stays T2.
4. The PDF path that works on this box is **Edge headless printing**; Playwright's own Chromium is not
   downloaded for the Python package (the code tries it first, then falls back automatically).
