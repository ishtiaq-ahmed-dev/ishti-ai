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

## This pass — new work (Section 4) — ALL DONE 2026-09-29
| WP | Deliverable | Owner-agent | Files (exclusive ownership) | Proof test | Result |
|---|---|---|---|---|---|
| WP1 | `chat_digest` skill core: read-only store, 24h window, map-reduce analysis, coverage table | Reports/Digest | `ishti/skills/chat_digest/{manifest.json,store.py,window.py,analyze.py,report.py,skill.py}` | N1–N7, N11, N12 + 6 module self-checks | **PASS** (17/17 tests) |
| WP2 | Exports: markdown / DOCX / PDF (landscape, repeating header, wrapped cells) | Reports/Digest | `ishti/skills/chat_digest/export.py` | N9, N10 | **PASS** (3-page PDF via Edge headless, pages rasterised and inspected) |
| WP3 | Chat UI: GFM-grade tables (escaped pipes, padded rows, sticky header, wrap, RTL, copy-table) | Frontend/Desktop | `interface/app/src/components/Markdown.tsx` | `npm run build`, N8 | **PASS** (tsc + vite exit 0, bundle asserted) |
| WP4 | Synthetic dataset + ground truth + N1–N12 suite | Skills-QA | `tests/test_chat_digest.py` | itself | **PASS** (17 tests) |
| WP5 | Re-scan after the change; `ISSUES.md`, `NEEDS.md`, `TEST_RESULTS.md`, `PROGRESS.md` | Integrator | docs | — | **DONE** (10 defects found and fixed, 4 open/blocked documented) |
| WP6 | Independent verification by a separate agent | Integrator/QA | none (read-only) | its report | see `VERIFICATION_REPORT.md` §7 |
| WP7 | Commit + rollback tag | Integrator | git | `git log` | see `PROGRESS.md` |

### Defects the work packages surfaced (all fixed, each with a regression test)
CD-01 owner promise outranked the client's request · CD-02 "30 Sep" not parsed · CD-03 **every Urdu message
crashed** (`IndexError`) · CD-04 "clause 7.3" read as a date · CD-05 the fix for CD-04 ate real dates
("dead**line**") · CD-06 Pashto word in the map but not the pattern · CD-07 usefulness never saw a deadline ·
CD-08 21 duplicate action rows · CD-09 `**` visible in the PDF · CD-10 the chat UI's table renderer.
Details and evidence: `ISSUES.md` (Session 2026-09-29).

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
