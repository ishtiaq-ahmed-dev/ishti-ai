<p align="center">
  <img src="assets/ishti_logo_dark.png#gh-dark-mode-only" alt="ISHTI: Personal AI Assistant" width="460" />
  <img src="assets/ishti_logo.png#gh-light-mode-only" alt="ISHTI: Personal AI Assistant" width="460" />
</p>

# ISHTI: local-first personal AI assistant

Runs 100% on this PC after setup: Ollama LLMs, Laya routing, SQLite + ChromaDB memory, faster-whisper STT, Piper TTS and the Playwright MCP browser. No cloud API calls at runtime.

```
request (text or voice)
   │
   ▼
dispatcher (FastAPI, ishti/dispatcher.py)
   ├─ pending confirmation?  → back to the skill that asked
   ├─ several tasks? ("…and then…") → LLM plan → skills in order ({prev} carries answers)
   ├─ skill triggers (regex)  ─┐
   ├─ Laya typed decisions    ─┼→ skill  →  reply (+ Piper audio)
   └─ default: chat           ─┘
          skills: memory · reminders · folders · weather · web_search · deep_research · web_fetch · browser (MCP)
                  files · apps · system · video · chat
          memory: exact facts (SQLite)  >  semantic recall (ChromaDB)
```

## Run

```bat
start.bat                 :: starts ISHTI desktop application (launches window & auto-closes CMD)
start.bat cli             :: starts ISHTI in interactive terminal REPL mode
stop.bat                  :: cleanly terminates ISHTI web servers and background processes
python -m ishti [port]    :: server only
python -m ishti.check     :: end-to-end check: routing accuracy + memory + live skill + LLM
```

- **Web HUD**: `http://127.0.0.1:8888` (reactive JARVIS/ISHTI HUD interface)
- **Settings Screen**: `http://127.0.0.1:8888/settings`
- **Logs**: `%LOCALAPPDATA%\ISHTI\logs\ishti.log` (10 MB × 5 files rotating log)
- **API Contract**: [`docs/API_CONTRACT.md`](../docs/API_CONTRACT.md)

## Desktop Application & Voice Control
- **True Desktop Shell**: PyWebView standalone app window with animated startup splash and system tray integration.
- **Microphone Kill-Switch**: Toggle system mic listener anytime from the desktop tray icon or UI HUD.
- **Hardware Profile**: Engineered and accelerated locally for RTX 5070 Ti (16GB GDDR7) and 64GB DDR5 RAM.
