# ISHTI System Prerequisites & Requirements (NEEDS.md)

This document tracks toolchain dependencies, missing skills, or user installations required for native builds and advanced features.

---

## 1. Optional Native Tauri 2 Toolchain
ISHTI currently operates via a native Microsoft Edge WebView2 desktop shell with PyInstaller `--onedir` distribution, which runs with zero terminal windows, native GPU acceleration, and low RAM.

To compile a native Rust Tauri 2 binary (`cargo tauri build`), the following toolchain components must be installed:

1. **Rust Toolchain**:
   - Download and run the official installer: [https://rustup.rs/](https://rustup.rs/) (or `winget install Rustlang.Rustup`).
   - Default host triple: `x86_64-pc-windows-msvc`.
2. **Visual Studio 2022 C++ Build Tools**:
   - Download Visual Studio Build Tools: [https://visualstudio.microsoft.com/visual-cpp-build-tools/](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
   - Workload to select: **Desktop development with C++** (includes MSVC v143, Windows 10/11 SDK, C++ CMake tools).
3. **Tauri CLI**:
   - Run `cargo install tauri-cli` or `npm install -g @tauri-apps/cli`.

*Note: The native WebView2 Python shell is already fully functional on this system and does not block desktop usage.*

---

## 2. Optional NSIS Binary Compiler
To compile the standalone `.exe` installer from `installer/ishti.nsi`:
- **NSIS (Nullsoft Scriptable Install System)**:
  - Install via winget: `winget install NSIS.NSIS`
  - Or download from: [https://nsis.sourceforge.io/Download](https://nsis.sourceforge.io/Download)
  - Default binary path: `C:\Program Files (x86)\NSIS\makensis.exe`.


---

# Backend (VRAM governor + lifecycle) — what is missing or needs the owner's decision (2026-09-29)

Nothing below blocks the backend; each item says exactly what is needed and what happens until then.

## N1. Your own prompt set, to grade the lighter models
The quality column in `MODEL_DECISIONS.md` comes from six fixed prompts I wrote (arithmetic, JSON extraction, a table, an Urdu
translation that only checks for Arabic script, a tool call, reading a number from an image). On them the 7B planner rung
**fails the Urdu prompt** and both coder rungs fail the tool-call prompt. Whether that matters depends on your real use.
*Needed:* 10–20 prompts you care about (especially Urdu/Pashto, plans, tool use) in a text file; then
`python -m ishti.vram_calibrate` grades every rung on them. *Until then:* the planner steps down to the 7B during voice sessions
and heavy vision jobs, and the UI receives a `model_switch` event saying so.

## N2. Decide: quantized KV cache (`OLLAMA_KV_CACHE_TYPE=q8_0`)
Left **off** (`f16`). The numbers from `tests/kv_cache_probe.py` are in `MODEL_DECISIONS.md`; enabling it also needs an Ollama
restart, which ISHTI only does for an Ollama it started itself (see N5). *Needed:* your OK after reading the quality result
(recall test + fixed prompts), then `POST /api/ollama/settings {"kv_cache_type":"q8_0"}` and `POST /api/ollama/apply`.

## N3. Decide: heavy vision tier 3 (gemma3:12b) and the 80% budget
At the default 80% budget the 12B vision model (8973 MiB) cannot sit next to any planner rung, so heavy vision jobs get tier 2
(`gemma3:4b`) and the planner steps to the 7B meanwhile. With `budget_pct: 95` it works (planner → 3B, both resident, 83% peak) but
leaves little headroom for the browser/Windows. *Needed:* your call on `budget_pct` (edit `models_ladder.yaml`; picked up live).

## N4. Decide: Laya on the CPU?
Laya holds ~1.3 GB of VRAM (and spikes to 3.8 GB while loading), which is why the 14B planner alone already sits at ~78.5%.
Moving it to the CPU would give the planner ~1.3 GB of room but slows routing (it was measured at ~35 ms on the GPU).
*Needed:* your OK; the change is one line in `ishti/router.py` and needs a routing-latency check (`python -m ishti.check`).

## N5. The Ollama that is running now was not started by ISHTI
It was started outside (process 4476, parent gone), so ISHTI treats it as **not ours**: it unloads its own models at shutdown but
never stops or restarts it, and it cannot apply new Ollama settings to it. *Needed:* either quit that Ollama once (tray icon →
Quit) so the next ISHTI start launches and owns one (hidden, in the Job Object, with the settings in `data/ollama_settings.json`),
or call `POST /api/ollama/apply {"take_over": true}` from the UI to let ISHTI restart it.

## N6. `config.json` model names, and a stale download
`llm.models.planner/coder` name `qwen3:30b-a3b` / `qwen3-coder:30b-a3b` (not installed, 19 GB). And an 18.5 GB `-partial` blob from
26 Sep sits in `~/.ollama/models/blobs`. Both are yours to change/remove (see ISSUES.md #21, #22).

## N7. Not measured: a live microphone turn
No microphone or speaker is connected to this PC (`Error querying device -1`, audit O-2). The voice tests ran the real pipeline in
process (Whisper load through the governor, STT decode, planner first token, TTS) with a synthesized question, not sound from a
microphone. *Needed:* reconnect the headset, then run `tests/e2e_vram_lifecycle.py t3 perf` again for the microphone-driven numbers.

## N8. For the desktop-shell session (Prompt 2)
`POST /api/focus` emits `show_window` on `/ws/events`; a second launch of the *backend* already calls it. The desktop launcher's own
second-launch path should call it too (it currently uses `desktop/focus_ishti.py`). Nothing in `desktop/` or `interface/app/` was
edited except `desktop/shutdown.py` (the stop routine, which is backend lifecycle and is now a thin client of `POST /api/shutdown`).

## N9. Chat File Upload UI Feature (Verification Scenario D3)
- **Status**: BLOCKED (Feature missing in frontend)
- **Issue**: The current chat interface (`interface/app/src/sections/Chat.tsx`) only provides a text input box. There is no visual file attachment button (paperclip) or drag-and-drop zone to upload images and PDFs directly into the chat session.
- **Current Workaround**: Users can type local filesystem paths (e.g. `summarize D:\ISHTI\data\sample.pdf`), which are parsed by the document and OCR skills.
- **Required Action**:
  1. Add a file attachment button and drag-and-drop listener to `interface/app/src/sections/Chat.tsx`.
  2. Implement a `POST /api/upload` multipart form endpoint in `interface/web_ui/server.py` that saves uploaded files to `data/uploads/` and passes the resulting path to the active conversation session.

## N10. Third-Party Integration — Apodex Clarification
- **Status**: BLOCKED (Repository ambiguity)
- **Issue**: Web search for "Apodex" identifies the commercial closed-source AI model family (`apodex.com`), but no official open-source repository exists with an embeddable Python client.
- **Required Action**: Owner must provide the exact repository URL or clarify whether this integration should interface with a specific REST endpoint.

## N11. Third-Party Integration — Pi Desktop Architecture Clarification
- **Status**: BLOCKED (Application vs library ambiguity)
- **Issue**: `vastsa/pi-desktop` is a standalone workstation application rather than an embeddable Python library with a stable agent API.
- **Required Action**: Owner must clarify the desired integration mode: (a) document and run side-by-side, (b) launch via sub-process from ISHTI, or (c) adapt UI design elements.

