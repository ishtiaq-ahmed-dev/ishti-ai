# PLAN — from ISSUES.md + the new feature (2026-09-29)

Ordering follows the master prompt: crashes/data-loss/security → VRAM+lifecycle → routing/STT →
WhatsApp/browser → UI → new features → polish. Items already closed in the prior pass are listed as
"verified" with their evidence file and are re-run, not re-fixed.

## Already closed before this pass (re-run only)
| Area | Status | Evidence |
|---|---|---|
| Partial CPU offload (VRAM governor, ladders, Job Object, graceful shutdown) | VERIFIED-FIXED | `VERIFICATION_REPORT.md`, `tests/e2e_vram_lifecycle.py` |
| Terminal window never appears; splash with real boot stages | VERIFIED-FIXED | `desktop/`, `DESKTOP_DECISION.md` |
| 17+ skill smoke tests | VERIFIED-FIXED | `TEST_RESULTS.md` |
| Contract tests `/api/health`, `/api/ready`, `POST /api/shutdown`, `/ws/events` | VERIFIED-FIXED | `tests/contract_test.py` |

## Work packages from ISSUES.md that are still open
| WP | Issue | Severity | Owner-agent | Files | Proof test | Risk | Rollback |
|---|---|---|---|---|---|---|---|
| WP3 | Chat UI cannot render real tables (no GFM), breaks on `\|`, no copy/sticky/RTL | Med | Frontend/Desktop | `interface/app/src/components/Markdown.tsx` | `npm run build` + N8 render check | Low (render only) | `git revert` |
| WP9 | Chat upload UI missing (N9/D1–D4) | Med | Frontend/Desktop | `Chat.tsx`, `server.py` | manual + contract test | Med | `git revert` |
| WP10 | `config.json` names 19GB models not installed (N6) | Med | VRAM/Models — **owner decision** | `ishti/config.json` | `/api/status` | Med | config backup |

## This pass — new work (Section 4)
| WP | Deliverable | Owner-agent | Files (exclusive ownership) | Proof test | Risk | Rollback |
|---|---|---|---|---|---|---|
| WP1 | `chat_digest` skill core: read-only store, 24h window, map-reduce analysis, coverage table | Reports/Digest | `ishti/skills/chat_digest/{manifest.json,store.py,window.py,analyze.py,report.py,skill.py}` | `tests/test_chat_digest.py` N1–N7, N11, N12 | Low (new files, T0) | delete folder |
| WP2 | Exports: markdown / DOCX / PDF (landscape, repeating header, wrapped cells) | Reports/Digest | `ishti/skills/chat_digest/export.py` | N9, N10 | Low | delete file |
| WP3 | Chat UI: GFM-grade tables (escaped pipes, padded rows, sticky header, wrap, RTL, copy-table) | Frontend/Desktop | `interface/app/src/components/Markdown.tsx` | `npm run build`, N8 | Low | `git revert` |
| WP4 | Synthetic dataset + ground truth + N1–N12 suite | Skills-QA | `tests/test_chat_digest.py` | self | Low | delete file |
| WP5 | Re-scan after the change; update `ISSUES.md`, `TEST_RESULTS.md`, `VERIFICATION_REPORT.md`, `NEEDS.md`, `PROGRESS.md` | Integrator | docs | n/a | None | n/a |
| WP6 | Independent verification by a separate agent (tries to falsify N1–N12) | Integrator/QA | none (read-only) | its report | None | n/a |

## File-ownership rule
No two agents write the same file. WP1/WP2 are disjoint files inside one new folder; WP3 touches only
`Markdown.tsx`; WP4 touches only `tests/test_chat_digest.py`; docs (WP5) are written last by the integrator.

## Gates that must hold before READY
Zero partial offload; zero orphan processes; no terminal window; contract tests green; skill smoke tests
green; safety tiers intact; injection tests green; chat-digest N1–N12 green; every Critical/High issue
fixed or in `NEEDS.md` with exactly what is missing.

## Owner approval needed (asked in NEEDS.md, not assumed)
- Baileys/`whats-mcp` unofficial WhatsApp reader: **default OFF** (ban risk + npm supply-chain history).
- Telegram/Discord/email sources for the digest: WhatsApp only until the owner lists others.
- `budget_pct` / KV-cache / Laya-on-CPU decisions: unchanged, still open from the prior pass (N2–N4).
