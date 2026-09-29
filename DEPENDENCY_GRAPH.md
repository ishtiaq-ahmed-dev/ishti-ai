# DEPENDENCY_GRAPH.md - what needs what, and the safe test order (2026-09-30)

Edges come from the manifests (`needs.*`, `scope.*`) and the import graph read this pass
(dispatcher -> skills/__init__ -> skills; skills -> audit, facts, stores). A layer depends on the
ones above it. **Test order = top to bottom**: foundations first, real-world side effects last,
always under `ISHTI_TEST_MODE=1`.

## Layers (safe test order)

### Layer 0 - pure compute (no I/O, no models, no net)
abtest, argumentcheck, bayes, braintrain(status views), calc, chart, citation, checklist, chapters,
dataprep, doclayout, formula, gametheory, gantt, highlights, infographic, imgcrop, kanban, legaldoc,
mailmerge, media_rename(in media), mnemonic, montecarlo, okr, optimize, palette, pdftables, pressrel,
prioritize, quickcalc, recipe, redact, resumeparse, riskregister, scanqr, sensitivity, speedtest,
standup, stats, sudoku, textstats, timeline, vocab, writing, argumentcheck
- Depends on: nothing. Fail here = the skill itself is broken.

### Layer 1 - local stores (SQLite/files only)
audiomark, budget, calendar, checklist, contacts(reads), digest, focus, goaltrack, habits, ledger,
lifelog, moodlog, networth, notes, pantry, reminders, shopping(local compare), sleeplog,
study, subscriptions, symptomlog, taskctl, travels(local plans), vocab, weather-cache
- Depends on: Layer 0 + the data schema. **Test before anything that writes to real stores:
  redirect stores to scratch dirs.**

### Layer 2 - intelligence services (models, memory, learning)
chat, deep_research(planning), digest, extract, find_leads, memory, meta, mnemonic, planners,
relationships, rgraph consumers, study, textstats-LLM, video(pro), writing, braintrain(trainer)
- Depends on: Layer 0/1 + `ishti/vram.py` (models must fit; `/api/ps` shows `size_vram == size`)
  + memory stores. Tests need Ollama up, or the honest-degradation path asserted.

### Layer 3 - platform integrations (net / OS / apps)
apps, audioclean, audioquality, automation, browser(T2), capcut, chart-exports, dev, devops,
desktop(T1), docs, fileops, folders, gitops(T2), imgquality, markets, media, networth-quotes,
screen_control(T2), screen_understand, security(T2), system, terminal(T2), trading(T2),
web_fetch, web_search, webtools, weather(net)
- Depends on: Layer 2 + OS resources. Network skills need offline-failure assertions
  (graceful, no hang). T2 skills here must show the gate under test mode.

### Layer 4 - real-world side effects (T2: sending, trading, system changes)
browser, contacts(send), email, gemini, gitops, online_fallback, screen_control, security,
sendlater, skillsmith, terminal, trading, whatsapp, workflows
- Depends on: everything above + the T2 gate + audit. **Tested LAST, always under
  `ISHTI_TEST_MODE=1`** (block at last step + audit record), with scripted yes/no.

### Cross-cutting foundations (needed by everything; test FIRST of all)
- CORE-01 router, CORE-02 dispatcher/gate, CORE-05 VRAM governor, CORE-06 lifecycle,
  CORE-09 audit, CORE-10 injection defense, `ishti/test_mode.py` (this pass)

## Example dependency chains (evidence)
- whatsapp -> browser (Playwright MCP tab), memory (context), rgraph (relationships), T2 gate, audit
  - manifest: needs.mcp=[browser], tier T2, scope net=["web.whatsapp.com"]
- chat_digest -> memory/rgraph (relationships), whisper (voice notes), pdf/docx exporters; T0 read-only
- trading -> markets (quotes), planner (research), T2 gate (paper orders confirm), audit
- reminders -> clock/scheduler, TTS announcement, audit; fires must not starve the UI (CORE-13)
- deep_research -> web_search + web_fetch + planner; grounds dates (ground_dates), cites sources
- weather -> open-meteo.com (net), fact store for the owner's city; offline -> honest failure
- fileops/files -> scope guard (`data`/named folders only), T1 confirmations for renames, audit

## Safe test order (concrete)
1. `pytest tests/test_backend_watch.py tests/test_desktop_bootstrap.py` (harness-independent)
2. Layer 0 suites: `python tests/run_skill.py abtest,bayes,calc,stats --runs 1`
3. Layer 1 with scratch stores, then Layer 2 with Ollama up (VRAM assertion on)
4. Layer 3: network skills with `--timeout 90` and offline-failure cases; OS skills read-only first
5. Layer 4 (T2): test-mode only; verify `TEST-MODE BLOCK` rows in the audit log
6. Scenarios (`tests/scenarios/*.yaml`) - Prompt 3 runs them concurrently
