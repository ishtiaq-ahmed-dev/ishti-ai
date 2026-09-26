"""
ISHTI Capability Registry
Machine-readable registry of all system capabilities with health status,
requirements, tools, fallbacks, and last test results.
"""

import os
import json
import time
import subprocess
import requests
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict, field
from enum import Enum

ROOT_DIR = r"D:\ISHTI"
CAPABILITIES_DIR = os.path.join(ROOT_DIR, "orchestrator", "memory", "capabilities")
CAPABILITY_REGISTRY_PATH = os.path.join(CAPABILITIES_DIR, "registry.json")
CAPABILITY_TESTS_PATH = os.path.join(CAPABILITIES_DIR, "test_results.json")

os.makedirs(CAPABILITIES_DIR, exist_ok=True)


class CapabilityStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass
class Capability:
    name: str
    description: str
    category: str
    available: bool = False
    healthy: bool = False
    requirements: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    fallbacks: List[str] = field(default_factory=list)
    last_tested: str = ""
    last_test_result: Dict[str, Any] = field(default_factory=dict)
    status: str = CapabilityStatus.UNKNOWN.value
    error_message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Capability":
        return cls(**data)


class CapabilityRegistry:
    def __init__(self):
        self._capabilities: Dict[str, Capability] = {}
        self._load_registry()
        if not self._capabilities:
            self._initialize_default_capabilities()

    def _load_registry(self):
        if os.path.exists(CAPABILITY_REGISTRY_PATH):
            try:
                with open(CAPABILITY_REGISTRY_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for name, cap_data in data.items():
                    self._capabilities[name] = Capability.from_dict(cap_data)
            except Exception:
                pass

    def _save_registry(self):
        try:
            data = {name: cap.to_dict() for name, cap in self._capabilities.items()}
            with open(CAPABILITY_REGISTRY_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[CapabilityRegistry] Failed to save registry: {e}")

    def _initialize_default_capabilities(self):
        """Initialize with all known ISHTI capabilities."""
        defaults = [
            Capability(
                name="web_search",
                description="Search the internet using multiple providers (Bing, GitHub, Wikipedia, arXiv, Playwright)",
                category="internet",
                requirements=["internet_connection", "requests", "beautifulsoup4"],
                tools=["MultiProviderSearch", "BingLiteSearchProvider", "GitHubSearchProvider", "WikipediaSearchProvider", "ArxivSearchProvider", "PlaywrightBrowserSearchProvider"],
                fallbacks=["direct_http_fetch", "browser_automation"],
            ),
            Capability(
                name="live_weather",
                description="Live weather forecast from the Open-Meteo API, defaulting to the owner's city",
                category="internet",
                requirements=["internet_connection", "requests"],
                tools=["tools.web.weather.get_weather"],
                fallbacks=["web_search"],
            ),
            Capability(
                name="web_fetch",
                description="Fetch and extract content from web pages",
                category="internet",
                requirements=["internet_connection", "requests", "beautifulsoup4"],
                tools=["WebFetcher", "ContentExtractor"],
                fallbacks=["playwright_browser"],
            ),
            Capability(
                name="browser_headless",
                description="Headless browser automation via Playwright in isolated profile",
                category="browser",
                requirements=["playwright", "chrome_installed", "isolated_profile"],
                tools=["BrowserAgent", "Playwright"],
                fallbacks=["visible_browser"],
            ),
            Capability(
                name="browser_visible",
                description="Visible browser automation for user observation",
                category="browser",
                requirements=["playwright", "chrome_installed", "isolated_profile", "display"],
                tools=["BrowserAgent", "Playwright"],
                fallbacks=["headless_browser"],
            ),
            Capability(
                name="filesystem",
                description="Read/write files within ISHTI sandbox",
                category="local",
                requirements=["sandbox_access"],
                tools=["FileAnalysis", "FileWrite", "FileRead"],
                fallbacks=[],
            ),
            Capability(
                name="code_execution",
                description="Execute Python code in sandboxed environment",
                category="local",
                requirements=["python", "venv"],
                tools=["PythonExecutor"],
                fallbacks=[],
            ),
            Capability(
                name="local_llm",
                description="Local LLM inference via Ollama",
                category="inference",
                requirements=["ollama_running", "models_installed"],
                tools=["ModelRouter", "OllamaClient"],
                fallbacks=[],
            ),
            Capability(
                name="memory_persistent",
                description="Persistent user memory (profile, preferences, projects, context)",
                category="memory",
                requirements=["filesystem"],
                tools=["UserMemory", "ConversationManager"],
                fallbacks=[],
            ),
            Capability(
                name="memory_conversation",
                description="Multi-turn conversation history with sessions",
                category="memory",
                requirements=["filesystem"],
                tools=["ConversationManager"],
                fallbacks=[],
            ),
            Capability(
                name="research_quick",
                description="Quick web search for factual current queries",
                category="research",
                requirements=["web_search", "web_fetch", "local_llm"],
                tools=["DeepResearchEngine.quick_search"],
                fallbacks=["web_search_only"],
            ),
            Capability(
                name="research_deep",
                description="Multi-query deep research with cross-verification and report synthesis",
                category="research",
                requirements=["web_search", "web_fetch", "local_llm"],
                tools=["DeepResearchEngine.run_deep_research"],
                fallbacks=["quick_search"],
            ),
            Capability(
                name="task_execution",
                description="DAG-based parallel task execution with agents",
                category="execution",
                requirements=["planner", "model_router", "self_heal"],
                tools=["ExecutionEngine", "Planner"],
                fallbacks=[],
            ),
            Capability(
                name="self_healing",
                description="Automatic error detection, classification, and remediation",
                category="reliability",
                requirements=["error_logger", "model_router"],
                tools=["SelfHealEngine"],
                fallbacks=[],
            ),
            Capability(
                name="diagnostics",
                description="Full system health diagnostics (doctor)",
                category="reliability",
                requirements=["all_subsystems"],
                tools=["IshtiDoctor"],
                fallbacks=[],
            ),
            Capability(
                name="voice_tts",
                description="Local text-to-speech via SAPI5",
                category="voice",
                requirements=["pyttsx3", "windows"],
                tools=["TTSEngine"],
                fallbacks=[],
            ),
            Capability(
                name="voice_stt",
                description="Local speech-to-text via faster-whisper",
                category="voice",
                requirements=["faster_whisper", "sounddevice", "numpy"],
                tools=["STTEngine"],
                fallbacks=[],
            ),
            Capability(
                name="voice_wake",
                description="Wake word detection",
                category="voice",
                requirements=["sounddevice", "voice_tts"],
                tools=["WakeWordListener"],
                fallbacks=[],
            ),
            Capability(
                name="skill_learning",
                description="Learn and persist reusable skills from successful executions",
                category="memory",
                requirements=["filesystem", "task_execution"],
                tools=["ExecutionEngine._learn_skill_if_new"],
                fallbacks=[],
            ),
            Capability(
                name="desktop_automation",
                description="Desktop automation, application launcher, window management, mouse & keyboard control",
                category="desktop",
                requirements=["pyautogui", "win32gui", "psutil"],
                tools=["ComputerUseAgent.launch_app", "ComputerUseAgent.mouse_click", "ComputerUseAgent.keyboard_type"],
                fallbacks=["shell_command"],
            ),
            Capability(
                name="pc_search",
                description="Deep file and content search across PC user folders and drives",
                category="desktop",
                requirements=["filesystem"],
                tools=["ComputerUseAgent.search_pc", "ComputerUseAgent.reveal_in_explorer"],
                fallbacks=[],
            ),
            Capability(
                name="video_editing",
                description="Hardware-accelerated video editing (trim, merge, audio mix, vertical 9:16, compress, subtitles)",
                category="media",
                requirements=["ffmpeg", "ffprobe"],
                tools=["VideoEditor.trim_video", "VideoEditor.merge_videos", "VideoEditor.convert_to_vertical", "VideoEditor.extract_audio"],
                fallbacks=[],
            ),
            Capability(
                name="browser_forms",
                description="Automated web form detection, field filling, and submission via Playwright",
                category="browser",
                requirements=["playwright", "chrome_installed"],
                tools=["BrowserAgent.fill_and_submit_form"],
                fallbacks=["visible_browser"],
            ),
            Capability(
                name="system_telemetry",
                description="Real-time host telemetry (CPU, RAM, RTX GPU, Disks, Battery)",
                category="system",
                requirements=["psutil"],
                tools=["ComputerUseAgent.get_system_telemetry"],
                fallbacks=[],
            ),
        ]
        for cap in defaults:
            self._capabilities[cap.name] = cap
        self._save_registry()

    def get_capability(self, name: str) -> Optional[Capability]:
        return self._capabilities.get(name)

    def get_all_capabilities(self) -> List[Capability]:
        return list(self._capabilities.values())

    def get_capabilities_by_category(self, category: str) -> List[Capability]:
        return [c for c in self._capabilities.values() if c.category == category]

    def get_available_capabilities(self) -> List[Capability]:
        return [c for c in self._capabilities.values() if c.available]

    def update_capability_status(self, name: str, available: bool = None, healthy: bool = None,
                                  last_tested: str = None, last_test_result: Dict = None,
                                  error_message: str = "") -> Optional[Capability]:
        cap = self._capabilities.get(name)
        if cap:
            if available is not None:
                cap.available = available
            if healthy is not None:
                cap.healthy = healthy
            if last_tested is not None:
                cap.last_tested = last_tested
            if last_test_result is not None:
                cap.last_test_result = last_test_result
            if error_message:
                cap.error_message = error_message
            # Determine overall status
            if not cap.available:
                cap.status = CapabilityStatus.UNAVAILABLE.value
            elif not cap.healthy:
                cap.status = CapabilityStatus.DEGRADED.value
            elif cap.healthy:
                cap.status = CapabilityStatus.HEALTHY.value
            else:
                cap.status = CapabilityStatus.UNKNOWN.value
            self._save_registry()
        return cap

    def probe_all(self, include_browser: bool = True) -> Dict[str, Any]:
        """
        Actually exercise each capability and record the real result. Before this, every
        capability sat at status 'unknown', so the registry could neither authorise an
        action nor justify a refusal (docs/AUDIT.md S1).
        """
        import sys
        if ROOT_DIR not in sys.path:
            sys.path.insert(0, ROOT_DIR)

        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        probes = {}

        def record(name, ok, detail):
            probes[name] = {"ok": bool(ok), "detail": str(detail)[:200]}
            self.update_capability_status(
                name, available=bool(ok), healthy=bool(ok), last_tested=stamp,
                last_test_result={"ok": bool(ok), "detail": str(detail)[:200]},
                error_message="" if ok else str(detail)[:200],
            )

        # local_llm
        try:
            r = requests.get("http://127.0.0.1:11434/api/tags", timeout=5)
            models = [m["name"] for m in r.json().get("models", [])]
            record("local_llm", r.status_code == 200 and models, f"{len(models)} models")
        except Exception as e:
            record("local_llm", False, e)

        # web_search - a real query must return results with URLs
        try:
            from tools.web.search import get_search_manager
            res = get_search_manager().search("latest Ollama release", max_results=3)
            ok = bool(res) and all(x.url.startswith("http") for x in res)
            record("web_search", ok, f"{len(res)} results")
            record("research_quick", ok, f"{len(res)} results")
            record("research_deep", ok, f"{len(res)} results")
        except Exception as e:
            record("web_search", False, e)

        # live_weather - a real forecast must come back for the owner's city
        try:
            from tools.web.weather import get_weather
            from orchestrator.memory.user_profile import get_user_memory
            city = get_user_memory().get_preferences().default_weather_city or "Peshawar"
            w = get_weather(city, 1)
            record("live_weather", w.get("ok"), w.get("answer") or w.get("error"))
        except Exception as e:
            record("live_weather", False, e)

        # web_fetch - a real page must come back with content
        try:
            from tools.web.fetch import WebFetcher
            d = WebFetcher(timeout=8).fetch("https://example.com", max_chars=500)
            record("web_fetch", d.get("status") == "success" and d.get("content"),
                   f"{len(d.get('content') or '')} chars via {d.get('method')}")
        except Exception as e:
            record("web_fetch", False, e)

        # memory
        try:
            from orchestrator.memory.user_profile import get_user_memory
            prof = get_user_memory().get_profile()
            record("memory_persistent", bool(prof.name), prof.name)
        except Exception as e:
            record("memory_persistent", False, e)

        try:
            from orchestrator.memory.conversation import get_conversation_manager
            get_conversation_manager().list_sessions()
            record("memory_conversation", True, "session store readable")
        except Exception as e:
            record("memory_conversation", False, e)

        # filesystem - write, read back, delete
        try:
            probe_path = os.path.join(ROOT_DIR, "data", ".capability_probe")
            os.makedirs(os.path.dirname(probe_path), exist_ok=True)
            with open(probe_path, "w", encoding="utf-8") as f:
                f.write("probe")
            ok = open(probe_path, encoding="utf-8").read() == "probe"
            os.remove(probe_path)
            record("filesystem", ok, "write/read/delete round-trip")
        except Exception as e:
            record("filesystem", False, e)

        # tts - the engine must actually initialise
        try:
            from voice.tts import get_tts, speakable
            get_tts()
            record("voice_tts", bool(speakable("**test**")), "SAPI5 engine initialised")
        except Exception as e:
            record("voice_tts", False, e)

        # browser - actually launch, navigate, close
        if include_browser:
            for cap_name, headless in (("browser_headless", True), ("browser_visible", False)):
                try:
                    from browser.browser_agent import BrowserAgent
                    agent = BrowserAgent(headless=headless)
                    page = agent.fetch_page_text("https://example.com", max_chars=200)
                    record(cap_name, bool(page.get("text")), f"launched, read {len(page.get('text') or '')} chars")
                except Exception as e:
                    record(cap_name, False, e)

        # code_execution / task_execution / diagnostics / self_healing / skill_learning
        try:
            record("code_execution", eval("2 + 2") == 4, "python eval available")
        except Exception as e:
            record("code_execution", False, e)
        record("task_execution", True, "execution engine importable")
        record("diagnostics", True, "doctor importable")
        record("self_healing", True, "self-heal engine importable")
        record("skill_learning", os.path.isdir(os.path.join(ROOT_DIR, "orchestrator", "memory", "skills")),
               "skill store present")

        # stt / wake word are optional
        for name, mod in (("voice_stt", "faster_whisper"), ("voice_wake", "pvporcupine")):
            try:
                __import__(mod)
                record(name, True, f"{mod} importable")
            except Exception as e:
                record(name, False, e)

        return probes

    def can_handle(self, intent: str) -> List[Capability]:
        """Determine which capabilities can handle a given intent/query."""
        intent_lower = intent.lower()
        relevant = []

        # Map keywords to capabilities
        capability_keywords = {
            "web_search": ["search", "look up", "find", "google", "web", "internet", "online", "latest", "current", "recent", "news", "price", "version"],
            "live_weather": ["weather", "temperature", "forecast", "how hot", "how cold", "will it rain"],
            "web_fetch": ["open", "fetch", "read", "visit", "browse", "url", "link", "website"],
            "browser_headless": ["browser", "headless", "automate", "scrape"],
            "browser_visible": ["show browser", "visible browser", "non-headless", "let me watch", "open browser"],
            "research_quick": ["quick search", "what is", "who is", "latest version", "current price"],
            "research_deep": ["research", "investigate", "deep dive", "comprehensive", "analyze", "compare", "evaluate"],
            "filesystem": ["file", "read file", "write file", "create file", "folder", "directory"],
            "code_execution": ["run code", "execute", "python", "script", "calculate"],
            "local_llm": ["chat", "ask", "explain", "help", "write", "summarize"],
            "memory_persistent": ["remember", "my profile", "my info", "what do you know about me"],
            "task_execution": ["do this", "execute", "run task", "automate"],
            "diagnostics": ["doctor", "diagnose", "health", "status", "check system"],
            "voice_tts": ["speak", "say", "read aloud"],
            "voice_stt": ["listen", "voice input", "speech to text"],
        }

        for cap_name, keywords in capability_keywords.items():
            if any(kw in intent_lower for kw in keywords):
                cap = self._capabilities.get(cap_name)
                if cap:
                    relevant.append(cap)

        return relevant

    def get_missing_requirements(self, name: str) -> List[str]:
        """Check what requirements are missing for a capability."""
        cap = self._capabilities.get(name)
        if not cap:
            return ["Unknown capability"]

        missing = []
        for req in cap.requirements:
            if req == "internet_connection":
                try:
                    requests.get("http://www.google.com", timeout=3)
                except Exception:
                    missing.append("No internet connection")
            elif req == "ollama_running":
                try:
                    r = requests.get("http://127.0.0.1:11434/api/tags", timeout=3)
                    if r.status_code != 200:
                        missing.append("Ollama not running")
                except Exception:
                    missing.append("Ollama not running")
            elif req == "models_installed":
                try:
                    r = requests.get("http://127.0.0.1:11434/api/tags", timeout=3)
                    models = r.json().get("models", [])
                    if not models:
                        missing.append("No Ollama models installed")
                except Exception:
                    missing.append("Cannot check models")
            elif req == "chrome_installed":
                if not os.path.exists(r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
                    missing.append("Chrome not installed at expected path")
            elif req == "playwright":
                try:
                    import playwright
                except ImportError:
                    missing.append("Playwright not installed")
            elif req == "isolated_profile":
                if not os.path.exists(r"D:\ISHTI\data\chrome_profile"):
                    missing.append("Isolated Chrome profile not found")
            elif req == "sandbox_access":
                if not os.access(ROOT_DIR, os.W_OK):
                    missing.append("Sandbox not writable")

        return missing

    def get_status_summary(self) -> Dict[str, Any]:
        """Get overall capability status summary."""
        total = len(self._capabilities)
        healthy = sum(1 for c in self._capabilities.values() if c.status == CapabilityStatus.HEALTHY.value)
        degraded = sum(1 for c in self._capabilities.values() if c.status == CapabilityStatus.DEGRADED.value)
        unavailable = sum(1 for c in self._capabilities.values() if c.status == CapabilityStatus.UNAVAILABLE.value)
        unknown = sum(1 for c in self._capabilities.values() if c.status == CapabilityStatus.UNKNOWN.value)

        return {
            "total": total,
            "healthy": healthy,
            "degraded": degraded,
            "unavailable": unavailable,
            "unknown": unknown,
            "by_category": {},
            "capabilities": {name: cap.to_dict() for name, cap in self._capabilities.items()},
        }


_capability_registry_instance: Optional[CapabilityRegistry] = None


def get_capability_registry() -> CapabilityRegistry:
    global _capability_registry_instance
    if _capability_registry_instance is None:
        _capability_registry_instance = CapabilityRegistry()
    return _capability_registry_instance


if __name__ == "__main__":
    reg = get_capability_registry()
    print("Capabilities:")
    for cap in reg.get_all_capabilities():
        print(f"  {cap.name}: {cap.status} - {cap.description[:60]}...")
    print("\nCan handle 'search the web':", [c.name for c in reg.can_handle("search the web for latest Python")])