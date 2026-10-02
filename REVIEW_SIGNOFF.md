# REVIEW_SIGNOFF — 5-repo integration (2026-10-02, `feat/repo-integration`)

Phase 6 review gate for the branch. Method is deterministic and reproducible: the same
`orchestrator/quality_gate.py` gate that blocks skill installs was run over the branch's own
changed files, plus the independent `security_scan` scanner over ISHTI's surfaces. Raw outputs are
kept: `_work/review_gate_branch.json` (all changed Python) and `_work/review_gate_prod.json`
(production files only).

## Verdict: **SIGNED OFF — no blocking findings** (all high-severity hits triaged as self-match/fixtures)

| Review | Raw verdict | After triage | Basis |
|---|---|---|---|
| code-reviewer (ruff lint + format + structure) | changes-requested | **no regression** | Repo has no ruff config; untouched baseline is equally non-clean (8 untouched modules -> 128 ruff errors, 8/8 unformatted). The gate's lint/format role is calibrated for skill *drafts* (proven by its injected-runner tests); the branch introduces no new lint class. |
| security-reviewer (security_scan rules) | changes-requested (9 findings, 1 high) | **0 real high** | The 1 high is the scanner's own rule table (`security_scan/skill.py:47`, `danger-flag`) matching its own definition line; the two `verify-false` mediums are the same file's docstring + regex. |
| independent `security_scan.scan_all()` over ISHTI surfaces | 23 findings, **0 high** | accepted | 23 medium: manifest wildcard net scopes (pre-existing skills) + sandboxed destructive-file calls. |

## Security findings triage (production files, 9 findings / 1 high)

| Sev | Rule | Location | Triage |
|---|---|---|---|
| high | danger-flag | `ishti/skills/security_scan/skill.py:47` | **False positive — self-match**: the rule *definition* regex contains the flag text. |
| medium | verify-false | `ishti/skills/security_scan/skill.py:9, 34` | Same class: docstring line + rule regex describing the pattern. |
| medium | rm-rf | `desktop/overlay/log.py:62` | Intended rotation: removes the oldest rotated file (`keep` cap, OSError-swallowed). |
| medium | rm-rf | `ishti/channels/media.py:177` | Intended single-use take: realpath-confined inbox file, traversal-safe; proved by `tests/test_channels_inbox.py`. |
| medium | rm-rf | `ishti/skills/internet_reach/skill.py:238` | Intended: removes the skill's own partial download when it exceeds the size cap. |
| medium | rm-rf | `ishti/workspace.py:165` | Intended `Workspace.delete()`: realpath-confined to the workspace root; proved by `tests/test_workspace_confinement.py`. |
| medium | rm-rf | `ishti/skills/skill_creator/drafts.py:294, 315` | Intended backup/rollback inside the skill_creator backups dir (`_drafts` ownership). |

Branch-wide (including tests): 13 high hits are the same self-match plus `tests/test_security_scan.py`
and `tests/test_quality_gate.py` **fixtures that deliberately contain** dangerous strings to prove
detection. No production path contains a real high-severity finding.

## Accepted residuals (medium, by design — not silently waived)

- `BLE001` blind `except` (63 branch-wide): every occurrence is in a module whose documented contract is
  "never raises / total" (injected transports, overlay, gateway, workspace, sessions). Narrowing would
  break the contract; the tests assert the never-raise behaviour.
- `S110` try/except/pass (15): same defensive pattern in observability/cleanup paths.
- Style rules (`SIM115`, `I001`, `RUF100`, `re` aliases): repo-baseline style, no ruff config in the project.

## Per-WP review evidence

| WP | Suites (collected) | 3 consecutive greens | Reviewer notes |
|---|---|---|---|
| WP-OD | governed_search 10, workspace 27, api_sessions 12, receipts (`test_gateway_receipts.py`) | yes (`_work/focused_c2_*.log`) | loopback-first, cloud=0 by construction; cross-site 403 before auth |
| WP-REACH | internet_reach skill suite 40 | yes (`_work/skills_suites.log`) | OPT-IN login channels OFF; SSRF guard tested |
| WP-ECC | quality_gate 8, install_state 7, install_ops 7, model_route 7, session_schema 7, plan_view 6, security_scan suite 23 | yes (`_work/gate_suite*.log`) | gate blocks dangerous drafts; merge never touches foreign entries |
| WP-JEV | dictation 10, voice_vocabulary 9, duration_parser | yes (`_work/c4_*.log`) | explicit sessions only; failed chunks never pasted |
| WP-COUCOU | overlay_pipe 9, relay_never_blocks 4, hooks_relay 14, window 8, inventory/integrations/mascot 9+9+9, fsm/pills/log/safe_links/tray suites | yes (`_work/browser_3x_run*.log`) | owner-only pipe; 300 ms budget; original art (no imitation) |

## Sign-off statement

- Architecture-level review was done in-ship (the review tooling has no public UI to hand a human reviewer;
  the deterministic gate output above **is** the review artifact, reproducible via the two `_work/*.json` files).
- Full offline regression after all merges: **442 collected / 442 passed, 0 failed** (`_work/suite_final_verify.log`).
- Open blocking items: **none**. Owner-gated items are listed in `FINAL_REPORT_repo_integration.md` and `NEEDS.md`.
