# ISHTI - Your Personal AI Assistant
*An autonomous, locally-hosted personal assistant inspired by JARVIS, engineered for zero ongoing cost, real-time internet search, deep research, strict profile isolation, parallel DAG task execution, and self-healing memory.*

---

## ⚡ Key Highlights & Core Capabilities

- **Zero Ongoing Cost:** 100% of core reasoning, planning, and transcription runs on local open-weight models via Ollama on your **RTX 5070 Ti (16GB VRAM)** and **Intel Core Ultra 9**. Zero paid external APIs.
- **Real-Time Internet Search:** Autonomous multi-provider search engine (`tools/web/search.py`) combining Bing Lite, GitHub API, Wikipedia API, arXiv API, and Playwright browser search with self-healing failover.
- **Automatic Temporal Intent Detection:** Recognizes keywords like *"today"*, *"now"*, *"latest"*, *"current"*, *"recent"*, and *"what happened"*, automatically routing to web search without requiring a manual "web mode".
- **Deep Research Engine:** Multi-query formulation, parallel web page fetching, boilerplate/ad stripping, cross-verification, and comprehensive executive report synthesis with persistent research memory.
- **Verifiable Source Traceability:** Final answers feature inline bracket citations (`[1]`, `[2]`) linked directly to an expandable **SOURCES** drawer displaying source title, domain, publication date, snippet, and `[OPEN]` links.
- **Animated Answer Streaming:** Professional cyber-holographic token/word streaming animation with real-time status transitions (`● THINKING`, `● SEARCHING WEB`, `● RESEARCHING`, `● VERIFYING`, `● RESPONDING`).
- **Dedicated Execution & Research Views:** Automatically transitions from Chat to dedicated Execution or Deep Research views for substantial tasks, complete with interactive clickable DAG nodes that open an inspection drawer.
- **Full Doctor Diagnostic Suite:** Integrated system health probe accessible via `ishti doctor` CLI or the Web HUD **DOCTOR** tab, verifying hardware, Ollama, Chrome isolation, Playwright, web search, fetch, Faster-Whisper, and TTS.
- **Strict Profile & Filesystem Isolation:** Automated browser operations run exclusively inside `D:\ISHTI\data\chrome_profile`. Daily-driver Chrome profiles and foreign Windows users are never touched.
- **Human-in-the-Loop Confirmation Gate:** Consequential actions (dispatching emails/messages, publishing content, executing financial transactions) pause automatically and present a full draft for explicit approval before execution.

---

## 🏗️ Architecture Layout

```
D:\ISHTI\
├── start.bat                   # Master one-click startup entry point
├── ishti.bat                   # CLI utility launcher (doctor, search, rebenchmark)
├── README.md                   # System documentation & architectural reference
├── assets/                     # Official holographic logos, banners & visual assets
├── tools/
│   ├── web/                    # Modular internet research tool registry
│   │   ├── provider.py         # Abstract WebSearchProvider & WebSearchResult dataclasses
│   │   ├── extract.py          # ContentExtractor stripping ads/scripts/nav boilerplate
│   │   ├── fetch.py            # 3-tier WebFetcher (HTTP -> Jina open reader -> Playwright)
│   │   └── search.py           # MultiProviderSearch (Bing Lite + GitHub + Wiki + arXiv + Playwright)
│   └── video/                  # GPU-Accelerated Video Editing & Media Automation
│       └── video_editor.py     # FFmpeg + NVENC video trimmer, merger, audio mixer, 9:16 vertical converter
├── orchestrator/               # Core intelligence & execution engine
│   ├── config.json             # Master system & model configuration
│   ├── startup.py              # Health-checked startup sequence & terminal HUD
│   ├── doctor.py               # Comprehensive system diagnostics probe suite (42 E2E tests)
│   ├── intent_detector.py      # Automatic intent & temporal keyword classifier
│   ├── deep_research.py        # Quick search & multi-query Deep Research engine
│   ├── model_router.py         # Automated local benchmark & model selection suite
│   ├── planner.py              # Request decomposition & dependency DAG graph analyzer
│   ├── crew.py                 # Unified execution gateway with parallel agent concurrency
│   ├── self_heal.py            # Error diagnostics, auto-remediation & reflection logger
│   ├── test_self_heal.py       # Automated test proving self-healing & learning persistence
│   └── memory/
│       ├── reflections.md      # Rolling audit log of errors, root causes & fixes
│       ├── research/           # Persistent JSON archives of synthesized research reports
│       └── skills/             # Reusable learned skills (browser_research, file_analysis, etc.)
├── voice/                      # Audio input/output pipeline
│   ├── stt.py                  # Local faster-whisper GPU speech-to-text
│   ├── tts.py                  # Zero-latency local speech synthesizer (SAPI5)
│   └── wake_word.py            # Energy-threshold wake-word listener ('Ishti' / 'Jarvis')
├── browser/
│   └── browser_agent.py        # Playwright agent with form filling & search, hard-isolated
├── desktop/
│   └── computer_use.py         # PC deep file search, universal app launcher, mouse/keyboard automation, telemetry
├── interface/
│   └── web_ui/                 # Futuristic local Web HUD (FastAPI + WebSockets)
│       ├── server.py           # Backend server, API endpoints & WebSocket event broadcaster
│       ├── static/             # Neon cyan/gold HUD styles, visualizer JS, and logo
│       └── templates/          # index.html multi-page command center dashboard
└── remote/
    └── tunnel_setup.md         # Cloudflare Tunnel + Cloudflare Zero Trust guide
```

---

## 🚀 How to Launch & Use ISHTI

### 1. Launching via Desktop Shortcut or Explorer
Double-click `D:\ISHTI\start.bat`.

The automatic startup sequence will:
1. Activate the Python virtual environment (`D:\ISHTI\venv`).
2. Verify that the Ollama service is running (auto-launches daemon if stopped).
3. Verify that the primary local model is loaded and responsive.
4. Launch the local Web UI server on `http://127.0.0.1:8888`.
5. Open an isolated Chrome application window pointing to the HUD.
6. Announce readiness out loud via TTS.
7. Open the interactive command loop (voice or text).

### 2. CLI Diagnostics & Quick Commands (`ishti.bat`)
Run system diagnostics from any terminal:
```cmd
d:\ISHTI\ishti.bat doctor
```
Perform quick CLI web research:
```cmd
d:\ISHTI\ishti.bat search "What is the latest version of Python?"
```
Re-benchmark local models on your RTX 5070 Ti:
```cmd
d:\ISHTI\ishti.bat rebenchmark
```

### 3. Web HUD Multi-Page Navigation
Navigate across 6 dedicated views using the top HUD navigation tabs:
- **💬 CHAT:** Main conversational interface with real-time answer streaming, reactor core audio visualizer, telemetry cards, and expandable sources.
- **⚡ EXECUTIONS:** Live DAG execution tracker showing multi-agent status, progress bar, topological stage breakdown, event stream, and clickable node inspection drawer.
- **🌐 RESEARCH:** Dedicated Deep Research view displaying formulated search queries, verified source cards, and final executive report.
- **🧠 SKILLS:** Interactive explorer of all learned skills saved in Skill Memory.
- **📜 MEMORY:** Self-healing reflections audit log and archived research reports.
- **🩺 DOCTOR:** Full system diagnostics dashboard testing GPU, RAM, Ollama, Models, Browser Sandbox, and Web Search.

---

## 🔍 Internet Research & Source Verification

### Quick Search
For simple factual questions requiring current information (e.g., *"What is the latest version of Python?"*):
1. **Detection:** Intent detector flags temporal keywords (`latest`, `version`).
2. **Search:** `MultiProviderSearch` queries Bing Lite / official tech sources.
3. **Fetch & Read:** `WebFetcher` reads the live downloads page in 0.5s.
4. **Synthesis:** Local model extracts exact release data with bracketed citations `[1]`.
5. **Display:** UI streams the answer smoothly, followed by an expandable **SOURCES (4)** dropdown.

### Deep Research
For complex investigations (e.g., *"Research the latest open-source autonomous AI agent projects"*):
1. **Formulation:** Local fast model generates 3-4 distinct research query angles.
2. **Multi-Query Search:** Concurrent searches across GitHub Repositories API, Bing Lite, and arXiv.
3. **Evidence Extraction:** Parallel reading of top sources, extracting structured excerpts.
4. **Cross-Checking:** Evaluates consensus, highlights disagreements, and resolves conflicting data.
5. **Executive Report:** Generates a structured report with Executive Summary, Key Findings, Comparative Breakdown, and inline citations.
6. **Research Memory:** Automatically archives topic, date, sources, and report to `orchestrator/memory/research/`.

---

## 🛡️ Security & Boundary Guarantees

1. **Strict Chrome Profile Isolation:** All automated browsing is constrained to `--user-data-dir=D:\ISHTI\data\chrome_profile`. No cookies, history, or tokens from your personal Chrome profile are accessible.
2. **Consequential Action Gate:** Real-world communications, public posts, financial payloads, or system modifications require explicit user approval via modal dialog and audio confirmation.
3. **Zero-Fee Commitment:** All inference, planning, transcription, and speech synthesis run strictly on local hardware. No paid third-party APIs.

---

## 🧪 Verification & Diagnostics

Run the full self-healing verification test:
```powershell
D:\ISHTI\venv\Scripts\python.exe D:\ISHTI\orchestrator\test_self_heal.py
```

Run the complete system health diagnostics:
```powershell
D:\ISHTI\venv\Scripts\python.exe D:\ISHTI\orchestrator\doctor.py
```
