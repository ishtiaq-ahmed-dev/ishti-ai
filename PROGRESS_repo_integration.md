# PROGRESS — 5-repo integration session (2026-10-01)

Master log lives in `PROGRESS.md`; this file is the repo-integration resume point (re-enter here on restart).

## Done (in mandated order)

1. Tag `pre-repo-integration` @ 0f6bb1f; branch `feat/repo-integration`; `vendor/` git-ignored (pins in `VENDOR_PINS.md`).
2. PHASE 1: all 5 official repos verified via `git ls-remote`, cloned at pinned SHAs; licenses recorded
   (hey-jev = NONE -> N36; coucou assets restricted -> original assets only); reads written to
   `docs/repos/{open-dots,ecc,hey-jev,agent-reach,coucou}.md`.
3. PHASE 1: `REPO_FEATURE_MATRIX.md` — 128 rows (PORT 36 / ADAPT 13 / IMPROVE 53 / OPT-IN 17 / SKIP-WITH-PROOF 9).
4. PHASE 2: `COMPLETENESS_AUDIT.md` run 1 — Pass A (matrix) vs Pass B (`tools/audit_repo_docs.py` -> 1,681 doc items)
   vs Pass C (`tools/audit_repo_items.py` -> 1,748 code items); **unmapped = 0**; 6 seed-list conflicts corrected.
   RE-RUN REQUIRED at project end vs newest upstream.
5. PHASE 3: environment probes measured (Docker/WSL2 running, Win11 Pro, pywebview shell); `RESEARCH.md` R6 section
   (gateway receipts, pipe ACLs + MS sources, overlay styles + MS sources, Docker isolation, login-state risks,
   vocabulary biasing, OPT-IN inventory); `IMPLEMENTATION_PLAN.md` PART 2 (P0 deviations, goals, WPs, merge order,
   acceptance gate) — **written before any feature code**.
6. `NEEDS.md` N27-N38 (9 skip proofs + hey-jev license ask + Windows-Sandbox elevation + OPT-IN enablement list).
7. `THIRD_PARTY_NOTICES.md` written.

## Done (Phase 4-6)

8. PHASE 4 build, merge order WP-OD -> WP-REACH -> WP-ECC -> WP-JEV -> WP-INTEGRATE(partial) -> WP-COUCOU -
   **complete** (15 commits). Each feature: implement -> unit tests -> feature test -> peer review -> security
   review -> integration test (full offline suite after each merge). New skills start DISABLED (T2 enable).
9. PHASE 5: `TEST_RESULTS.md` **PART 2** - per-feature suites + 3x-green logs, measured before/after numbers,
   security numbers, regression (442/442 green, `_work/suite_final_verify.log`).
10. PHASE 6: `REVIEW_SIGNOFF.md` (gate over the branch, triaged; 0 real high findings) +
    `FINAL_REPORT_repo_integration.md` (per-repo verdicts) + `COMPLETENESS_AUDIT.md` run 2 vs newest upstream
    (open-dots/hey-jev/agent-reach unchanged; ecc moved docs-only; coucou moved -> COU-23..25 dispositioned).
`FEATURE_CATALOG.md` regenerated from an extended generator (124 skills + 31 core rows, idempotent),
`docs/SKILL_CATALOG.md`, `docs/API_CONTRACT.md` §12, `THIRD_PARTY_NOTICES.md` addendum, `REPO_FEATURE_MATRIX.md`
(131 rows).

## Done (Phase 7 - status pass + wiring, 2026-10-02, commit `9e6003b`)

11. Matrix status pass: `tools/audit_matrix_status.py` maps every row to the test files that cite it;
    all 23 proving suites re-run 3x green -> **52 rows `tested`** (Evidence cells name the suite),
    10 `proved-skip`, **69 `mapped` backlog** grouped per repo in the matrix's status-pass section.
    Caught + fixed one over-claim (REACH-29 cited the T-R24 update check) and completed WP-REACH's
    proof line with 8 new channel-contract tests (T-R03/04/05/06/14/16/22/23).
12. Wiring: gateway -> `internet_reach` search channel (OD-14); session envelopes -> overlay pill
    events (ECC-09); overlay drop -> unified media inbox (COU-06/18); tray push-to-talk (JEV-02).
    Regression: full offline suite **445/445 green** (`_work/suite_after_wiring.log`); PART 3 of
    `TEST_RESULTS.md` holds the evidence; catalog generator re-run = zero diff.

## Remaining

- Owner-gated (unchanged): N36 hey-jev license ask; N39 owner-approved live-STT set; N22/N23 real
  Telegram/WhatsApp pairing; N7 microphone hardware; cua-driver install; N37 (only if Docker proves
  insufficient for the computer runtime). See `FINAL_REPORT_repo_integration.md` section "Blocked".
- 69 `mapped` rows are code work, not blockers - new capability listed per repo in `REPO_FEATURE_MATRIX.md`
  (open-dots chat/UI + connectors + runtime, ecc library/later-audit rows, hey-jev macOS-action merges,
  agent-reach login-state channels + setup tooling, coucou COU-07/25). Resume row by row via their Test IDs.

## Environment facts measured 2026-10-01 (do not re-claim unmeasured)

- Docker Desktop + WSL2 works (`docker ps` shows ishti-searxng); Windows 11 Pro; Intel Core Ultra 9 285.
- Windows Sandbox feature query needs elevation (N37) — Docker is the chosen isolation path.
- Python 3.11.9 venv; pywebview desktop shell on 127.0.0.1:8888.

## Safety invariants unchanged this session

T0-T3 + T2 explicit-yes gates, kill switch, watchdog, audit log, injection defense, per-skill scopes,
CRM isolation, per-person privacy, mic default OFF, orchestrator-only speech, ishti-* models untouched,
zero partial offload, local-only runtime (all cloud = OPT-IN, OFF).
