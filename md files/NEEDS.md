# NEEDS: blocked or deferred work

Each entry lists what's needed, why it's blocked, and what would have to change. Items are removed when done.

## 1. Fine-tune Laya on ISHTI's own traffic
- **Need:** a `laya-typed-decisions` checkpoint fine-tuned on real ISHTI routing decisions.
- **Blocked by:** data volume. Every decision is logged to `data/ishti.db` (`route_log`). Saying **"that should have been <skill>"** or **"use the <skill> for that"** re-runs the last request with that skill and logs it with `source = correction` (a gold label). A fine-tune needs a few hundred such labels; so far there are about 25 (`ishti/check.py` `LABELLED`).
- **To change:** once there are enough, export `GET /api/route_log` (corrections are gold labels, `rule` rows are high-precision weak labels), run the upstream Kaggle notebook (`notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb` in NandhaKishorM/laya), and point `router.laya_model` at the new checkpoint. No router code change is needed.

## 2. Visual (screenshot) fallback for the browser skill
- **Need:** click by coordinates on pages whose accessibility tree is useless (canvas apps, some captcha-free widgets).
- **Blocked by:** no local vision model. `qwen2.5:14b` is text-only, so Playwright MCP's `--caps vision` tools would be driven blind.
- **To change:** pull a local VLM (e.g. `qwen2.5vl:7b`, about 6 GB) and add a `vision` role in `config.json`. When a step fails because no usable ref exists, the browser skill takes `browser_take_screenshot`, asks the VLM for coordinates, and uses the vision tools. VRAM is the constraint: 14B planner (11 GB) + VLM (6 GB) don't fit together, so Ollama will swap (about 5 s per swap).

## 3. Planner and coder can't both stay in VRAM
- **Now:** a code question loads `ishti-qwen2.5-coder:14b` and evicts the planner, so the first reply after a switch costs about 5 s.
- **Options:** use `qwen2.5-coder:7b` (both fit), or accept the swap. The config change is one line; no code change.


## 9. Semantic-memory threshold
- `ishti/semantic.py` keeps recalls with cosine distance ≤ 0.45, calibrated on a handful of samples. Re-tune it once real conversation history exists (log the distances, pick the knee).

## 10. Learn new skills instead of saying "can't" (next iteration)
- **Goal:** when no skill (and no escalation) can do an *action* request, ISHTI builds one.
- **Plan:** a `builder` skill on top of an open-source code agent. HuggingFace **smolagents** CodeAgent is Apache-2.0 and works with Ollama; Open Interpreter is AGPL, so it was avoided. The coder model writes a Python script for the task. ISHTI shows the script and what it will touch, and waits for "yes" (it runs code on this PC). It runs the script in a subprocess with a timeout, in `data/sandbox` as the working directory, then checks the result. On success it saves the script as a new skill folder (`manifest.json` + `skill.py`, with the parameters lifted out), so the next similar request routes to it directly (Voyager-style skill library).
- **Safety:** always confirmed; no deletes outside the sandbox without a second confirmation; every run logged.

## 11. Video editing like an editor (Completed with capcut-cli integration)
- **Flow:** "edit the video in my downloads" / "create CapCut project"
  1. `folders` / `capcut` lists videos or existing projects.
  2. ISHTI analyses it: faster-whisper transcript, FFmpeg silence and scene detection (`capcut detect-silence` / `detect-scenes`).
  3. It builds a real CapCut draft (`capcut-cli`): video clips, multi-track title cards, imported SRT captions, audio fades, filters, and 9:16 vertical canvas.
  4. It renders proxy previews via hardware-accelerated NVENC.
  5. It opens the project directly in the CapCut desktop app with all tracks editable.
- **Status (27 Sep):** Complete. Installed `capcut-cli` (v0.26.0) globally and Python package `capcut` (v0.1.1). Created dedicated `ishti/skills/capcut` skill and `tools/video/capcut_controller.py`, upgrading `pro.py` to write drafts via `capcut-cli` with synchronized timeline mirrors (`draft_content.json`, `template-2.tmp`, `root_meta_info.json`). Screen clicking is no longer needed because `capcut-cli` manipulates the draft store directly.

- 2026-09-26 20:14 [gap] skillsmith: couldn't generate a working 'roman_numeral_converter' skill (self-check kept failing) - asked: "create a skill that converts roman numerals to numbers and back"
## 12. Skills catalog items that need something this PC doesn't have (26 Sep)
- **Status of #10 and #11:** #10 is now the `skillsmith` skill (install from GitHub or generate; scanned, shown, self-checked, yes to install). The 14B coder often can't make a generated skill pass its own test in 3 fixes - then it refuses and logs it. `qwen-27b` (Qwen 3.5) would write better code but crashes llama-server here (CUDA "shared object initialization failed"); retry after an Ollama/driver update. #11 is done: `video/pro.py` makes the ad-style edit, writes a real CapCut project and verifies the render with Gemma 3 vision.
- **Smart home (catalog 12):** needs a Home Assistant server on the network. None found.
- **Social publishing (14), Telegram/Discord/Slack (4):** need the owner's API tokens/bot accounts. Drafting works (`planners` post drafts); posting/sending is not built.
- **AI image generation (7):** needs ComfyUI running (http://127.0.0.1:8188) - not running now. Background removal (rembg) and upscaling (Real-ESRGAN) need ~200 MB-1 GB model downloads; not installed yet.
- **PDF image recompression / OCR of scanned PDFs (8):** Ghostscript / Tesseract not installed; `read the text from <image>` uses the local vision model instead.
- **Flight/hotel search (18):** Skyscanner/Kayak block scraping; itineraries are web-grounded instead.
- **Outlook calendar/email (4):** Outlook is installed but has no account; `calendar` is local (+ .ics export) and `email` uses Gmail in Chrome Profile 10.
- **Microphone:** no input device opens (only the Bluetooth speaker's hands-free mic, MME error 1); check Windows privacy "Let desktop apps access your microphone".

## 13. Fish Audio S2 voice, on demand (27 Sep) - needs the owner
The voice roster is live on Piper (3 male + 1 female, `voice.voices`, switch by voice). S2 Pro plugs into the same
roster (engine + a 10-30 s royalty-free reference clip per voice) but is blocked on this PC:
- **CUDA Toolkit 12.8+ is not installed** (admin/UAC install - the owner's call): `winget install Nvidia.CUDA`.
  Also `pip install cmake`. VS Build Tools 2026 is present; CUDA may need `-allow-unsupported-compiler` for it.
- Then: build `rodrigomatta/s2.cpp` (`-DS2_CUDA=ON`), fetch the q4_k_m or q5_k_m GGUF (3.6-4.0 GB, Fish Audio
  Research License - personal non-commercial use OK), run its HTTP server only while Live mode is open, stream its
  PCM into `RemoteTalker`, fall back to Piper when free VRAM < ~4.5 GB.
- VRAM reality: Qwen 14B (~10 GB) + Whisper large-v3 (~1.6 GB) leave ~4 GB of 16 GB, so S2 would mostly fall
  back unless Whisper moves to large-v3-turbo or the planner model gets smaller.

## 14. Kokoro TTS Pashto G2P Lexicon (27 Sep)
- **Need:** Native Pashto speech synthesis without letter spelling.
- **Blocked by:** Kokoro's Hindi/Urdu voice (`hf_alpha`) does not possess G2P phonetic rules for Pashto-specific letters (`ښ`, `ې`, `څ`, etc.). When fed these characters, it spells out individual Arabic letter names.
- **Current status:** Whisper STT handles Pashto recognition, tokenization, and language folding (`fa` -> `ps`) accurately on CUDA float16. For speech synthesis, Pashto voice notes require Urdu phonemes or Romanized Pashto until an upstream Pashto G2P lexicon is integrated into Kokoro.

## 15. The three interaction modes: RESOLVED 28 Sep 2026 (was: verified in code, not clicked through)
- **Closed by the 28 Sep UI click-through on the real app (127.0.0.1:8888, the owner's live instance - untouched):
  Settings renders with the Whisper card showing `large-v3-turbo` (Issue 10's fix visible); Skills page shows
  **All Categories 47** (Finance & Commerce = 5 incl. the new `trading` skill); a paper order in Chat shows the
  amber T2 gate with Go ahead/Cancel and the activity graph (Router -> rule -> trading); Live voice mode with no
  microphone degrades cleanly: dialog says **"STOPPED - Microphone unavailable: Requested device not found"** with
  a working End Live button - no crash, no hang. The confirm path was verified through the real HTTP layer
  (`POST /api/execute` -> pending gate -> fill -> ledger row), see 18. Remaining unknown: Live from a *phone*
  browser (no second device on this desk) - code path is `/ws/live` + `RemoteTalker`, same one-session gate.

## 16. Barge-in/turn-taking: mic situation now MEASURED, not assumed (28 Sep 2026)
- `tools/probe_audio_devices.py` (new) enumerates every input, opens each at its advertised rate and measures real
  signal; verdict written to `data/audio_devices.json`. Measured 28 Sep: the only inputs are the Bluetooth
  "Headset (Hands-Free)" endpoints (MME/WASAPI/WDM-KS) and **both fail to open** - `PortAudioError -9999
  WdmSyncIoctl` (hands-free profile not connected). **No microphone can deliver audio on this PC right now**, so
  real-mic barge-in (wake self-check: 120 ms +~23 ms block on synthesised speech) stays code-verified only until
  a mic is connected - the probe re-runs in seconds and says so instead of guessing.

## 18. Trade execution: RESOLVED 28 Sep 2026 as PAPER-ONLY (was: does not exist)
- **Built:** `ishti/skills/trading` (T2) - a paper-trading desk on simulated money: `paper buy/sell`, `paper close`,
  `paper account`, `paper trade history`, `reset the paper account`. SQLite ledger (paper_account/orders/positions,
  same ishti.db), cash/position validated BEFORE the T2 gate so a yes can never fire an invalid order, every fill
  recorded with `note="owner confirmed"`, avg-price/cash arithmetic unit-tested, no shorting even on paper.
  Routing requires the word "paper" (priority 6; markets keeps every research question); any ask to connect a
  **real/live broker is caught and refused in code** - never attempted, never sent to an LLM - with the T3
  escalation explained. Verified end-to-end on the live app: gate in the UI (Go ahead/Cancel), `yes` ->
  "Bought 10 NVDA @ 500.00 - Paper cash now 95,000.00" (21 ms via pending), ledger row + position in ishti.db,
  account read-out, refusal, and a gated reset back to 100,000.00. Routing ground truth grew to 157/157.
- **Still true, by design:** no real broker. Going live is the T3 design below (adapter, Windows Credential
  Manager vault for credentials, risk limits from markets' 1.5% sizing, explicit final confirmation, audit row) -
  it needs the owner's approval before any of it is built.

## 17. Retire the duplicated legacy STT engine (`voice/stt.py`) - RESOLVED 28 Sep 2026
- Done: `tests/test_stt_accuracy.py` was moved onto the one production path (`ishti.voice` / `voice.stt_call`) and
  passes (EN/UR accuracy, latency, real-audio file, Pashto tokenizer + language folding); `voice/stt.py` - the
  second decode path behind the fail-fast of Issue 8 - is deleted. Nothing else imported it (grep-verified).

## 19. Sections 1-9 performance/voice/mic/Obscura pass - RESOLVED 28 Sep 2026
- **Section 1 (models)**: measured on this machine, `MODEL_DECISIONS.md` written. Planner/coder keep the custom
  `ishti-qwen2.5*` 14B models (Modelfiles backed up to `data/*.modelfile.bak`); fast classifier is Laya
  (near-zero VRAM, 0.2 ms); translator gemma3:4b on demand. `qwen3:30b-a3b` (19 GB MoE - all experts load)
  verified NOT to fit 16 GB; not adopted. Resident worst case ~12 GB = 74% of VRAM (budget 80%).
- **Section 2 (residency)**: `ishti/models.py` verified live - gemma3:4b loaded (2.88 GB), evicted, event logged;
  planner pinned; 10-min idle timers; `gpu.slot` unloads idle first under pressure.
- **Section 3 (self-heal)**: `self_heal.py` OFF by default, manual trigger only; crash resilience untouched.
- **Section 4 (monitor)**: `monitor.py` passive logging to corrections store; never triggers healing.
- **Section 5 (single voice)**: `talk.py` context gate - sub-agents silenced, orchestrator-only TTS; verified.
- **Section 6 (language)**: English bias verified incl. code-switch cases (en 0.52/ur 0.44 stays en; ur 0.78
  switches). "switch to Urdu / speak English / auto language" handled in dispatcher.
- **Section 7 (mic control)**: NEW `ishti/mic_control.py` - global mic OFF by default, persisted in
  `data/mic_state.json`, voice commands ("microphone on/off"), dashboard toggle (`/api/mic/*`), system tray
  (`desktop/tray.py`), killswitch and shutdown force mic OFF, Live sessions close on mic-off. No audio captured
  when OFF.
- **Section 8 (Obscura)**: verified on this machine (obscura 0.2.3 Windows binary in `tools/obscura/`): fetch
  markdown 0.17 s, parallel scrape 2 URLs/10 workers 1.15 s, CDP serve OK. Now the DEFAULT reader for
  `web_fetch` and `deep_research` (markdown for LLMs, --obey-robots always, no stealth, blocked pages reported
  not circumvented). Fallback chain Obscura -> requests/Playwright -> visible Chrome Profile 9 for logged-in.
- **Section 9 (perf)**: `PERF_LOG.md` - routing 0.17 ms median, planner TTFT 0.96 s, STT 114 ms, TTS 244 ms;
  perceived voice latency ~1.3 s with TTS overlapped on first tokens.
- **Verified**: `tools/verify_all.py` **91/91**; `ishti.check` routing **157/157**; check_skills 54 skills /
  136 assertions OK.

## 20. Skills expansion (owner's 35-domain catalogue) - LEDGER CREATED, batches remain
- `SKILLS_ROADMAP.md` maps every domain honestly: built / blocked-on-infra / real gap. New this pass:
  `screen_control` (OCR word-targeted clicks/typing/scrolling, T2-gated), `screen_understand` (OCR + Gemma3
  vision Q&A about the screen), `notes`, `writing` (summarize/paraphrase/bullets/expand/grammar/email/post/
  outline), `study` (flashcards/quiz/teach-me/grade), `budget` (limits with 80% warnings), `habits` (streaks),
  and the **skill lifecycle engine** (`ishti/skill_lifecycle.py`: gap discovery, practice, per-skill memory,
  report cards, refine, reversible retire). Remaining ~40 concrete skills are batched B/C/D in the roadmap;
  each batch ships through the same gates (self-check + routing labels + check_skills) so the 1,000-name
  catalogue is never faked with stubs.

- 2026-09-27 01:30 [error] chat: InternalServerError: Error code: 500 - {'error': {'message': 'llama-server process has terminated: exit status 1: cudaMalloc failed: out of m - asked: "what is 17 times 23? reply with just the number"
- 2026-09-27 01:31 [error] chat: InternalServerError: Error code: 500 - {'error': {'message': 'llama-server reported out-of-memory during startup: cudaMalloc failed: out of m - asked: "what is 17 times 23? reply with just the number"
- 2026-09-27 06:05 [gap] media: no webcam connected - asked: "record the webcam for 2 seconds"
- 2026-09-27 06:48 [error] desktop: COMError: (-2147023728, 'Element not found.', (None, None, None, 0, None)) - asked: "volume up"
- 2026-09-27 19:28 [error] dispatcher: KeyError: 'alpha' - asked: "alpha price of gold"
- 2026-09-27 19:28 [error] dispatcher: KeyError: 'beta' - asked: "beta weather in Dubai"
- 2026-09-27 19:28 [error] dispatcher: KeyError: 'gamma' - asked: "gamma flight to Istanbul"
- 2026-09-27 19:28 [error] dispatcher: KeyError: 'delta' - asked: "delta invoice for Ahmed"
- 2026-09-27 19:31 [error] probe_boom: RuntimeError: skill exploded - asked: "boom now"
- 2026-09-27 20:07 [error] health: 12 failures in the last hour - asked: "errors"
- 2026-09-27 22:15 [error] health: 633 MB free of 16303 - asked: "gpu"
- 2026-09-28 06:59 [error] health: 620 MB free of 16303 - asked: "gpu"
- 2026-09-28 07:57 [error] screen_understand: pip couldn't install rapidocr-onnxruntime: ime>=1.7.0->rapidocr-onnxruntime) (7.36.2)
Requirement already satisfied: colorama in d:\ishti\venv\lib\site-packages (from tqdm-> - asked: "auto-install"
- 2026-09-28 23:25 [error] imgquality: pip couldn't install Pillow: Requirement already satisfied: Pillow in d:\ishti\venv\lib\site-packages (12.3.0)
 - asked: "auto-install"
- 2026-09-28 23:26 [error] palette: pip couldn't install Pillow: Requirement already satisfied: Pillow in d:\ishti\venv\lib\site-packages (12.3.0)
 - asked: "auto-install"
- 2026-09-29 14:12 [error] chat: GpuBusy: The graphics card is busy: ishti-qwen2.5:14b needs about 9.8 GB and only 0.0 GB is free within the 80% GPU budget (in us - asked: "Write a python function to compute factorial of a number."
- 2026-09-29 14:46 [error] health: 575 MB free of 16303 - asked: "gpu"
- 2026-09-29 15:02 [error] dispatcher: GpuBusy: The graphics card is busy: gemma3:4b needs about 2.9 GB and only 1.4 GB is free within the 80% GPU budget (in use: qwen2 - asked: "Write a python function to compute factorial of a number."
- 2026-09-29 15:04 [error] dispatcher: GpuBusy: The graphics card is busy: gemma3:4b needs about 5.5 GB and only 1.4 GB is free within the 80% GPU budget (in use: qwen2 - asked: "Translate to Urdu: Hello, thank you for your help."
- 2026-09-29 15:49 [error] health: 116/117 loaded; broken: find_leads - asked: "skills"
- 2026-09-29 17:51 [error] dispatcher: GpuBusy: The graphics card is busy: ishti-qwen2.5:14b needs about 9.8 GB and only 0.0 GB is free within the 80% GPU budget (in us - asked: "Hello ISHTI, who are you and what GPU are you running on?"
- 2026-09-29 19:27 [error] chat: GpuBusy: The graphics card is busy: ishti-qwen2.5:14b needs about 9.8 GB and only 0.5 GB is free within the 80% GPU budget (in us - asked: "what is 17 times 23? reply with just the number"
- 2026-09-29 19:28 [error] dispatcher: GpuBusy: The graphics card is busy: gemma3:4b needs about 3.8 GB and only 0.5 GB is free within the 80% GPU budget (in use: nomic - asked: "what is 17 times 23? reply with just the number"
- 2026-09-29 20:52 [error] shopping: GpuBusy: The graphics card is busy: ishti-qwen2.5:14b needs about 9.8 GB and only 0.0 GB is free within the 80% GPU budget (in us - asked: "find the cheapest current price: ny WH-1000XM5"
- 2026-09-29 20:53 [error] dispatcher: GpuBusy: The graphics card is busy: gemma3:4b needs about 3.8 GB and only 0.0 GB is free within the 80% GPU budget (in use: qwen2 - asked: "search the web for current weather in London"
- 2026-09-29 21:00 [error] dispatcher: GpuBusy: The graphics card is busy: ishti-qwen2.5:14b needs about 9.8 GB and only 1.3 GB is free within the 80% GPU budget (in us - asked: "Search about Sony WH-1000XM5"
- 2026-09-29 21:01 [error] shopping: GpuBusy: The graphics card is busy: ishti-qwen2.5:14b needs about 9.8 GB and only 1.3 GB is free within the 80% GPU budget (in us - asked: "find the cheapest current price: ny WH-1000XM5"
- 2026-09-29 21:02 [error] chat: GpuBusy: The graphics card is busy: ishti-qwen2.5:14b needs about 9.8 GB and only 0.7 GB is free within the 80% GPU budget (in us - asked: "Write a python function to compute factorial of a number."
- 2026-09-29 21:02 [error] dispatcher: GpuBusy: The graphics card is busy: gemma3:4b needs about 3.8 GB and only 0.7 GB is free within the 80% GPU budget (in use: qwen3 - asked: "search the web for current weather in London"
- 2026-09-29 21:44 [error] health: 5 failures in the last hour - asked: "errors"
- 2026-09-29 23:37 [error] health: ConnectionError: HTTPConnectionPool(host='127.0.0.1', port=8890): Max retries exceeded with url: /search?q=ping&forma - asked: "search"
- 2026-09-30 00:22 [error] dispatcher: GpuBusy: The model could not be loaded onto the graphics card (404). - asked: "Search about Sony WH-1000XM5"
- 2026-09-30 00:22 [error] shopping: GpuBusy: The model could not be loaded onto the graphics card (404). - asked: "find the cheapest current price: ny WH-1000XM5"
- 2026-09-30 00:22 [error] chat: GpuBusy: The model could not be loaded onto the graphics card (404). - asked: "Write a python function to compute factorial of a number."
- 2026-09-30 00:22 [error] web_search: GpuBusy: The model could not be loaded onto the graphics card (404). - asked: "Translate to Urdu: Hello, thank you for your help."
- 2026-09-30 00:22 [error] deep_research: GpuBusy: The model could not be loaded onto the graphics card (404). - asked: "Translate to Urdu: Hello, thank you for your help."
- 2026-09-30 08:44 [error] chat: OSError: [Errno 22] Invalid argument - asked: "summarize: files named invoice in D:/ISHTI/docs"
- 2026-09-30 08:45 [error] dispatcher: OSError: [Errno 22] Invalid argument - asked: "no wait, I meant the March invoice, not the first one"
- 2026-09-30 08:46 [error] shopping: OSError: [Errno 22] Invalid argument - asked: "compare price of usb-c cable 100W"
- 2026-09-30 09:59 [error] chat: NotFoundError: Error code: 404 - {'error': {'message': "model 'qwen3:30b-a3b' not found", 'type': 'not_found_error', 'param': None, 'co - asked: "Reply with the exact word: PONG"
- 2026-09-30 09:59 [error] web_search: RuntimeError: cannot schedule new futures after interpreter shutdown - asked: "Reply with the exact word: PONG"
- 2026-09-30 10:00 [error] dispatcher: GpuBusy: The graphics card is busy: gemma3:4b needs about 3.8 GB and only 1.3 GB is free within the 80% GPU budget (in use: qwen2 - asked: "Reply with the exact word: PONG"
- 2026-09-30 10:02 [error] dispatcher: GpuBusy: The graphics card is busy: gemma3:4b needs about 3.8 GB and only 1.2 GB is free within the 80% GPU budget (in use: qwen2 - asked: "Reply with the exact word: PONG"
- 2026-09-30 13:53 [error] planners: ScopeError: the planners skill isn't allowed to write C:\Users\PC\Documents\ISHTI\Flashcards - general 2026-09-30.csv (outside its w - asked: "make flashcards"
- 2026-09-30 14:03 [gap] skillsmith: couldn't generate a working 'pdf_to_text' skill (self-check kept failing) - asked: "create a skill that converts pdf to text"
- 2026-09-30 14:04 [gap] skillsmith: couldn't generate a working 'temperature_conversion' skill (self-check kept failing) - asked: "build me a skill that converts temperatures"
## Q10 (new, 2026-09-30): QA harness vs live-instance GPU contention
Running skill QA batches while the live ISHTI instance (`python -m ishti 8888`) holds loaded models
causes repeated GpuBusy failures in skills that need the vision/planner models (desktop screen-understand,
travel, skillsmith). VRAM observed at 14.9/16.3 GB during contention; the app's own idle-unload freed it
to 0.7 GB after ~4 min. Options for the owner: (a) accept "run QA batches when idle" as policy,
(b) add a QA-mode VRAM handshake so the live instance preempts unloading, or (c) a config budget
reserving a slice for QA runs. No code changed for this yet.

- 2026-09-30 19:01 [error] web_search: GpuBusy: The graphics card is busy: ishti-qwen2.5:14b needs about 9.7 GB and only 1.0 GB is free within the 80% GPU budget (in us - asked: "send hi to bhai"
Update to Q10 (same day): measured coexistence limit — with qwen3:14b (9.0 GB) loaded, gemma3:4b
(2.7 GB, the translator) cannot load under the 80% budget (only ~1 GB free). Language-locked users
on arch2 get correct-but-untranslated replies; any arch2 + arch1-role mix flaps. See
PERFORMANCE_REPORT.md for numbers and the proposed fixes (translator gate is the big one).

- 2026-10-01 08:39 [gap] skill_creator: a blocked (T3) action: live trade - ISHTI will not do this, and no confirmation unblocks it - asked: "place a live trade on my broker account"
- 2026-10-01 17:23 [error] scanqr: pip couldn't install zxingcpp: ERROR: Could not find a version that satisfies the requirement zxingcpp (from versions: none)
ERROR: No matching distribution found for zxingcpp
 - asked: "auto-install"
- 2026-10-01 19:58 [error] planners: JSONDecodeError: Expecting value: line 1 column 1 (char 0) - asked: "make flashcards"
- 2026-10-01 21:38 [error] dispatcher: GpuBusy: The graphics card is busy: gemma3:4b needs about 2.7 GB and only 0.2 GB is free within the 80% GPU budget (in use: qwen3 - asked: "brain training status"
- 2026-10-01 23:37 [error] dispatcher: GpuBusy: The graphics card is busy: ishti-spark2.5:4b needs about 8.0 GB and only 1.3 GB is free within the 80% GPU budget (in us - asked: "Search about Sony WH-1000XM5"
- 2026-10-02 00:03 [error] dispatcher: GpuBusy: The model could not be loaded onto the graphics card (500). - asked: "how much RAM is free"
- 2026-10-02 13:30 [error] chat: GpuBusy: The graphics card is busy: ishti-spark2.5:4b needs about 8.0 GB and only 0.0 GB is free within the 80% GPU budget (in us - asked: "what is 7 plus 5?"
- 2026-10-02 13:31 [error] dispatcher: GpuBusy: The graphics card is busy: gemma3:4b needs about 2.7 GB and only 0.0 GB is free within the 80% GPU budget (in use: nomic - asked: "what is 7 plus 5?"
- 2026-10-02 13:31 [error] chat: GpuBusy: The graphics card is busy: ishti-qwen2.5-coder:14b needs about 9.9 GB and only 0.0 GB is free within the 80% GPU budget  - asked: "explain what a mutex is in two sentences"
- 2026-10-02 13:40 [error] browser: UnsupportedOperation: fileno - asked: "internet reach"
- 2026-10-02 13:41 [error] health: 7 failures in the last hour - asked: "errors"