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

## 6. Custom "hey ishti" wake word
- **Now:** hands-free listening works (`ishti/wake.py`) with openWakeWord's pretrained **"hey jarvis"** model. Replies are spoken, and each exchange shows up in the HUD. No microphone is connected at the moment; the listener retries every 30 s.
- **Need:** a "hey ishti" model.
- **Why deferred:** openWakeWord custom models are trained from thousands of synthetic clips plus negative audio (about 1 hour on a GPU), using their training notebook.
- **To change:** generate the positive clips locally with Piper (already installed, many voices), train with openWakeWord's `train.py`, drop the `.onnx` into `data/`, and set `voice.wake_model` to its path. No code change.

## 9. Semantic-memory threshold
- `ishti/semantic.py` keeps recalls with cosine distance ≤ 0.45, calibrated on a handful of samples. Re-tune it once real conversation history exists (log the distances, pick the knee).

## 10. Learn new skills instead of saying "can't" (next iteration)
- **Goal:** when no skill (and no escalation) can do an *action* request, ISHTI builds one.
- **Plan:** a `builder` skill on top of an open-source code agent. HuggingFace **smolagents** CodeAgent is Apache-2.0 and works with Ollama; Open Interpreter is AGPL, so it was avoided. The coder model writes a Python script for the task. ISHTI shows the script and what it will touch, and waits for "yes" (it runs code on this PC). It runs the script in a subprocess with a timeout, in `data/sandbox` as the working directory, then checks the result. On success it saves the script as a new skill folder (`manifest.json` + `skill.py`, with the parameters lifted out), so the next similar request routes to it directly (Voyager-style skill library).
- **Safety:** always confirmed; no deletes outside the sandbox without a second confirmation; every run logged.

## 11. Video editing like an editor (next iteration)
- **Flow:** "edit the video in my downloads"
  1. `folders` lists the videos and asks which one.
  2. ISHTI analyses it: whisper transcript, FFmpeg silence and scene detection, loudness.
  3. It proposes an edit: cut silences and dead air, burned-in captions, loudness normalised, optional 9:16 reframe and music bed.
  4. It renders with NVENC.
  5. It opens the result in CapCut if installed. If CapCut isn't installed, `winget install ByteDance.CapCut`, after a yes.
- **Blocked part:** actually *clicking around inside CapCut* needs screen understanding (see #2, local vision model). Until then the editing is done by the FFmpeg pipeline, and CapCut is where you review and tweak.

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
