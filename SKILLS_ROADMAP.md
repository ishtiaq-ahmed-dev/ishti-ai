# SKILLS_ROADMAP — the 35-capability-domain comparison (2026-09-28)

The owner asked for every capability in the researched 1,000-skill / 35-domain catalogue, with
"not a single one missed". This file is the honest ledger: what ISHTI already does, what a
**real** implementation would take on this machine (16 GB VRAM, offline-first, T0–T3 tiers),
and the build order. A capability is only "done" when it has code + a self-check + routing
coverage — lists of names without that would be noise.

## Ground truth after this pass

- **54 skills** loaded (`tools/check_skills.py`: 54 skills / 136 assertions / 157 labelled requests, OK)
- **Skill lifecycle engine** (`ishti/skill_lifecycle.py`): discovery of gaps, practice
  (self-check runs), per-skill `.memory.md`, report cards, refine, reversible retire — the
  "never stops learning" loop around skillsmith's create/install gates.
- Routing **157/157 at 0.2 ms median**; every new skill T2-gated where it acts, T0 where it reads.

## Domain-by-domain status

| # | Domain | Status in ISHTI | Real gaps (next batches) |
|---|--------|-----------------|--------------------------|
| 1 | NLU & Generation | **Mostly built**: chat, writing (summarize/paraphrase/bullets/expand/grammar/email/post/outline), tone via Laya | paraphrase-mode presets, meeting-transcript digest (planners has lecture notes) |
| 2 | Voice & Audio | **Built deep**: wake word, VAD, STT en/ur/ps, TTS roster, voice clone, barge-in, voice notes, echo filter | music ID (needs a service), audio-event detection (alarms) |
| 3 | Vision & Image | **Built**: OCR (Windows+RapidOCR), screen understanding, chart/vision via Gemma3, media skill, dedupe, meme-ish edits via capcut | segmentation, depth, 3D — need model downloads (~200 MB–1 GB each, NEEDS 12) |
| 4 | Video | **Built**: capcut-cli drafts, NVENC renders, captions, silence/scene detect, transcripts, verify render | highlight auto-clipping, content moderation |
| 5 | Knowledge & Retrieval | **Built**: SearXNG search, Obscura default reader, deep_research with relevance filter + citations, markets, weather, travel, web_fetch | patent/court DBs (API tokens), hotel/flight booking (scrape-blocked, NEEDS 12) |
| 6 | Reasoning | **Built via planner**: math, decision tables, pro/con, planning steps in chat + planner | Monte-Carlo/bayesian helpers (nice-to-have scripts) |
| 7 | Code & Dev | **Built**: dev skill (scaffold/run script/services), coder model, skillsmith codegen, git basics | CI/CD authoring, k8s/terraform templates (templates only, still useful) |
| 8 | Data Analysis & Viz | **Partial**: extract→spreadsheet, ledger/clients reports, markets tables | pandas profiling, chart images (matplotlib present) — batch B |
| 9 | Documents | **Built**: docs (letters, invoices, presentations, PDF forms), OCR of scans, extraction | redaction workflow, citation formatter — batch B |
| 10 | Task & Project | **Built**: reminders, task tracker (10 topics), focus sprints, workflows, automation triggers | kanban view in HUD, gantt export — batch C |
| 11 | Calendar & Scheduling | **Built**: local calendar + .ics, reminders, time-zone utilities | Google Calendar OAuth (needs owner tokens, NEEDS 12) |
| 12 | Email & Comms | **Built**: email via Gmail (Profile 10), drafting, WhatsApp sends + voice notes, clients CRM | send-later scheduling, thread summarizer — batch B |
| 13 | Smart Home / IoT | **Blocked**: no Home Assistant server on the network (NEEDS 12) | whole domain waits for infrastructure |
| 14 | Health & Wellness | **Partial**: habits (streaks/today/nudges built), reminders for meds | symptom log UI, water reminders wired to habits nudge — batch B |
| 15 | Financial | **Built**: budget ledger (limits/80% warnings), ledger skill, markets, paper trading (T2), currency in utilities | bill-splitting, subscription tracker — batch B |
| 16 | Learning & Education | **Built**: study (flashcards saved for review, quiz, teach-me-eli5, grade), planners flashcards→Anki CSV, quiz, study plan, lecture notes | spaced-repetition scheduler wiring (reminders ↔ flashcards boxes) — batch B |
| 17 | Travel | **Built**: travel research grounded, itinerary draft, utilities (time/currency), packing list | live prices (blocked), packing-list-from-weather link — batch C |
| 18 | Shopping | **Built**: shopping skill, price watch, deal scan | pantry/expiry tracker — batch C |
| 19 | Social & Relationships | **Built**: relationships graph (kin edges, contradictions), contacts, clients CRM, birthday reminders | social post scheduling (planners drafts exist) |
| 20 | Security & Privacy | **Built**: security skill (HIBP, breach checks), password gen, killswitch, scopes, audit log, untrusted-content fences, T2/T3 gates | phishing URL checker — batch B |
| 21 | Desktop & PC Control | **Built**: desktop skill (windows/clipboard/volume/processes/winget/power), apps launcher, files/folders search, screenshots + OCR screen reading, lifelog app usage | **NEW: screen_control (click/type/scroll/hotkey via OCR word-targeting, T2-gated)**, macro recording — batch C |
| 22 | App & API Integration | **Built**: MCP browser profiles (9/10), Playwright, webhook-ish automations, obscura CDP | OAuth manager for Google/Outlook — needs owner tokens (NEEDS 12) |
| 23 | Streaming & Events | **Built (scale-appropriate)**: automation triggers (file appears), page-watch, price-watch, clipboard history, health loop | Kafka-scale streaming is out of scope for a single PC — documented, not faked |
| 24 | Multi-Agent | **Built**: planner decomposition, parallel topics (tested 10-way), orchestrator-only voice, sub-agents return compressed text, deep-research workers | agent reputation/AB tests — batch D |
| 25 | Multitasking & Parallel | **Built & tested**: INFLIGHT condition per session, task tracker, follow-up waits, background tasks with announced answers, gpu.slot serialization | cancellation propagation edge cases — hardening |
| 26 | Error handling & Self-heal | **Built**: health loop, watchdog, killswitch, self_heal (manual-only), reflex containment, error log + NEEDS feed | auto-restart policies stay manual by design (Section 3) |
| 27 | Observability | **Built**: trace events → dashboard, audit log, route_log, model_events, PERF_LOG, monitor (passive correction store) | trace export/OpenTelemetry — batch D |
| 28 | Workflow Automation | **Built**: workflows skill, automation triggers, focus sprints, page/price watchers | visual workflow editor in HUD — batch D |
| 29 | Knowledge Graph & Memory | **Built**: facts (UPSERT), semantic ChromaDB, rgraph (temporal relationship graph), clients isolation, corrections learning, redaction | memory forgetting policy — batch C |
| 30 | Security/Compliance/Gov | **Built**: scope.py per-skill, T2/T3, killswitch, untrusted fences, audit, secrets redaction (ftlog), mic control (default OFF) | DLP scan of exports — batch B |
| 31 | Advanced AI/ML | **Built**: braintrain/finetune QLoRA pipeline with regression gate, Laya router, model residency manager | speculative decoding etc. — Ollama-side, not ours |
| 32 | Human-Agent UX | **Built**: HITL pending gates, clarification questions, identity correction humour, narration, single-voice gate, mic indicator | gamification — deliberately skipped (owner didn't ask) |
| 33 | Edge & IoT | Same as 13 — blocked on infrastructure | |
| 34 | Blockchain/Web3 | **Read-only**: markets covers prices; **by design no wallet/keys on this PC** — T3-class, would need explicit owner request | |
| 35 | Autonomous Operation | **Built (bounded)**: self-configuration (model residency), watchdog, skill lifecycle, ftlog learning, NEEDS self-log | full autonomous mode conflicts with T2 gates — by design |

## Re-verification pass (2026-09-28)

All gates re-run fresh: check_skills **61/146/157 OK**, routing **157/157 @ 0.2 ms**, verify_all
**98/98**, plus a new live-fire smoke (`tools/smoke_reverify.py`) that routes and *executes* real
owner phrasings end-to-end: **33/33 after fixing 2 defects it caught** (quickcalc "go to bed now"
matcher gap; checklist reset deleting the list instead of un-ticking it — both now covered by
extended self-checks).

## Batch E (2026-09-28)

Six local-compute skills on already-installed libraries: **forecast** (exact least-squares
trend/projection/moving-average/seasonality), **chart** (matplotlib bar/line/histogram PNGs),
**palette** (PIL dominant colors with exact hex + pixel shares), **imgquality** (Laplacian
sharpness, blur verdict, brightness/contrast), **abtest** (two-proportion z-test with a
direction-stating verdict), **prioritize** (Eisenhower triage with visible inferred flags).
Manifests declare import names (`PIL`, not the pip name `Pillow`) after the loader's package
check flagged the mismatch. Labels: **231 labelled requests**, check_skills **81/174/231 OK**,
routing **231/231 @ 0.2 ms**, verify_all **121/121**, smoke **81/81 live cases**.

## Pull verification + Batch D (2026-09-28)

The owner's pull was verified first: three regressions found and fixed (stats' forward-slash
paths silently breaking four routing labels; arch2's consolidation template crashing on every
turn — `{reply[:800]}` — so nothing was ever learned; the arch2 test file reading the owner's
real chat store). Then six skills: **redact, devops (CI/CD + IaC templates), gitops (safe
read-only subset, writes T2-gated, history rewriting refused in code), sendlater (email queue on
the reminders store, T2 at send time — nothing sends while the owner is away), mailmerge (CSV +
template → per-row drafts), pdftables (pdfplumber → CSV)**. New hardening: git-flavoured "log"
only; owner's fuzzy verb match no longer claims path/branch questions; git ask "add …" can no
longer steal subscriptions. Labels: **207 labelled requests**, check_skills **75/162/207 OK**,
routing **207/207 @ 0.2 ms**, verify_all **115/115**, smoke **70/70 live cases**.

## Batch C (2026-09-28)

Seven more skills: **vocab, recipe, subscriptions, networth, sleeplog, moodlog, stats** — plus
routing-hardening the gates forced: habits no longer claims "track asset …" (letter-only habit
names), desktop's process-kill never matches "kill myself" (moodlog's distress safety gate owns
that path at priority 10), vocab beats the dictionary only for words the owner saved. Labels:
183 labelled requests, check_skills **69/155/183 OK**, verify_all **107/107**, smoke **55/55**.

## Batch F + G (2026-09-28/29)

Batch F: **dataprep** (CSV quality/outliers/dedup), **vulnscan** (AST-based Python checks —
bandit absent; f-string SQL included), **optimize** (scipy LP/1-D minimization + cost-phrase
knapsack), **timeline** (text events → chronological timeline), **formula** (sympy Excel-formula
help), **resumeparse** (PDF/text resume → structured JSON). Batch G: **metrics** (weight/BP/sugar
log), **symptomlog** (severity log + doctor summary), **goaltrack** (savings goals with %),
**standup** (yesterday/today/blockers note), **meetingcost** (computed meeting cost — the assert
caught my own wrong expected value before the engine was wrong once), **gantt** (task list →
Mermaid gantt). Fix the gates forced: desktop's window rule no longer steals math expressions
("maximize 5x + 3y"), formula's EXPLAIN restricted to formula-shaped text, symptomlog/metrics DBs
lazily resolved so self-check tempdirs actually apply. Labels 231 → **266**; smoke 81 → 104.

## Batch H (2026-09-29)

Six more local skills: **imgcrop** (PIL aspect/box/trim crop → data/crops, originals untouched),
**meme** (top/bottom outlined captions, auto font fit, canvas fallback → data/memes),
**audioquality** (ffprobe codec/rate/bitrate/channels/duration + verdict), **kanban** (SQLite
todo/doing/done board with the WIP>3 warning — the board double-listing bug the gates missed is
fixed and regression-asserted), **okr** (objectives + key results, exact average roll-up),
**riskregister** (5x5 matrix, computed 1-25 scores, top-by-score, mitigations recorded without
claiming the risk is already reduced). Routing fix forced by the gates: desktop's generic
"close <window>" rule stole "close risk X" — riskregister outranks it (priority 11). Labels
266 → **287**, check_skills **99/199/287 OK**, routing **280/287 (all 7 misses Laya-only labels
under --no-laya)**, verify_all **139/139**, smoke **121/121**.

## Batch I (2026-09-29)

Six skills probed before building: **scanqr** (this machine's cv2 5.0 wheel QR *decoder* returns
None even on its own encoder output — verified live, so decoding uses the newly installed
zxing-cpp; the encoder works and every generated QR is re-decoded before claiming success),
**audioclean** (ffmpeg afftdn + EBU R128 loudnorm to data/clean, failures shown verbatim),
**chapters** (ffmpeg scdet → YouTube-style chapters + Mermaid), **sudoku** (givens-consistency
pre-check + full post-verify; "1"×81 is proven unsolvable instead of "solved"), **bayes**
(explicit % parsing — "prior 1%" was silently 100% until the self-check caught it; the 16.67%
base-rate anchor is asserted), **montecarlo** (seed 7 reproducibility asserted; dice mode
validated against the analytical mode 7 and mean 3.5n). Routing fixes the gates forced:
calc's "solve …" no longer steals sudoku grids (sudoku priority 13). Labels 287 → **303**,
check_skills **105/208/303 OK**, routing **296/303 (7 Laya-only misses under --no-laya)**,
verify_all **145/145**, smoke **128/128**.

## Batches J, K, L (2026-09-29) — the buildable backlog is exhausted

Batch J (writing/docs): **legaldoc** (rental/NDA/employment templates with visible [FILL]
blanks and a mandatory not-legal-advice notice), **pressrel** (press release / product
description / FAQ from stated facts verbatim), **doclayout** (pdfplumber per-page layout:
title by font size, heading-like lines, ruled lines, tables; scanned pages reported as scans).
Batch K (media/data): **pantry** (expiry tracker), **audiomark** (timestamped notes),
**highlights** (per-second RMS via asetnsamples+astats+ametadata — the naive astats parse
measured nothing, caught by the self-check), **chromakey** (green → transparent alpha verified
on real pixels), **infographic** (exact stats printed on the chart). Batch L (reasoning):
**gametheory** (mixed equilibrium verified by indifference — formula never trusted),
**sensitivity** (sympy-exact elasticity table, f=0 honest), **argumentcheck** (fallacy patterns
named with trigger phrase, candidates-not-proof). Routing hardening the gates forced:
screen_control's KEY rule no longer treats any word as a hotkey ("press release" is safe).
Labels 303 → **329**, check_skills **116/223/329 OK**, routing **322/329 (7 Laya-only misses
under --no-laya)**, verify_all 153/153 then **155/156** with one timing-flaky multitopic test
passing on re-run, smoke **143/143**.

What remains is genuinely blocked or needs the owner: model downloads (segmentation, depth,
YOLO, FER, rembg — NEEDS 12), cloud APIs (music ID, hotels/flights, Google OAuth tokens),
Home-Assistant-class infrastructure, wallet keys (T3 by design), and LLM-strength features
(vision description, moderation) that already route through Gemma3. No remaining PARTIAL item
is buildable locally today.

## Counting honestly

The catalogue's ~1,000 "skills" mostly decompose into: (a) capabilities ISHTI already ships as
the 54 skills above, (b) model/infra features (quantization, streaming infra) that live in
Ollama/the server rather than skills, (c) real gaps listed per domain. Rather than inflate a
number, the roadmap tracks **gaps**. Current honest gap count: **~40 concrete skills** across
batches B (utilities: pandas-profile, chart-maker, redact, phishing-check, bill-split,
subscription tracker, thread summarizer), C (kanban view, pantry tracker, memory-forgetting,
macro recorder, gantt export), D (agent AB tests, trace export, visual workflows). Each batch
follows the same gate: manifest + match/run + self-check + routing labels + check_skills green.

## Why not generate all of them in one pass

Mass-generating 1,000 stubs would (a) fail this project's own auditors (check_skills matches
manifests against behaviour; check_libraries against installed APIs), (b) poison routing —
every new regex competes for requests, which is exactly how Issue 9/11-class bugs are born,
and (c) violate the T0–T3 trust model the project runs on. The lifecycle engine exists so the
remaining batches can be built (and even generated by skillsmith + refined) *without* that risk,
in owner-approved batches, with the routing eval as the regression gate.
