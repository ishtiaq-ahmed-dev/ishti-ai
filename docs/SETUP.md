# SETUP: install, run, test

Written 2026-10-02 for the repository as it is now. I checked that every file, folder and command named below exists
(read-only checks: file listings, `venv\Scripts\python -c "import ishti"`, and two offline test files). I did not
install anything, start the backend, load a model or touch Ollama's model set, so steps marked **not run here** are
derived from the code and existing docs, not executed by me.

## 1. What this machine is assumed to be

ISHTI was built and measured on one PC (SOURCED, `docs/build-history/optimization/ENVIRONMENT.md` and
`docs/MODEL_DECISIONS.md`): Windows 11, NVIDIA RTX 5070 Ti with 16 GB VRAM (16303 MiB), 64 GB RAM, Python 3.11.9,
Ollama 0.35.0 (MEASURED today with `ollama --version`), Node v24.16.0 and npm 11.13.0 (MEASURED today). It is a
Windows-only product today: the desktop shell, Job Object, DPAPI, Windows GPU counters and `start.bat` are all
Windows-specific. I have no measurement for a smaller GPU. The VRAM governor will pick lighter model rungs when
memory is short (see [ARCHITECTURE.md](ARCHITECTURE.md) section 7), but the minimum viable GPU is **ASSUMED unknown**.

## 2. Install

There is **no requirements file** in the repository (no `requirements*.txt`, `pyproject.toml` or `setup.py`). The
Python environment exists only as the `venv\` folder, which is git-ignored. To rebuild it on another machine you need
to reconstruct it from the package list below, or run `venv\Scripts\python -m pip freeze > requirements.txt` on the
working machine first. That gap is the single biggest obstacle to a clean install.

Key packages in `venv\` today (MEASURED with `pip list`, 343 packages in total): torch 2.11.0+cu128 (a CUDA 12.8
build; the RTX 50-series needs it), torchaudio 2.11.0, laya 0.3.20, chromadb 1.1.1, mcp 1.28.1, faster-whisper 1.2.1,
ctranslate2 4.8.2, openwakeword 0.6.0, onnxruntime 1.30.0, kokoro-onnx 0.6.1, piper-tts 1.8.0, sounddevice 0.5.6,
openai 2.54.0 (client for Ollama's OpenAI-compatible endpoint), fastapi 0.141.1, uvicorn 0.53.0, pydantic 2.12.5,
pywebview 6.2.1, pywin32 312, pystray 0.19.5, pynput 1.8.2, psutil 7.2.2, keyring 25.7.0, playwright 1.63.0,
pypdfium2 5.13.0, python-docx 1.2.0, reportlab 5.0.1, PyYAML 6.0.3, pyinstaller 6.22.3, pytest 9.1.1,
pytest-timeout 2.4.0.

Steps (**not run here**):

1. Install Python 3.11, then `python -m venv venv` in the project folder and install the packages above into it
   (PyTorch from the cu128 index). `start.bat` will create an empty venv if none exists, but it does not install
   packages.
2. Install [Ollama](https://ollama.com) (0.35.0 or newer; the Spark model needs 0.34.2+, `models_ladder.yaml`).
3. Install Node 24+ (used for `npx @playwright/mcp@0.0.82`, the browser skill's MCP server, and for building the UI).
4. Place the project at **`D:\ISHTI`**. `ishti/config.json` contains absolute `D:\ISHTI\...` paths for the memory
   database, Chroma, MCP output and voice folders. Either install there or edit those paths. I did not verify that a
   different location works without edits.
5. Build the UI:

   ```bat
   cd interface\app
   npm install
   npm run build
   cd ..\..
   venv\Scripts\python -m tools.fetch_ui_fonts
   ```

   `npm run build` writes `interface\app\dist` (git-ignored). If it is missing, the server falls back to the older
   templated HUD at `/hud`. `tools.fetch_ui_fonts` downloads the UI fonts once into `interface\app\public\fonts`
   (needs internet; run it before the build so Vite copies them into `dist`).
6. Models (Ollama). The roles and their ladders are in `ishti/config.json` and `models_ladder.yaml`. The Modelfiles
   in `ishti/` create the 8k-context variants because Ollama's `/v1` endpoint cannot set the context per request:

   ```bat
   ollama pull qwen2.5:14b
   ollama pull qwen2.5-coder:14b
   ollama pull nomic-embed-text
   ollama pull gemma3:4b
   ollama create ishti-qwen2.5:14b       -f ishti\planner.Modelfile
   ollama create ishti-qwen2.5-coder:14b -f ishti\coder.Modelfile
   ```

   The configured planner is `ishti-spark2.5:4b`, built from `ishti\spark2.5.Modelfile` (a local GGUF with a recorded
   SHA256; acquisition record: `docs/build-history/prompts-1-4/MODEL_ACQUISITION_spark2.5.md`). Without it the loader
   falls back to `ishti-qwen2.5:14b` (`llm.fallback`). The coder role names `qwen3-coder:30b-a3b`, which was not
   installed when last checked, so the fallback chain applies (`NEEDS.md` N23). `gemma3:12b` is the optional heavy
   vision rung. The governor never downloads a model; a rung that is not installed is skipped.
7. Speech models:
   - STT: `venv\Scripts\python tests\download_whisper_turbo.py` fetches the CTranslate2 `large-v3-turbo` build
     (about 1.6 GB) into `data\models\whisper-large-v3-turbo`, the path named in `config.json`.
   - TTS: Kokoro and Piper voice files live in `data\kokoro` and `data\piper`. They can be downloaded from the
     app's Settings -> Models & voices screen (`ishti/manage.py`), which fetches Kokoro from the kokoro-onnx
     releases and Piper voices from Hugging Face.
8. Optional services:
   - Web search: a local SearXNG container (Docker). Without it search falls back to Wikipedia/GitHub/arXiv/Bing.

     ```bat
     docker run -d --name ishti-searxng --restart unless-stopped -p 127.0.0.1:8890:8080 ^
       -v D:/ISHTI/ishti/searxng:/etc/searxng -e SEARXNG_SECRET=<random hex> searxng/searxng
     ```
   - Page reader: download Obscura 0.2.3 into `tools\obscura\` (`obscura.exe`, `obscura-worker.exe`; steps in
     `tools\obscura\README.md`; verify with `venv\Scripts\python -m ishti.obscura`).
   - Remote access from a phone: `remote\tunnel_setup.md` (Cloudflare Tunnel + Access) and, for Telegram, pairing in
     the app. Both are off by default; read [SAFETY.md](SAFETY.md) section 10 first.

## 3. Run

```bat
start.bat                 :: the desktop app: splash window, then the dashboard (no console window)
start.bat cli             :: interactive terminal mode (orchestrator\startup.py --cli)
start.bat server [port]   :: backend only, default port 8888, no window
stop.bat                  :: clean shutdown (desktop\shutdown.py)
start.bat doctor          :: orchestrator\doctor.py system diagnostics
start.bat settings        :: opens http://127.0.0.1:8888/settings (see the note below)
start.bat test            :: the offline test suite
```

`start.bat` with no argument runs `dist\ISHTI\ISHTI.exe` if it has been built from `ISHTI.spec`, otherwise
`venv\Scripts\pythonw.exe desktop\launcher.py`. The launcher starts the backend (`python -m ishti 8888`), the tray icon
and the window. The dashboard is at `http://127.0.0.1:8888`. Boot takes tens of seconds because the models load;
the window shows the dashboard about a second after launch and the boot stages as they finish. A shortcut for the
desktop can be created with `venv\Scripts\python desktop\create_shortcut.py`.

Logs: `%LOCALAPPDATA%\ISHTI\logs\ishti.log` (rotating). Single-instance and process registry:
`%LOCALAPPDATA%\ISHTI\run\ishti.json`. Window position: `%LOCALAPPDATA%\ISHTI\window.json`.

Notes:

- `start.bat settings` opens `/settings`, but I found no `/settings` route in the server (the React app has its own
  Settings page inside the dashboard). `docs/SETTINGS.md` carries a status note about this.
- The server creates `data\api_token` on first start if it is missing. The microphone starts switched **off**
  (tray menu or the dashboard turns it on).
- Environment variables read by the code: `ISHTI_MODEL_<ROLE>` pins a role to one model, `ISHTI_OLLAMA_URL`,
  `ISHTI_LADDERS` (alternate ladder file), `ISHTI_TEST_MODE=1` (blocks T2 side effects), `ISHTI_WINDOW_STATE`.

## 4. Test

Everything below runs from the project folder with the venv's Python (`venv\Scripts\python`). Full details:
[TESTING.md](TESTING.md) and `tests/README.md`.

| What | Command | Needs |
|---|---|---|
| Offline suite (default) | `venv\Scripts\python -m pytest` (same as `start.bat test`) | nothing live; `pytest.ini` deselects `-m e2e` and skips `tests/skills`, `tests/scenarios`, `tests/review` |
| One file | `venv\Scripts\python -m pytest tests\test_manifest_integrity.py -q` | nothing live |
| Per-skill suites (120+ folders) | `venv\Scripts\python tests\run_skill.py weather` (or `all`) | sets `ISHTI_TEST_MODE=1`; some need the network or GPU |
| Live backend contract | `venv\Scripts\python -m pytest tests\test_lifecycle_contract.py -m e2e -s` | starts a real backend; needs Ollama + GPU |
| Live core loop | `venv\Scripts\python -m ishti.check` | Ollama running |
| Every self-check and test, raw exit codes | `venv\Scripts\python tools\verify_all.py [--only X] [--quick]` | partly live |
| Performance regression gate | `venv\Scripts\python -m tools.perf_guard --baseline <json>` | a baseline JSON from `tools\bench_opt.py` |
| Launch timing | `venv\Scripts\python tools\launch_probe.py` | starts the real app; do not run while ISHTI is open |

Checks I ran for this document: `venv\Scripts\python -c "import ishti"` printed the project root without error;
`pytest tests\test_manifest_integrity.py tests\test_session_schema.py` passed (10 tests); `pytest --collect-only`
collects **464** tests (MEASURED today, default configuration). I did not run the full suite.

Last recorded full-suite result: 445 collected, 445 passed on the commit `9e6003b` working tree (SOURCED:
`docs/build-history/qa/backend-2026-09-29/TEST_RESULTS.md`, PART 3, 2026-10-02). Later Cycle-4 performance work
recorded the suite green (`EXIT=0`) twice (SOURCED: `PERFORMANCE_CYCLE4.md`). Treat both as history, not as a
statement about the tree you have today.

Do not run tests that start the backend, load models or use the GPU while another ISHTI session is using the machine:
the VRAM governor budget is shared, and a second model load can push the main model off the GPU.

## 5. Troubleshooting pointers

- Boot stuck or failed: read `%LOCALAPPDATA%\ISHTI\logs\ishti.log`; stages and their plain-language errors are
  defined in `ishti/lifecycle.py` (`FRIENDLY`).
- "Ollama didn't start": install Ollama and make sure `ollama serve` works; ISHTI starts it itself and stops it at
  shutdown only if it started it.
- Model not fully on the GPU: that is refused by design; check free VRAM (`nvidia-smi`) and `GET /api/vram`.
- Something broke that ISHTI could not handle: `md files\NEEDS.md` (the live runtime log) and `ISSUES.md`.
- Window never appears or app will not close: `stop.bat`, then `start.bat`; process details are in
  `docs/build-history/qa/backend-2026-09-29/TEST_RESULTS.md` (Issues 31-35).
