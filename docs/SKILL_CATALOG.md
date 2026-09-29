# SKILL CATALOG — every skill from the spec's domains 21-35 (Part A4)

Generated from the live Skill Registry (`ishti/skills/registry.py`) on 2026-09-29 16:07. **119 skills loaded**, 119 implemented, 0 partial/broken.

Statuses: **implemented** = loaded + passing self-check/tests · **partial** = exists with named gaps · **planned** = Tier-3/optional, design note present · **blocked** = loads broken (see NEEDS.md).

## Domain 21 — Desktop & PC Control

| Skill / module | Status | Notes |
|---|---|---|
| ✅ apps | implemented | medium risk, v1.0.0 — Opens desktop apps from a fixed alias list or PATH (never through a shell), or focuses an open window. Wraps d |
| ✅ automation | implemented | medium risk, v1.0.0 — "When X, do Y" rules the owner sets in plain words: a new file in a folder, battery low, disk space low, CPU o |
| ✅ desktop | implemented | medium risk, v1.0.0 — Desktop & OS control: minimize/maximize/close windows, clipboard, screenshots and screen reading (local vision |
| ✅ meta | implemented | low risk, v1.0.0 — About ISHTI itself: skill health (runs every skill's self-check), what it logged as missing in NEEDS.md, a per |
| ✅ screen_control | implemented | high risk, v1.0.0 — Real mouse, keyboard and scroll control of this PC: click at coordinates or on the text you name (the screen i |
| ✅ screen_understand | implemented | low risk, v1.0.0 — Look at any window, region or the whole screen and answer questions about it: 'what does the error on my scree |
| ✅ security | implemented | high risk, v1.0.0 — Password strength (zxcvbn) and breach check (Have I Been Pwned range API - only 5 characters of a hash leave t |
| ✅ system | implemented | low risk, v1.0.0 — Live CPU, RAM, GPU/VRAM and disk status of this PC (psutil + nvidia-smi). |
| ✅ terminal | implemented | high risk, v1.0.0 — Sandboxed terminal/script execution (B5): commands run in a restricted subprocess (no shell interpolation, wor |

## Domain 22 — Application Integration & API Control

| Skill / module | Status | Notes |
|---|---|---|
| ✅ chat | implemented | low risk, v1.0.0 — General conversation and questions answered by the local LLM, with exact facts and semantic memory injected. C |
| ✅ contacts | implemented | high risk, v1.0.0 — Google Contacts housekeeping in the owner's Google-signed-in Chrome profile: finds duplicate contacts with Goo |
| ✅ email | implemented | high risk, v1.0.0 — Gmail in the owner's Chrome Profile 10 (signed in to Google): list or summarise unread mail, read one email, s |
| ✅ whatsapp | implemented | high risk, v1.0.0 — Reads and writes WhatsApp messages in WhatsApp Web, inside the owner's own Chrome 'Profile 9' (already logged  |

## Domain 23 — Real-Time Streaming & Event Processing

| Skill / module | Status | Notes |
|---|---|---|
| 🟠 Real-time event bus (design note, Tier 3) | planned | Design: watchdog + health loop already poll; an asyncio pub/sub bus with rate limits can subscribe skills to file/email/calendar events (I2). Tier 3: deferred until the owner asks for live streaming. |

## Domain 24 — Multi-Agent Coordination

| Skill / module | Status | Notes |
|---|---|---|
| ✅ Multi-agent orchestrator (supervisor + 10 specialists) | implemented | ishti.agents — C1-C9; self-check + tests/test_prompt3_skills.py |
| ✅ deep_research | implemented | low risk, v1.0.0 — Explicitly requested research reports: several search angles, parallel page reading, relevance filtering, cite |
| ✅ gemini | implemented | high risk, v1.0.0 — Generates images and videos with Google Gemini (the owner's Pro account) in the owner's main Chrome profile: o |
| ✅ learning | implemented | medium risk, v1.0.0 — Inspect and manage learned lessons, repeated mistakes, feedback, and proactive learning settings. |
| ✅ memory | implemented | medium risk, v1.0.0 — Exact facts: where the owner's projects/files/folders are, project status, remembered facts. Unknown locations |
| ✅ owner | implemented | medium risk, v1.0.0 — Owner commands: audio on/off ('mute', 'read that'), 'who am I', 'what do you know about me' (dumps saved facts |
| ✅ relationships | implemented | medium risk, v1.0.0 — Relationship memory graph: family, friends, preferences. 'this is my brother Ahmed' creates a person with the  |

## Domain 25 — Multitasking & Parallel Execution

| Skill / module | Status | Notes |
|---|---|---|
| ✅ Async task manager (priority queue, cancel cascade, GPU semaphore, checkpoints) | implemented | ishti.taskmgr — D1-D5; self-check + tests/test_prompt3_skills.py |
| ✅ taskctl skill (list/pause/resume/cancel by voice) | implemented | ishti/skills/taskctl — D4 |
| ✅ calendar | implemented | medium risk, v1.0.0 — ISHTI's own calendar (no Outlook account on this PC): add events (daily/weekly repeats) with a clash warning a |
| ✅ digest | implemented | low risk, v1.0.0 — The daily brief: weather, next prayer, today's reminders, markets watchlist, top headlines and screen time, ga |
| ✅ docs | implemented | medium risk, v1.0.0 — Documents, PDF and Office: merge/split/compress PDFs, extract PDF tables to Excel, Word<->PDF through Microsof |
| ✅ focus | implemented | medium risk, v1.0.0 — Focus sprints (Pomodoro): start a timed sprint on a task, a countdown the background loop checks, a spoken ann |
| ✅ lifelog | implemented | medium risk, v1.0.0 — Personal logs (tracking only, never medical advice): water, sleep, exercise, habits with streaks, and screen t |
| ✅ planners | implemented | medium risk, v1.0.0 — The local model writes and saves to Documents\ISHTI: flashcards (Anki CSV), quizzes, study plans, YouTube lect |
| ✅ reminders | implemented | medium risk, v1.0.0 — Reminders and timers ('remind me in 20 minutes to...', 'at 5pm', 'tomorrow at 9', 'set a timer for 10 minutes' |
| ✅ taskctl | implemented | medium risk, v1.0.0 — Background task control (D4): 'what tasks are running', 'cancel task 2', 'pause the research task', 'resume ta |
| ✅ workflows | implemented | high risk, v1.0.0 — Named webhooks the owner saves ("add webhook backup http://localhost:5678/webhook/backup") and runs by name (" |

## Domain 26 — Error Handling & Self-Healing

| Skill / module | Status | Notes |
|---|---|---|
| ✅ Resilience core (classify, retry+backoff, circuit breakers, fallback chains, checkpoints, safe mode) | implemented | ishti.resilience — G1-G3, G5-G7; watchdog.py stays the process watchdog (G4) |
| ✅ Skill failover inside agents (next skill on repeated failure) | implemented | ishti.agents._run_agent — C5 |
| ✅ braintrain | implemented | medium risk, v1.0.0 — The brain fine-tuning pipeline, conversational: how many training examples are logged, export the curated data |
| ✅ dev | implemented | medium risk, v1.0.0 — Development: run a pasted Python snippet (sandbox folder, time limit) or a project script, git status/log, com |
| ✅ skillsmith | implemented | high risk, v1.0.0 — Grows ISHTI: installs a skill from a GitHub repo or has the coder model write a new one - validates the skill  |

## Domain 27 — Observability & Monitoring

| Skill / module | Status | Notes |
|---|---|---|
| ✅ Observability core (OTel-shaped spans, p50/p95/p99, counters, error view, retention+redaction) | implemented | ishti.telemetry — H1, H2, H4; /api/metrics, /api/telemetry/* |
| ✅ Eval harness (fixed regression set, stored runs) | implemented | ishti.telemetry.run_eval — H5; POST /api/eval/run |
| 🟡 Traces in the Settings dashboard page | partial | JSON APIs ready (/api/telemetry/spans, /api/metrics); a dedicated Settings tab is UI work |

## Domain 28 — Workflow Automation

| Skill / module | Status | Notes |
|---|---|---|
| ✅ Trigger rules: 'when X, do Y' (file/battery/disk/CPU triggers) | implemented | ishti/skills/automation — the owner's existing trigger engine (I1 subset) |
| ✅ Named webhooks + schedules + reminders | implemented | ishti/skills/workflows + reminders — runs by name, cron-like scheduling |
| 🟡 Workflow engine v2: loops, parallel branches, sub-workflows, versioning, dry-run | partial | I1: parameters/templates/testing not built; design: compose taskmgr tasks (D1) as branch nodes — Tier-2 follow-up |

## Domain 29 — Knowledge Graph & Memory Management

| Skill / module | Status | Notes |
|---|---|---|
| ✅ Memory system (4 layers, conflicts, real-deletion forget, vault) | implemented | Prompt 2: ishti/arch2/memory.py + core/vault.py + docs/REQUIREMENTS_MATRIX_PROMPT2.md |
| ✅ Relationship graph (kin edges, confidence, disambiguation) | implemented | Prompt 2: ishti/rgraph.py + ishti/arch2/people.py |
| ✅ Reinforcement-like learning (lessons, feedback, mistakes, decay) | implemented | Prompt 2: ishti/learning/* (pre-existing, improved in P2) |

## Domain 30 — Security/Compliance/Governance

| Skill / module | Status | Notes |
|---|---|---|
| ✅ Redaction + audit trail + secrets in keyring | implemented | ishti/core/logutil + audit.py + core/settings secrets (Prompts 1-2) |
| 🟡 PII detection layer for free text | partial | regex redaction in logs/spans; a dedicated PII-classification pass is Tier-2 follow-up |
| 🟠 RBAC (multi-user) | planned | Design: single-owner system; an owner/assistant role table over audit+scopes would be the entry point. Not needed today. |

## Domain 31 — Advanced AI & ML

| Skill / module | Status | Notes |
|---|---|---|
| ✅ QLoRA fine-tuning pipeline (offline, eval-gated) | implemented | orchestrator/finetune.py (Prompt 2); training run BLOCKED on peft/datasets/bitsandbytes |
| ✅ Laya typed-decision router + nomic embeddings | implemented | ishti/router.py; ishti/semantic.py |

## Domain 32 — Human-Agent Interaction & UX

| Skill / module | Status | Notes |
|---|---|---|
| ✅ Clarifying questions + confirmation gates + progress narration + interruption (barge-in) + kill switch | implemented | Prompt 1 resolver (T3), skills.ask_first (T2), dispatcher narrate/killswitch; tests from P1 |

## Domain 33 — Edge & IoT

| Skill / module | Status | Notes |
|---|---|---|
| 🟠 Edge & IoT (MQTT/ESP devices) | planned | Design: an MQTT adapter skill subscribing device topics into the event bus (23) with the same T2 gates. The owner's setup has no IoT hardware yet — deferred. |

## Domain 34 — Blockchain & Web3

| Skill / module | Status | Notes |
|---|---|---|
| 🟠 Blockchain & Web3 (wallet read-only, chain lookups) | planned | Design: a read-only chain explorer skill (balance/tx lookup via a public API) behind the standard scope/net allow-list. No wallet keys, no signing; deferred until requested. |

## Domain 35 — Autonomous Operation

| Skill / module | Status | Notes |
|---|---|---|
| 🟡 Autonomous scheduled operation (standing rules, automation skill, self-healing restarts) | partial | ishti/skills/automation + health.py + watchdog.py cover scheduled actions and recovery; a full autonomy loop with goal ownership is Tier 3 |

## Additional domains (the owner's skill set beyond the spec's 21-35 list)

### Files & Storage
| Skill | Status | Notes |
|---|---|---|
| ✅ fileops | implemented | medium risk — File operations: batch rename with preview and undo, zip/7z make and extract (zip-slip safe), duplicate finder |
| ✅ files | implemented | low risk — Finds files on this PC by name (or content with 'containing'), optional extension filter. Wraps desktop/comput |
| ✅ folders | implemented | medium risk — Questions about a folder (count/list files by type, largest, newest) and organizing a folder's loose files int |

### Web & Internet
| Skill | Status | Notes |
|---|---|---|
| ✅ browser | implemented | high risk — Drives a real (visible, isolated-profile) browser through the Playwright MCP server: the local LLM plans tool  |
| ✅ online_fallback | implemented | high risk — For jobs this PC can't do locally (heavy media and conversions): finds a free online tool (reusing ones that w |
| ✅ web_fetch | implemented | low risk — Reads a URL given in the request (direct HTTP, then the local headless browser) and answers about it with the  |
| ✅ web_search | implemented | low risk — Live internet answer: multi-provider search (Bing Lite, Wikipedia, GitHub, arXiv, Playwright fallback), reads  |
| ✅ webtools | implemented | medium risk — Web extras: download a file from a link (programs and huge files ask first), watch a page and announce when it |

### Media & Creative
| Skill | Status | Notes |
|---|---|---|
| ✅ audioclean | implemented | medium risk — Audio enhancement with FFmpeg: FFT denoise (afftdn) and/or EBU R128 loudness normalization (loudnorm) into dat |
| ✅ audiomark | implemented | low risk — Audio bookmarks (local SQLite): timestamped notes into long recordings (HH:MM or HH:MM:SS, kept exactly as giv |
| ✅ audioquality | implemented | low risk — Audio file quality report via ffprobe: exact codec, sample rate, bit rate, channels, duration, and a plain-lan |
| ✅ capcut | implemented | medium risk — Full programmatic control of CapCut and JianYing (剪映) video projects via capcut-cli: inspect drafts, create ti |
| ✅ chapters | implemented | low risk — Video chapters from measured scene changes: ffmpeg scdet (threshold 8) timestamps parsed into a YouTube-style  |
| ✅ chromakey | implemented | medium risk — Green-screen removal with ffmpeg: colorkey for images (PNG with alpha), chromakey for video (WebM/VP9 with alp |
| ✅ extract | implemented | medium risk — Full-page extraction: opens a page in the owner's main Chrome, scrolls until no new content loads (progress re |
| ✅ highlights | implemented | low risk — Candidate video highlights, MEASURED: ffmpeg per-second RMS levels (asetnsamples + astats + ametadata) rank th |
| ✅ imgcrop | implemented | medium risk — Smart cropping with PIL: center-crop to a target aspect (1:1, 16:9, 4:3...), crop a stated pixel box, or trim  |
| ✅ imgquality | implemented | low risk — Assess an image's technical quality with PIL: resolution and megapixels, aspect ratio, sharpness (Laplacian va |
| ✅ media | implemented | medium risk — Audio, video extras and images: transcribe and summarise recordings, normalise loudness, remove noise, cut sil |
| ✅ meme | implemented | medium risk — Caption an image meme-style with PIL: top/bottom text in the classic white-with-black-outline style, wrapped a |
| ✅ palette | implemented | low risk — Extract the dominant colors of an image with PIL: exact hex codes and the share of pixels behind each, plus av |
| ✅ scanqr | implemented | medium risk — QR codes, fully local: generate a QR for any text with a proper quiet zone (cv2 encoder, self-read verified vi |
| ✅ video | implemented | medium risk — Video edits with FFmpeg (NVENC on the RTX): trim, extract audio, 9:16 vertical, speed, compress, thumbnail, in |

### Finance & Commerce
| Skill | Status | Notes |
|---|---|---|
| ✅ clients | implemented | medium risk — CRM for the owner's freelance/business clients: per-client isolated memory (profile, notes, thread summaries,  |
| ✅ find_leads | implemented | low risk — Lead finder: natural-language lead requests ('find US LLC formation agencies in Texas', 'e-commerce founders i |
| ✅ ledger | implemented | medium risk — Personal money tracking: log expenses in plain words (auto-categorised), spending reports by period and catego |
| ✅ markets | implemented | low risk — Live stocks (yfinance; PSX live from the exchange, history as SYMBOL.KA), crypto, gold/silver and currency rat |
| ✅ shopping | implemented | low risk — Finds a product's price at the brand's official store and verified retailers (PK and international), cheapest  |
| ✅ trading | implemented | high risk — Paper trading on simulated money only: place paper buy/sell orders, close positions, review the paper account, |

### Utilities & Daily
| Skill | Status | Notes |
|---|---|---|
| ✅ speedtest | implemented | low risk — Internet speed test: latency and download/upload measured against Cloudflare's free speed endpoints, history o |
| ✅ travel | implemented | medium risk — Flight and hotel search through Google Flights / Google Hotels in the owner's main Chrome profile (scraper sit |
| ✅ utilities | implemented | low risk — Everyday utilities: prayer times (Karachi method, Hanafi Asr) with the next prayer, translate, define a word,  |
| ✅ weather | implemented | low risk — Real forecast from Open-Meteo (free, no key). City from the request, else the owner's city from the fact store |
