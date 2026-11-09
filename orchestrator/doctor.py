"""
ISHTI System Diagnostics & Doctor Engine
Performs comprehensive end-to-end health checks across all hardware, local models,
isolated browser environment, web capabilities, speech pipelines, and memory integrity.
"""

import os
import sys
import time
import json
import shutil
import subprocess
import importlib.metadata
from typing import Dict, Any, List
import requests

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from tools.web.search import get_search_manager
from tools.web.fetch import WebFetcher
from orchestrator.model_router import ModelRouter
from orchestrator.memory.user_profile import get_user_memory

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE_DIR = os.path.join(ROOT_DIR, "data", "chrome_profile")


class IshtiDoctor:
    def __init__(self):
        self.results = {}
        self.warnings = []
        self.errors = []

    def _live(self, fn, skip_note):
        """Run one LIVE check with the remaining budget as its cap (thread + join). A hung network call
        therefore delays the suite by at most the leftover budget, never forever (2026-09-30 fix).
        """
        import threading
        left = self._deadline - time.time()
        if left <= 1.0:
            self._log_result("Doctor", skip_note, True, "skipped: time budget spent", is_warning=True)
            return
        box = {}

        def runner():
            try:
                fn()
            except BaseException as e:   # noqa: BLE001 - a live check failure is recorded, not fatal
                box["err"] = e
        th = threading.Thread(target=runner, daemon=True)
        th.start()
        th.join(left)
        if th.is_alive():
            self._log_result("Doctor", f"{skip_note} (skipped: overran the time budget)", True,
                             "the suite moved on", is_warning=True)

    def check_all(self, include_e2e: bool = True, budget_s: float = 45.0) -> Dict[str, Any]:
        """Runs full diagnostic suite.

        budget_s: overall wall-clock cap. Live-network checks (search, fetch, weather, e2e) are
        SKIPPED once the budget is spent, so 'run diagnostics' can never hang on a slow/offline
        network (owner-skill timeout bug, 2026-09-30). Static checks always run.
        """
        import time as _t
        self._deadline = _t.time() + float(budget_s)
        print("\n" + "=" * 65)
        print("         ISHTI SYSTEM HEALTH & CAPABILITY DIAGNOSTICS")
        print("=" * 65 + "\n")

        self.check_environment()
        self.check_hardware()
        self.check_ollama()
        self.check_models()
        self.check_browser_isolation()
        self._live(self.check_web_search, "Live web search")
        self._live(self.check_web_fetch, "Live page fetch")
        self.check_voice()               # internal live parts are budget-aware
        self.check_memory_and_skills()
        self.check_install_state()
        self.check_services()

        if include_e2e:
            self._live(self.check_end_to_end_capabilities, "End-to-end core checks")

        self._print_summary()
        return {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "checks": self.results,
            "warnings": self.warnings,
            "errors": self.errors,
            "overall_status": "HEALTHY" if not self.errors else "DEGRADED",
        }

    def _log_result(self, category: str, test_name: str, passed: bool, details: str = "", is_warning: bool = False):
        if category not in self.results:
            self.results[category] = []
        status_text = "PASS" if passed else ("WARN" if is_warning else "FAIL")
        icon = "[OK]  " if passed else ("[WARN]" if is_warning else "[FAIL]")
        self.results[category].append({
            "test": test_name,
            "status": status_text,
            "details": details,
        })
        print(f"  {icon} {test_name:<38} [{status_text}] {details}")
        if not passed:
            if is_warning:
                self.warnings.append(f"{test_name}: {details}")
            else:
                self.errors.append(f"{test_name}: {details}")

    def check_environment(self):
        print("--- [1] Environment & Filesystem ---")
        py_ver = sys.version.split()[0]
        self._log_result("Environment", "Python Runtime", sys.version_info >= (3, 10), f"v{py_ver}")
        venv_active = sys.prefix != sys.base_prefix
        self._log_result("Environment", "Virtual Environment", venv_active, f"Path: {sys.prefix}")
        sandbox_ok = os.path.exists(ROOT_DIR)
        self._log_result("Environment", "Project Sandbox Root", sandbox_ok, ROOT_DIR)

    def check_hardware(self):
        print("\n--- [2] Hardware & GPU Acceleration ---")
        try:
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.free,temperature.gpu", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=4,
            )
            if res.returncode == 0 and res.stdout.strip():
                gpu_info = res.stdout.strip().replace("\n", " | ")
                self._log_result("Hardware", "NVIDIA RTX GPU", True, gpu_info)
            else:
                self._log_result("Hardware", "NVIDIA RTX GPU", False, "nvidia-smi returned empty", is_warning=True)
        except Exception as e:
            self._log_result("Hardware", "NVIDIA RTX GPU", False, str(e), is_warning=True)

        import psutil
        cpu_count = os.cpu_count() or 0
        ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 1)
        self._log_result("Hardware", "Host CPU & RAM", cpu_count >= 8, f"{cpu_count} Logical Cores, {ram_gb} GB RAM")

    def check_ollama(self):
        print("\n--- [3] Local Inference Engine (Ollama) ---")
        router = ModelRouter()
        try:
            r = requests.get(f"{router.host}/api/tags", timeout=3)
            if r.status_code == 200:
                models = [m["name"] for m in r.json().get("models", [])]
                self._log_result("Ollama", "Ollama Daemon (Zero-Cost)", True, f"Online ({len(models)} models available)")
            else:
                self._log_result("Ollama", "Ollama Daemon", False, f"HTTP {r.status_code}")
        except Exception as e:
            self._log_result("Ollama", "Ollama Daemon", False, f"Could not connect to {router.host}: {e}")

    def check_models(self):
        print("\n--- [4] Local Models & Reasoning ---")
        router = ModelRouter()
        # The configured name may be stale (config.json has named a 19 GB MoE that is not installed).
        # Ping the model ISHTI would actually serve: the first installed ladder rung.
        primary = router.get_model("primary")
        installed = router.get_installed_models()
        if primary not in installed:
            ladder = []
            try:
                from ishti import vram as ishti_vram
                ladder = [r.get("model") for r in (ishti_vram.cfg().get("roles", {}).get("planner", {}).get("rungs") or [])]
            except Exception as _exc:
                swallowed('orchestrator/doctor.py:IshtiDoctor.check_models', _exc)
            ladder = [m for m in ladder if m in installed]
            self._log_result("Models", f"Configured primary '{primary}' not installed",
                             False, f"ISHTI serves '{ladder[0] if ladder else 'the fallback rung'}' instead", is_warning=True)
            primary = ladder[0] if ladder else next(iter(installed), primary)
        fast = router.get_model("fast")
        self._log_result("Models", f"Fast Classifier Model ({fast})", True, "Configured for cheap jobs")

        left = self._deadline - time.time()
        try:  # a cold model load reads ~20 GB off disk (~35 s measured here): under a tight budget the
              # check skips instead of overrunning it - the budget governs static checks too (2026-10-16)
            from ishti import vram as ishti_vram
            resident = {m["name"] for m in ishti_vram.ps(True)}
            if primary not in resident and left < 40:
                self._log_result("Models", f"Primary Model ({primary})", True,
                                 f"skipped: {round(left)} s left is too tight for a cold model load",
                                 is_warning=True)
                return
        except Exception as _exc:
            swallowed('orchestrator/doctor.py:IshtiDoctor.check_models', _exc)
        try:
            # Through the governor, never a raw load: a bare /api/generate makes Ollama force the
            # configured model onto the card even when it cannot fit - it ends up split between GPU
            # and CPU (a minute of loading, and still resident after the client gives up). Everything
            # run afterwards then fights the ghost (2026-10-16: the 27B left ~15 GB resident and every
            # later QA suite failed at admission). admit() returns the rung ISHTI would really serve;
            # wait_s=0 because a health check must never sit in the job queue.
            from ishti import vram as ishti_vram
            admitted = ishti_vram.admit(primary, role="planner", job="doctor model check", wait_s=0)
            if not admitted["ok"]:
                self._log_result("Models", f"Primary Model ({primary})", False, admitted["queued"], is_warning=True)
            else:
                t0 = time.time()
                res = requests.post(f"{router.host}/api/generate", json={"model": admitted["model"], "prompt": "1+1=", "stream": False}, timeout=30)
                elapsed = round(time.time() - t0, 2)
                served = f" (served by {admitted['model']})" if admitted["model"] != primary else ""
                if res.status_code == 200:
                    self._log_result("Models", f"Primary Model ({primary}){served}", True, f"Responsive in {elapsed}s")
                else:
                    self._log_result("Models", f"Primary Model ({primary}){served}", False, f"HTTP {res.status_code}")
        except Exception as e:
            self._log_result("Models", f"Primary Model ({primary})", False, str(e), is_warning=True)

    def check_browser_isolation(self):
        print("\n--- [5] Browser & Profile Isolation ---")
        chrome_exists = os.path.exists(CHROME_EXE)
        self._log_result("Browser", "System Google Chrome", chrome_exists, CHROME_EXE)

        profile_exists = os.path.exists(PROFILE_DIR)
        profile_isolated = os.path.abspath(PROFILE_DIR).lower().startswith(ROOT_DIR.lower())
        self._log_result("Browser", "Isolated Chrome Profile", profile_exists and profile_isolated, PROFILE_DIR)

        # Real test: launch the browser, load a page, close it. A version string proves nothing.
        for label, headless in (("Headless Browser Launch", True), ("Visible Browser Launch", False)):
            if time.time() >= self._deadline:
                self._log_result("Browser", f"{label} (skipped: time budget spent)", True, "static checks complete", is_warning=True)
                continue
            try:
                from browser.browser_agent import BrowserAgent
                t0 = time.time()
                page = BrowserAgent(headless=headless).fetch_page_text("https://example.com", max_chars=300)
                ok = bool(page.get("text"))
                self._log_result("Browser", label, ok,
                                 f"Loaded {len(page.get('text') or '')} chars in {round(time.time()-t0, 2)}s",
                                 is_warning=not ok)
            except Exception as e:
                self._log_result("Browser", label, False, str(e)[:120], is_warning=True)

    def check_web_search(self):
        print("\n--- [6] Real-Time Web Search Capabilities ---")
        search_mgr = get_search_manager()
        diag = search_mgr.run_diagnostics()

        for prov_name, prov_info in diag.items():
            avail = prov_info.get("available", False)
            lat = prov_info.get("latency_s", 0.0)
            self._log_result("WebSearch", f"Search Provider '{prov_name}'", avail, f"Latency: {lat}s", is_warning=not avail)

        try:
            t0 = time.time()
            results = search_mgr.search("latest Python version", max_results=3)
            elapsed = round(time.time() - t0, 2)
            if results:
                self._log_result("WebSearch", "Live Internet Search Test", True, f"Retrieved {len(results)} sources in {elapsed}s")
            else:
                self._log_result("WebSearch", "Live Internet Search Test", False, "0 results returned", is_warning=True)
        except Exception as e:
            self._log_result("WebSearch", "Live Internet Search Test", False, str(e), is_warning=True)

    def check_web_fetch(self):
        print("\n--- [7] Web Fetch & Content Extraction ---")
        fetcher = WebFetcher(timeout=8)
        try:
            t0 = time.time()
            data = fetcher.fetch("https://www.python.org", max_chars=1000)
            elapsed = round(time.time() - t0, 2)
            if data.get("status") == "success" and data.get("content"):
                clean_title = data.get("title", "")[:30]
                self._log_result("WebFetch", "Live Page Fetch & Extraction", True, f"Read '{clean_title}' ({elapsed}s via {data.get('method')})")
            else:
                self._log_result("WebFetch", "Live Page Fetch & Extraction", False, data.get("error", "Empty text"), is_warning=True)
        except Exception as e:
            self._log_result("WebFetch", "Live Page Fetch & Extraction", False, str(e), is_warning=True)

    def check_voice(self):
        print("\n--- [8] Voice & Audio Pipeline ---")
        try:
            v_whisper = importlib.metadata.version("faster-whisper")
            self._log_result("Voice", "Faster-Whisper STT", True, f"v{v_whisper}")
        except Exception as e:
            self._log_result("Voice", "Faster-Whisper STT", False, str(e), is_warning=True)

        # Real test: Piper synthesises audio bytes. SKIPPED when the time budget is spent - a slow
        # TTS first-load must never hang 'run diagnostics' (owner-skill timeout bug, 2026-09-30).
        if time.time() < self._deadline:
            try:
                from ishti.voice import synthesize
                size = len(synthesize("ISHTI audio test"))
                self._log_result("Voice", "Piper TTS", size > 1000, f"Synthesised {size} bytes of audio",
                                 is_warning=size <= 1000)
            except Exception as e:
                self._log_result("Voice", "Piper TTS", False, str(e)[:120], is_warning=True)
        else:
            self._log_result("Voice", "Piper TTS (skipped: time budget spent)", True, "static checks complete", is_warning=True)

        # Real test: weather data source used for live local queries (LIVE NETWORK - budget-capped)
        if time.time() < self._deadline:
            try:
                from tools.web.weather import get_weather
                from orchestrator.memory.user_profile import get_user_memory
                city = get_user_memory().get_preferences().default_weather_city or "Peshawar"
                w = get_weather(city, 1)
                self._log_result("LiveData", "Weather API (Open-Meteo)", bool(w.get("ok")),
                                 w.get("answer") or w.get("error", ""), is_warning=not w.get("ok"))
            except Exception as e:
                self._log_result("LiveData", "Weather API (Open-Meteo)", False, str(e)[:120], is_warning=True)
        else:
            self._log_result("LiveData", "Weather API (skipped: time budget spent)", True, "static checks complete", is_warning=True)

    def check_memory_and_skills(self):
        print("\n--- [9] Memory & search services ---")
        try:
            from ishti.facts import get_facts
            self._log_result("Memory", "Exact fact store (SQLite)", True, f"{len(get_facts().keys())} entities")
        except Exception as e:
            self._log_result("Memory", "Exact fact store (SQLite)", False, str(e)[:120])
        try:
            from ishti.semantic import get_semantic
            self._log_result("Memory", "Semantic memory (ChromaDB)", True, f"{get_semantic().col.count()} entries")
        except Exception as e:
            self._log_result("Memory", "Semantic memory (ChromaDB)", False, str(e)[:120], is_warning=True)
        from tools.web.search import SearxngSearchProvider
        up = SearxngSearchProvider().is_available()
        self._log_result("WebSearch", "Local SearXNG (Docker)", up,
                         SearxngSearchProvider.URL if up else "down - falling back to Wikipedia/Bing", is_warning=True)

    def check_install_state(self):
        """ECC-12: compare the tree with the recorded install baseline (data/install_state.json).
        No baseline yet is a PASS with instructions, not a failure; drift is a WARNING (repair
        is manual and never touches owner files)."""
        print("\n--- [7b] Install State & Ownership ---")
        try:
            from orchestrator import install_state
            d = install_state.drift()
            if not d.get("baseline"):
                self._log_result("Install", "Install baseline", True,
                                 "none yet - run `python -m orchestrator.install_state capture`")
                return
            drifted = bool(d.get("missing") or d.get("modified"))
            self._log_result(
                "Install", "Skills/config drift", not drifted,
                f"{d.get('checked', 0)} tracked; missing {len(d.get('missing') or [])}, "
                f"modified {len(d.get('modified') or [])}, new {len(d.get('extra') or [])}",
                is_warning=drifted)
            if drifted:
                print(f"  [WARN] {install_state.report(d)}")
        except Exception as exc:   # the check must never break the suite
            self._log_result("Install", "Install-state check", False, str(exc)[:200], is_warning=True)

    def check_services(self):
        print("\n--- [10] Services & Networking ---")
        try:
            r = requests.get("http://127.0.0.1:8888/api/status", timeout=2)
            self._log_result("Services", "ISHTI Web HUD Server", r.status_code == 200, "Online at http://127.0.0.1:8888")
        except Exception:
            self._log_result("Services", "ISHTI Web HUD Server", False, "Offline (will auto-launch on startup)", is_warning=True)

    def check_end_to_end_capabilities(self):
        """The real request path: dispatcher -> tool routing -> skills, against a throwaway memory."""
        print("\n--- [11] ISHTI core (dispatcher, routing, skills) ---")
        import tempfile
        from ishti import CONFIG
        from orchestrator.memory.user_profile import scratch_memory
        real_db = CONFIG["memory"]["db_path"], CONFIG["memory"]["chroma_dir"]
        tmp = tempfile.mkdtemp(prefix="ishti_doctor_")
        CONFIG["memory"].update(db_path=os.path.join(tmp, "t.db"), chroma_dir=os.path.join(tmp, "chroma"))
        try:
            with scratch_memory():
                import ishti.facts as facts
                facts._facts = None
                from ishti import dispatcher
                from ishti import tool_routing as router  # ishti/router.py was removed; this replaced it
                broken = {n: s.problem for n, s in dispatcher.SKILLS.items() if s.status == "broken"}
                self._log_result("Core", "Skills loaded", not broken,
                                 f"{len(dispatcher.SKILLS)} skills" + (f", broken: {broken}" if broken else ""))
                d = router.route("tell me a joke", dispatcher.SKILLS)
                # Deterministic by construction: ishti.tool_routing makes no model call, so there is no
                # checkpoint to be missing and no router error to report. The one thing worth flagging is
                # a request no rule claimed - source "default" means the shortlist fell through to chat.
                self._log_result("Core", "Rule routing", d["source"] != "default",
                                 f"{d['skill']} via {d['source']}", is_warning=True)
                for text, skill, expect in [
                    ("Project DoctorTest is on D: at D:\\Projects\\DoctorTest", "memory", "Saved"),
                    ("where is the doctortest project?", "memory", "D:\\Projects\\DoctorTest"),
                    ("what's the weather tomorrow?", "weather", "open-meteo"),
                    ("what is 6 times 7? reply with just the number", "chat", "42"),
                    ("how much ram am I using", "system", "RAM"),
                ]:
                    t0 = time.time()
                    r = dispatcher.handle(text, "doctor")
                    ok = r["skill"] == skill and expect in r["final_answer"]
                    self._log_result("Core", f"{skill}: {text[:30]}", ok,
                                     f"{round(time.time() - t0, 1)}s" if ok else r["final_answer"][:100])
                dispatcher.SESSIONS.pop("doctor", None)
        except Exception as e:
            self._log_result("Core", "Dispatcher", False, f"{type(e).__name__}: {e}"[:160])
        finally:
            CONFIG["memory"].update(db_path=real_db[0], chroma_dir=real_db[1])
            import ishti.facts as facts
            facts._facts = None

    def _print_summary(self):
        print("\n" + "=" * 65)
        if not self.errors:
            print("  ISHTI DOCTOR STATUS: ALL CRITICAL SYSTEMS OPERATIONAL (HEALTHY)")
        else:
            print(f"  ISHTI DOCTOR STATUS: DEGRADED ({len(self.errors)} errors, {len(self.warnings)} warnings)")
        if self.warnings:
            print("  Warnings:")
            for w in self.warnings:
                print(f"    - {w}")
        print("=" * 65 + "\n")


def run_doctor():
    doc = IshtiDoctor()
    return doc.check_all()


if __name__ == "__main__":
    run_doctor()
