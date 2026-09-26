"""
ISHTI Parallel Agent Orchestration & Execution Engine
Executes planned DAG stages concurrently using multi-agent workers,
integrates real-time internet search & deep research, self-healing diagnostics,
human-in-the-loop confirmation gates, and persistent skill/research memory.
"""

import os
import sys
import time
import json
import re
import concurrent.futures
import subprocess
from typing import Dict, Any, List, Optional, Callable
import requests

ROOT_DIR = r"D:\ISHTI"
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from orchestrator.planner import Planner, TaskPlan
from orchestrator.model_router import ModelRouter
from orchestrator.self_heal import SelfHealEngine
from orchestrator.intent_detector import IntentDetector
from orchestrator.deep_research import DeepResearchEngine
from tools.web.search import get_search_manager
from tools.web.fetch import WebFetcher
from orchestrator.memory.conversation import get_conversation_manager
from orchestrator.memory.error_logger import ErrorLogger
from orchestrator.memory.user_profile import get_user_memory
from orchestrator.memory.capability_registry import get_capability_registry
from orchestrator.memory.task_state import TaskStateMachine, get_task_state_machine, TaskState
from browser.browser_agent import should_use_visible_browser
from orchestrator.response_guard import inspect, is_safe_for_history, fallback_from_evidence, clean_history
from tools.web.weather import get_weather, extract_city, which_day
from desktop.computer_use import get_computer_use_agent
from tools.video.video_editor import get_video_editor

SKILLS_DIR = os.path.join(ROOT_DIR, "orchestrator", "memory", "skills")
REFLECTIONS_PATH = os.path.join(ROOT_DIR, "orchestrator", "memory", "reflections.md")


class ExecutionEngine:
    def __init__(
        self,
        confirmation_callback: Optional[Callable[[Dict[str, Any]], bool]] = None,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        self.model_router = ModelRouter()
        self.self_heal = SelfHealEngine(model_router=self.model_router)
        self.planner = Planner()
        self.confirmation_callback = confirmation_callback
        self.event_callback = event_callback

        self.search_manager = get_search_manager()
        self.fetcher = WebFetcher(timeout=10)
        self.deep_research = DeepResearchEngine(event_callback=self.event_callback)
        self.user_memory = get_user_memory()
        self.capability_registry = get_capability_registry()
        self.task_state_machine = get_task_state_machine()

        self._browser_agent = None
        self._browser_headless_default = True

    @property
    def browser_agent(self):
        if self._browser_agent is None:
            from browser.browser_agent import BrowserAgent
            self._browser_agent = BrowserAgent(headless=self._browser_headless_default)
        return self._browser_agent
    
    def get_browser_agent(self, headless: Optional[bool] = None) -> "BrowserAgent":
        """Get browser agent with optional headless override."""
        from browser.browser_agent import BrowserAgent
        if headless is None:
            headless = self._browser_headless_default
        return BrowserAgent(headless=headless)

    def emit(self, event_type: str, data: Dict[str, Any]) -> None:
        """Emits event to UI websocket or terminal."""
        if self.event_callback:
            try:
                self.event_callback(event_type, data)
            except Exception as e:
                print(f"[ExecutionEngine] Event callback error: {e}")
        print(f"[EVENT] [{event_type.upper()}] {json.dumps(data, ensure_ascii=False)[:200]}")

    def process_request(self, user_prompt: str, session_id: str = None) -> Dict[str, Any]:
        """
        Main routing gateway:
        1. Retrieves conversation history AND persistent user memory for context.
        2. Classifies intent (Quick Search vs Deep Research vs Web Fetch vs DAG Plan vs Local Knowledge).
        3. Consults capability registry for available tools.
        4. Routes to specialized execution pipeline.
        5. Saves user + ISHTI messages to persistent session.
        6. Routes all errors through ErrorLogger for durable auditing.
        """
        # Check for owner commands first
        owner_cmd_result = self._handle_owner_command(user_prompt)
        if owner_cmd_result:
            return owner_cmd_result

        conv_mgr = get_conversation_manager()
        if session_id:
            conv_mgr.active_session_id = session_id
        active_sid = conv_mgr.active_session_id or conv_mgr.new_chat("New Session")

        # Save user message
        conv_mgr.add_message(active_sid, "user", user_prompt)

        # Get conversation history for LLM context injection
        history_text = conv_mgr.get_history_text(active_sid, max_turns=6)

        # Retrieve relevant persistent user memory (MEMORY-FIRST ARCHITECTURE)
        memory_context = self.user_memory.format_for_llm(user_prompt)

        # Store current user prompt for browser visibility detection
        self._current_user_prompt = user_prompt

        # Check capability registry for available tools
        relevant_capabilities = self.capability_registry.can_handle(user_prompt)
        cap_names = [c.name for c in relevant_capabilities]

        try:
            classification = IntentDetector.classify(user_prompt)
            mode = classification.get("mode")
            depth = classification.get("depth", "SIMPLE")
            print(f"[ExecutionEngine] Routing request to: {mode} (depth={depth}, triggers={classification.get('triggers', [])})")
            print(f"[ExecutionEngine] Relevant capabilities: {cap_names}")

            # Route 0: Greeting / small talk. No tools, no history replay, one sentence.
            if mode == "GREETING":
                answer = self.model_router.generate(user_prompt, depth="SIMPLE", temperature=0.4)
                result = {
                    "request": user_prompt, "mode": "GREETING", "session_id": active_sid,
                    "final_answer": answer or f"Hey {self.user_memory.get_profile().name.split()[0]}, all good here.",
                    "sources": [], "verified": True, "subtask_results": [],
                }

            # Route 0b: Weather. Real forecast API with the owner's city - never a scraped page.
            elif mode == "WEATHER":
                result = self._execute_weather(user_prompt, active_sid)

            # Route 0c: Visible browser search the owner can watch.
            elif mode == "BROWSER_VISIBLE":
                result = self._execute_visible_browser_search(user_prompt, active_sid)

            # Route 0d: Deep PC File & Directory Search
            elif mode == "PC_SEARCH":
                result = self._execute_pc_search(user_prompt, active_sid)

            # Route 0e: Desktop Application Control & Window Management
            elif mode == "APP_CONTROL":
                result = self._execute_app_control(user_prompt, active_sid)

            # Route 0f: Video Editing Operations (FFmpeg & NVENC GPU Acceleration)
            elif mode == "VIDEO_EDIT":
                result = self._execute_video_edit(user_prompt, active_sid)

            # Route 0g: Browser Form Filling & Interactive Web Tasks
            elif mode == "BROWSER_FORM":
                result = self._execute_browser_form(user_prompt, active_sid, url=classification.get("url"))

            # Route 0h: Host System Telemetry (CPU/RAM/GPU/Disks)
            elif mode == "PC_TELEMETRY":
                result = self._execute_pc_telemetry(user_prompt, active_sid)

            # Route 1: Deep Research (only when explicitly requested)
            elif mode == "DEEP_RESEARCH":
                self.emit("view_navigate", {"target_view": "RESEARCH", "topic": user_prompt})
                research_result = self.deep_research.run_deep_research(user_prompt)
                result = {
                    "request": user_prompt,
                    "mode": "DEEP_RESEARCH",
                    "session_id": active_sid,
                    "final_answer": research_result.get("report", ""),
                    "sources": research_result.get("sources", []),
                    "queries": research_result.get("queries", []),
                    "verified": research_result.get("verified", False),
                    "subtask_results": [],
                }

            # Route 2: Quick Search
            elif mode == "QUICK_SEARCH":
                self.emit("view_navigate", {"target_view": "CHAT", "query": user_prompt})
                quick_result = self.deep_research.quick_search(user_prompt, depth=depth)
                result = {
                    "request": user_prompt,
                    "mode": "QUICK_SEARCH",
                    "session_id": active_sid,
                    "final_answer": quick_result.get("answer", ""),
                    "sources": quick_result.get("sources", []),
                    "verified": quick_result.get("verified", False),
                    "subtask_results": [],
                }

            # Route 3: Direct Web Fetch
            elif mode == "WEB_FETCH":
                url = classification.get("url")
                self.emit("status_change", {"state": "SEARCHING", "detail": f"Fetching online URL: {url}"})
                fetched = self.fetcher.fetch(url)
                summary = self._summarize_web_page(user_prompt, fetched)
                sources = [{
                    "id": 1,
                    "title": fetched.get("title", url),
                    "domain": url.split("//")[-1].split("/")[0],
                    "url": url,
                    "snippet": fetched.get("content", "")[:200],
                    "verified": fetched.get("status") == "success",
                }]
                result = {
                    "request": user_prompt,
                    "mode": "WEB_FETCH",
                    "session_id": active_sid,
                    "final_answer": summary,
                    "sources": sources,
                    "verified": fetched.get("status") == "success",
                    "subtask_results": [],
                }

            # Route 4: Standard Multi-step DAG Task (or Local Knowledge)
            else:
                plan = self.planner.create_plan(user_prompt, history_context=history_text)
                is_substantial = len(plan.stages) > 1 or any(len(s) > 1 for s in plan.stages)
                if is_substantial:
                    self.emit("view_navigate", {"target_view": "EXECUTIONS", "task": user_prompt})
                result = self.execute_plan(plan)
                result["session_id"] = active_sid

            # Trust boundary: never show - and never persist - a refusal or a hallucinated
            # "classified/intel" reply when the tools actually ran (docs/AUDIT.md F1, F5).
            result["final_answer"] = self._guard_answer(
                result.get("final_answer", ""), user_prompt, result.get("sources", []), depth
            )

            answer_text = result["final_answer"]
            if is_safe_for_history(answer_text):
                conv_mgr.add_message(active_sid, "ishti", answer_text, sources=result.get("sources"))
            else:
                print("[ExecutionEngine] Reply withheld from history: still contaminated after guard.")
            return result

        except Exception as exc:
            ErrorLogger.log_error(
                subsystem="CREW",
                error=exc,
                user_prompt=user_prompt,
                context=f"process_request routing mode={classification.get('mode', 'UNKNOWN') if 'classification' in dir() else 'PRE_CLASSIFY'}",
            )
            fallback_answer = f"I encountered an error processing your request. The error has been logged for review. Details: {str(exc)[:200]}"
            conv_mgr.add_message(active_sid, "ishti", fallback_answer)
            return {
                "request": user_prompt,
                "mode": "ERROR",
                "session_id": active_sid,
                "final_answer": fallback_answer,
                "sources": [],
                "subtask_results": [],
            }

    def _guard_answer(self, answer: str, user_prompt: str, sources: List[Dict[str, Any]], depth: str) -> str:
        """
        Blocks refusals and hallucination markers. One forced retry through the unified
        prompt path; if that still fails, answer from the evidence itself rather than
        showing the owner a refusal.
        """
        ok, hits, why = inspect(answer)
        if ok:
            return answer

        print(f"[Guard] Blocked response ({why}): {hits}. Re-running.")
        self.emit("guard_blocked", {"reason": why, "matched": hits})

        evidence = ""
        if sources:
            evidence = "\n".join(
                f"[{i+1}] {s.get('title','')} - {s.get('snippet','')} ({s.get('url','')})"
                for i, s in enumerate(sources[:4])
            )

        retry_prompt = (
            f"{user_prompt}\n\n"
            + (f"Live results already retrieved for you:\n{evidence}\n\n" if evidence else "")
            + "Answer the question directly from the information above. Do not say you lack "
              "internet access or real-time data. Do not use any classified, intelligence or "
              "briefing language."
        )
        retried = self.model_router.generate(
            retry_prompt, depth=depth, tools_used=["web_search"] if sources else None,
            evidence_is_live=bool(sources), memory_query=user_prompt, temperature=0.1,
        )
        if retried and inspect(retried)[0]:
            return retried

        fallback = fallback_from_evidence(sources, user_prompt)
        if fallback:
            return fallback
        return ("I ran the request but the local model kept returning an invalid response. "
                "The tools themselves reported no error - try rephrasing, or run 'ishti doctor'.")

    def _execute_weather(self, user_prompt: str, session_id: str) -> Dict[str, Any]:
        """Weather comes from a forecast API, with the owner's city when none is named."""
        city = extract_city(user_prompt)
        if not city:
            prefs = self.user_memory.get_preferences()
            profile = self.user_memory.get_profile()
            city = prefs.default_weather_city or (profile.location or {}).get("city", "")
        self.emit("status_change", {"state": "SEARCHING", "detail": f"Fetching live forecast for {city}"})

        data = get_weather(city, which_day(user_prompt))

        # Verification: only report success if the payload really carries a temperature for that city.
        verified = bool(data.get("ok")) and data.get("temp_max") is not None \
            and city.lower()[:4] in data.get("city", "").lower()

        if verified:
            answer = data["answer"]
            sources = [{"id": 1, "title": f"Open-Meteo forecast for {data['city']}",
                        "domain": "open-meteo.com", "url": data["source"],
                        "snippet": f"{data['date']}: {data['temp_max']}C / {data['temp_min']}C, {data['condition']}",
                        "verified": True}]
        else:
            answer = f"The forecast service did not return data for {city}: {data.get('error', 'unknown error')}."
            sources = []

        return {"request": user_prompt, "mode": "WEATHER", "session_id": session_id,
                "final_answer": answer, "sources": sources, "verified": verified,
                "subtask_results": []}

    def _execute_visible_browser_search(self, user_prompt: str, session_id: str) -> Dict[str, Any]:
        """Owner asked to watch: launch the real visible window, then verify it ran."""
        query = re.sub(
            r"^.*?(?:browser|watch)\b\s*(?:and\s+)?(?:search(?:\s+(?:for|on))?|open|go to|look up)?\s*",
            "", user_prompt, flags=re.IGNORECASE
        ).strip() or user_prompt
        self.emit("status_change", {"state": "BROWSING", "detail": f"Opening visible browser for: {query}"})

        browser = self.get_browser_agent(headless=False)
        results = browser.search_duckduckgo(query, max_results=4, headless=False)
        verified = bool(results) and all(r.get("url") for r in results)

        sources = [{"id": i + 1, "title": r.get("title", ""), "domain": r.get("url", "").split("/")[2] if "/" in r.get("url", "") else "",
                    "url": r.get("url", ""), "snippet": r.get("snippet", ""), "verified": True}
                   for i, r in enumerate(results)]

        if verified:
            answer = self.model_router.generate(
                f"{query}\n\nLive results from the visible browser:\n" + "\n".join(
                    f"[{i+1}] {r['title']} - {r['snippet']} ({r['url']})" for i, r in enumerate(results)),
                depth="SIMPLE", tools_used=["browser_visible"], evidence_is_live=True,
                memory_query=user_prompt,
            ) or fallback_from_evidence(sources, query)
        else:
            answer = f"The visible browser opened but returned no results for '{query}'."

        return {"request": user_prompt, "mode": "BROWSER_VISIBLE", "session_id": session_id,
                "final_answer": answer, "sources": sources, "verified": verified,
                "subtask_results": [], "browser_headless": False}

    def _execute_pc_search(self, user_prompt: str, session_id: str) -> Dict[str, Any]:
        """Deep searches PC user folders (Desktop, Downloads, Documents, etc.) and project drives."""
        agent = get_computer_use_agent()
        self.emit("status_change", {"state": "SEARCHING", "detail": f"Scanning PC storage for: {user_prompt}"})

        # Extract target query
        query = re.sub(r"^(?:search|find|locate|where is|look)\b.*?\b(?:for|file|pc)\s+(?:for\s+)?", "", user_prompt, flags=re.IGNORECASE).strip(" .?!'\"")
        query = re.sub(r"\b(?:on\s+pc|on\s+my\s+pc|in\s+pc)\b", "", query, flags=re.IGNORECASE).strip(" .?!'\"")
        if not query:
            query = user_prompt

        # Extract extension if any
        ext_match = re.findall(r"\.([a-zA-Z0-9]+)\b", user_prompt)
        exts = [f".{e.lower()}" for e in ext_match] if ext_match else None

        results = agent.search_pc(query, extensions=exts, max_results=10)
        verified = bool(results)

        if results:
            lines = [f"Found {len(results)} matching files on your PC for '{query}':\n"]
            for i, r in enumerate(results, 1):
                snippet_line = f"\n   *Match:* {r['snippet']}" if r.get("snippet") else ""
                lines.append(f"{i}. **{r['name']}** ({r['size']}, modified {r['modified']})\n   Path: `{r['path']}`{snippet_line}")
            answer = "\n".join(lines)
        else:
            answer = f"I scanned your user folders and project directories, but did not find any files matching '{query}'."

        return {
            "request": user_prompt, "mode": "PC_SEARCH", "session_id": session_id,
            "final_answer": answer, "sources": [], "files": results, "verified": verified,
            "subtask_results": [],
        }

    def _execute_pc_search_subtask(self, target: str) -> str:
        agent = get_computer_use_agent()
        res = agent.search_pc(target, max_results=5)
        if not res:
            return f"No PC files found matching '{target}'."
        return f"Found {len(res)} files matching '{target}':\n" + "\n".join([f"- {r['name']} (`{r['path']}`, {r['size']})" for r in res])

    def _execute_app_control(self, user_prompt: str, session_id: str) -> Dict[str, Any]:
        """Launches desktop applications or controls windows."""
        agent = get_computer_use_agent()
        self.emit("status_change", {"state": "EXECUTING", "detail": f"Controlling desktop app: {user_prompt}"})

        app_target = re.sub(r"^.*?(?:open|launch|start|switch to|focus)\s+", "", user_prompt, flags=re.IGNORECASE).strip(" .?!'\"")
        if not app_target:
            app_target = "notepad"

        res = agent.launch_app(app_target)
        if res.get("status") == "success":
            answer = f"I have launched **{app_target}** on your desktop."
            verified = True
        else:
            focused = agent.focus_window(app_target)
            if focused:
                answer = f"I have brought the **{app_target}** window to the foreground."
                verified = True
            else:
                answer = f"I could not launch or find **{app_target}**: {res.get('error', 'App not recognized')}."
                verified = False

        return {
            "request": user_prompt, "mode": "APP_CONTROL", "session_id": session_id,
            "final_answer": answer, "sources": [], "verified": verified,
            "subtask_results": [],
        }

    def _execute_app_control_subtask(self, target: str) -> str:
        agent = get_computer_use_agent()
        res = agent.launch_app(target)
        if res.get("status") == "success":
            return f"Successfully launched {target} (PID: {res.get('pid', 'N/A')})."
        return f"Failed to launch {target}: {res.get('error')}"

    def _execute_video_edit(self, user_prompt: str, session_id: str) -> Dict[str, Any]:
        """Executes video editing workflows via VideoEditor with GPU acceleration."""
        editor = get_video_editor()
        self.emit("status_change", {"state": "EXECUTING", "detail": "Executing video editing operation with GPU acceleration"})

        host = self.model_router.host
        fast_model = self.model_router.get_model("fast")
        extract_prompt = (
            f"Analyze this video editing instruction and output strictly a JSON object with keys:\n"
            f"- operation: one of ('trim', 'merge', 'extract_audio', 'convert_vertical', 'change_speed', 'compress', 'thumbnail', 'info')\n"
            f"- input_file: string path or file name\n"
            f"- start_time: string (e.g. '00:00:10') or null\n"
            f"- end_time: string (e.g. '00:01:00') or null\n"
            f"- speed: float or null\n"
            f"- target_size_mb: float or null\n"
            f"Instruction: {user_prompt}\nJSON:"
        )

        params = {"operation": "info", "input_file": ""}
        try:
            r = requests.post(f"{host}/api/generate", json={"model": fast_model, "prompt": extract_prompt, "stream": False, "options": {"temperature": 0.1}}, timeout=10)
            if r.status_code == 200:
                raw = r.json().get("response", "")
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                if m:
                    params = json.loads(m.group(0))
        except Exception:
            pass

        input_file = params.get("input_file", "")
        if input_file and not os.path.exists(input_file):
            found = get_computer_use_agent().search_pc(input_file, max_results=1)
            if found:
                input_file = found[0]["path"]

        op = params.get("operation", "info")
        res = {"status": "error", "error": "Operation could not be parsed."}

        if not input_file or not os.path.exists(input_file):
            answer = f"I recognized the video editing request ({op}), but could not find the source video file '{input_file or 'unspecified'}'. Please provide the file name or path."
            verified = False
        else:
            if op == "trim":
                res = editor.trim_video(input_file, start_time=params.get("start_time") or "00:00:00", end_time=params.get("end_time"))
            elif op == "extract_audio":
                res = editor.extract_audio(input_file)
            elif op == "convert_vertical":
                res = editor.convert_to_vertical(input_file)
            elif op == "change_speed":
                res = editor.change_speed(input_file, speed_factor=float(params.get("speed") or 2.0))
            elif op == "compress":
                res = editor.compress_video(input_file, target_size_mb=float(params.get("target_size_mb") or 25.0))
            elif op == "thumbnail":
                res = editor.extract_thumbnail(input_file, timestamp=params.get("start_time") or "00:00:01")
            else:
                res = editor.get_media_info(input_file)

            if res.get("status") == "success" or "duration_seconds" in res:
                out_path = res.get("output_path", input_file)
                answer = f"Video editing operation **{op}** completed successfully!\n- **Output File**: `{out_path}`\n- **GPU Accelerated**: {editor.has_nvenc}"
                verified = True
            else:
                answer = f"Video editing operation failed: {res.get('error', 'FFmpeg processing error')}"
                verified = False

        return {
            "request": user_prompt, "mode": "VIDEO_EDIT", "session_id": session_id,
            "final_answer": answer, "sources": [], "result": res, "verified": verified,
            "subtask_results": [],
        }

    def _execute_video_edit_subtask(self, target: str) -> str:
        res = self._execute_video_edit(target, "")
        return res.get("final_answer", "")

    def _execute_browser_form(self, user_prompt: str, session_id: str, url: Optional[str] = None) -> Dict[str, Any]:
        """Fills forms on web pages and submits."""
        self.emit("status_change", {"state": "BROWSING", "detail": f"Automating browser form submission for: {url or 'target page'}"})

        if not url:
            url_m = re.search(r"https?://[^\s]+", user_prompt)
            url = url_m.group(0) if url_m else "https://duckduckgo.com"

        host = self.model_router.host
        fast_model = self.model_router.get_model("fast")
        extract_prompt = (
            f"Extract the form fields and values the user wants to input. Output strictly a JSON object of field_name: value.\n"
            f"User Prompt: {user_prompt}\nJSON:"
        )

        fields = {}
        try:
            r = requests.post(f"{host}/api/generate", json={"model": fast_model, "prompt": extract_prompt, "stream": False, "options": {"temperature": 0.1}}, timeout=10)
            if r.status_code == 200:
                raw = r.json().get("response", "")
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                if m:
                    fields = json.loads(m.group(0))
        except Exception:
            pass

        browser = self.get_browser_agent(headless=False)
        form_res = browser.fill_and_submit_form(url=url, fields=fields, headless=False)

        verified = form_res.get("status") == "success" and form_res.get("submitted", False)
        screenshot = form_res.get("screenshot")
        answer = (
            f"I have opened the browser, navigated to `{url}`, and filled the requested fields: {list(fields.keys())}.\n"
            f"Form submitted: **{form_res.get('submitted', False)}**.\n"
            f"Page Title: {form_res.get('page_title', '')}\n"
            + (f"Verification Screenshot: `{screenshot}`" if screenshot else "")
        )

        return {
            "request": user_prompt, "mode": "BROWSER_FORM", "session_id": session_id,
            "final_answer": answer, "sources": [], "form_data": form_res, "verified": verified,
            "subtask_results": [],
        }

    def _execute_browser_form_subtask(self, target: str) -> str:
        res = self._execute_browser_form(target, "")
        return res.get("final_answer", "")

    def _execute_pc_telemetry(self, user_prompt: str, session_id: str) -> Dict[str, Any]:
        """Gathers and formats real-time PC hardware telemetry."""
        agent = get_computer_use_agent()
        data = agent.get_system_telemetry()

        gpu_info = data.get("gpu", {})
        gpu_str = f"{gpu_info.get('name', 'NVIDIA GPU')} ({gpu_info.get('used_vram_mb', 0)}MB / {gpu_info.get('total_vram_mb', 16384)}MB VRAM, {gpu_info.get('temp_c', 0)}°C)" if isinstance(gpu_info, dict) else str(gpu_info)
        disks_str = ", ".join([f"{k} ({v.get('free_gb', 0)}GB free of {v.get('total_gb', 0)}GB)" for k, v in data.get("disks", {}).items()])

        answer = (
            f"**Host Hardware Telemetry & Health Status:**\n\n"
            f"- **CPU Usage**: {data.get('cpu_percent', 0)}% across {data.get('cpu_count', 0)} cores\n"
            f"- **RAM Usage**: {data.get('ram_used_gb', 0)} GB used of {data.get('ram_total_gb', 0)} GB ({data.get('ram_percent', 0)}%)\n"
            f"- **GPU Status**: {gpu_str}\n"
            f"- **Storage Drives**: {disks_str}\n"
            f"- **Active Sandbox**: `D:\\ISHTI` (Isolated Profile & Data Stores Enforced)"
        )

        return {
            "request": user_prompt, "mode": "PC_TELEMETRY", "session_id": session_id,
            "final_answer": answer, "sources": [], "telemetry": data, "verified": True,
            "subtask_results": [],
        }

    def _execute_system_command(self, command: str) -> str:
        """Executes system command within the project workspace safely."""
        print(f"[ExecutionEngine] Executing system command: {command}")
        dangerous = ["format ", "del /f /s /q c:", "rmdir /s /q c:", "drop database", "shutdown "]
        if any(d in command.lower() for d in dangerous):
            raise PermissionError(f"Command '{command}' rejected: dangerous operation detected.")

        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                cwd=ROOT_DIR,
                capture_output=True,
                text=True,
                timeout=30,
            )
            out = res.stdout.strip()
            err = res.stderr.strip()
            if res.returncode == 0:
                return out or "Command executed successfully with no output."
            return f"Command exited with code {res.returncode}:\nOutput: {out}\nError: {err}"
        except Exception as e:
            return f"Failed to execute system command: {e}"

    def _summarize_web_page(self, user_prompt: str, fetched_data: Dict[str, Any]) -> str:
        content = fetched_data.get("content", "")[:3500]
        title = fetched_data.get("title", "")
        url = fetched_data.get("url", "")

        answer = self.model_router.generate(
            f"{user_prompt}\n\nPage [1]: {title} ({url})\n{content}",
            depth="MODERATE", tools_used=["web_fetch"], evidence_is_live=True,
            memory_query=user_prompt,
        )
        return answer or f"Summary of {title} ({url}):\n\n{content[:600]}..."

    def run_subtask(self, task: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Executes a single subtask with self-healing and tool integration."""
        task_id = task.get("id", "step_0")
        name = task.get("name", "Subtask")
        action_type = task.get("action_type", "synthesis")
        target = task.get("query_or_target", "")
        t0 = time.time()

        self.emit("task_started", {"task_id": task_id, "name": name, "type": action_type})
        model = self.model_router.get_model("primary")

        # Consequential action gate check. Shell commands are always gated, whatever the planner said.
        if task.get("is_consequential") or action_type == "system_command":
            self.emit("consequential_check", {
                "task_id": task_id,
                "name": name,
                "details": task.get("consequential_details", target),
            })
            approved = self._request_user_confirmation(task)
            if not approved:
                self.emit("task_aborted", {"task_id": task_id, "reason": "User rejected consequential action."})
                return {
                    "task_id": task_id,
                    "name": name,
                    "status": "rejected_by_user",
                    "output": "Action halted by user confirmation gate.",
                    "duration_s": round(time.time() - t0, 2),
                }

        # Dispatch action based on action_type
        def action_runner():
            if action_type == "browser_research":
                return self._execute_browser_research(target, user_prompt=getattr(self, '_current_user_prompt', ''))
            elif action_type == "file_analysis":
                return self._execute_file_analysis(target)
            elif action_type == "pc_search":
                return self._execute_pc_search_subtask(target)
            elif action_type == "app_control":
                return self._execute_app_control_subtask(target)
            elif action_type == "video_edit":
                return self._execute_video_edit_subtask(target)
            elif action_type == "browser_form":
                return self._execute_browser_form_subtask(target)
            elif action_type == "system_command":
                return self._execute_system_command(target)
            elif action_type == "message_draft":
                return self._execute_message_draft(target, context)
            else:
                return self._execute_llm_synthesis(target, context)

        # Wrap with self-healing
        success, output, notes = self.self_heal.execute_with_healing(
            action_fn=action_runner,
            task_id=task_id,
            step_name=name,
            model_name=model,
            skill_id=task.get("action_type"),
        )

        elapsed = round(time.time() - t0, 2)
        status = "completed" if success else "failed"

        self.emit("task_finished", {
            "task_id": task_id,
            "status": status,
            "duration_s": elapsed,
            "notes": notes,
            "output_preview": str(output)[:150],
        })

        return {
            "task_id": task_id,
            "name": name,
            "status": status,
            "duration_s": elapsed,
            "output": output,
            "notes": notes,
            "action_type": action_type,
        }

    def _execute_browser_research(self, query: str, user_prompt: str = "") -> str:
        """Executes resilient multi-provider web search & extracts live evidence."""
        # Check for explicit visible browser request
        use_visible = should_use_visible_browser(user_prompt)
        results = self.search_manager.search(query, max_results=3)
        if not results:
            # Fallback to isolated Playwright Chrome
            browser = self.get_browser_agent(headless=not use_visible)
            ddg_res = browser.search_duckduckgo(query, max_results=3)
            if ddg_res:
                bullets = "\n".join([f"- **{r.get('title')}**: {r.get('snippet')} ({r.get('url')})" for r in ddg_res])
                mode_str = "Visible Browser" if use_visible else "Headless Browser"
                return f"Web Search Findings for '{query}' (via {mode_str}):\n{bullets}"
            return f"No search results returned for query: '{query}'."

        formatted = "\n".join([f"- **{r.title}** ({r.source}): {r.snippet} (URL: {r.url})" for r in results])
        return f"Verified Online Search Findings for '{query}':\n{formatted}"

    def _execute_file_analysis(self, file_path: str) -> str:
        """Safely reads file within project sandbox and generates summary."""
        target_path = os.path.abspath(file_path if file_path else REFLECTIONS_PATH)
        # Sandbox boundary enforcement
        if not target_path.lower().startswith(ROOT_DIR.lower()):
            raise PermissionError(f"Access denied: path '{target_path}' is outside ISHTI sandbox.")

        if not os.path.exists(target_path):
            alt = os.path.join(ROOT_DIR, "orchestrator", "memory", os.path.basename(target_path))
            if os.path.exists(alt):
                target_path = alt
            else:
                return f"File does not exist: {target_path}"

        with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(2500)

        return f"File Content Analysis ({os.path.basename(target_path)}):\n{content}"

    def _execute_message_draft(self, instruction: str, context: Dict[str, Any]) -> str:
        ctx_summary = "\n".join([f"Step {k}: {v.get('output', '')[:300]}" for k, v in context.items()])
        draft = self.model_router.generate(
            f"Draft this message for the owner to review. Plain everyday wording - it is an "
            f"ordinary message, not a report or a briefing.\n\n"
            f"Instruction: {instruction}\n\nContext from prior steps:\n{ctx_summary}",
            depth="MODERATE", memory_query=instruction, temperature=0.3,
        )
        return draft or f"Draft message prepared based on context: {instruction}"

    def _execute_llm_synthesis(self, prompt: str, context: Dict[str, Any]) -> str:
        ctx_summary = "\n".join([f"Step {k}: {v.get('output', '')[:250]}" for k, v in context.items()])

        # Conversation history is replayed into the prompt, so it must be sanitised first:
        # an unguarded bad turn is how one hallucination became a persona (docs/AUDIT.md F5).
        conv_mgr = get_conversation_manager()
        history = clean_history(
            conv_mgr.get_history_text(conv_mgr.active_session_id, max_turns=4)
        ) if conv_mgr.active_session_id else ""

        depth = IntentDetector.classify(prompt).get("depth", "MODERATE")
        answer = self.model_router.generate(
            (f"Recent conversation:\n{history}\n\n" if history else "")
            + (f"Results from the steps just executed:\n{ctx_summary}\n\n" if ctx_summary else "")
            + f"Owner's request: {prompt}",
            depth=depth, memory_query=prompt,
        )
        if not answer:
            ErrorLogger.log_error(
                subsystem="LLM_SYNTHESIS", error=RuntimeError("empty model response"),
                user_prompt=prompt, context="_execute_llm_synthesis",
            )
            return "The local model returned nothing for this step."
        return answer

    def _request_user_confirmation(self, task: Dict[str, Any]) -> bool:
        if self.confirmation_callback:
            return self.confirmation_callback(task)

        print("\n" + "=" * 60)
        print("  [CONSEQUENTIAL ACTION CONFIRMATION GATE]")
        print(f"  Task: {task.get('name')}")
        print(f"  Details: {task.get('consequential_details')}")
        print("=" * 60)
        try:
            if not sys.stdin.isatty():
                print("  [Non-interactive: no one to confirm, denying]")
                return False
            ans = input("  Proceed with this consequential action? (y/N): ").strip().lower()
            return ans in ["y", "yes"]
        except Exception:
            return False

    def execute_plan(self, plan: TaskPlan) -> Dict[str, Any]:
        """Executes all DAG stages of the plan concurrently where independent."""
        # Initialize task state machine
        task_id = self.task_state_machine.start_task()
        self.task_state_machine.to_planning(f"Planning {len(plan.stages)} stages")
        self.task_state_machine.to_executing(f"Executing {len(plan.stages)} stages")

        self.emit("execution_started", {"request": plan.raw_request, "total_stages": len(plan.stages), "task_id": task_id})
        context: Dict[str, Any] = {}
        all_results = []

        total_tasks = sum(len(s) for s in plan.stages)
        completed_count = 0

        for stage_idx, stage in enumerate(plan.stages):
            stage_num = stage_idx + 1
            self.emit("stage_started", {"stage": stage_num, "task_count": len(stage)})
            self.task_state_machine.to_executing(f"Stage {stage_num}")

            if len(stage) == 1:
                task = stage[0]
                self.task_state_machine.set_subtask_state(task.get("id"), TaskState.EXECUTING)
                result = self.run_subtask(task, context)
                if result is None:
                    result = {"status": "failed", "error": "No result returned"}
                self.task_state_machine.set_subtask_state(task.get("id"), TaskState.COMPLETED if result.get("status") == "completed" else TaskState.FAILED)
                context[task.get("id")] = result
                all_results.append(result)
                completed_count += 1
            else:
                print(f"[ExecutionEngine] Executing Stage {stage_num} with {len(stage)} tasks in PARALLEL...")
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(stage), 4)) as executor:
                    future_to_task = {
                        executor.submit(self.run_subtask, task, context): task for task in stage
                    }
                    for future in concurrent.futures.as_completed(future_to_task):
                        task = future_to_task[future]
                        self.task_state_machine.set_subtask_state(task.get("id"), TaskState.EXECUTING)
                        try:
                            res = future.result()
                            if res is None:
                                res = {"status": "failed", "error": "No result returned"}
                            self.task_state_machine.set_subtask_state(task.get("id"), TaskState.COMPLETED if res.get("status") == "completed" else TaskState.FAILED)
                            context[task.get("id")] = res
                            all_results.append(res)
                        except Exception as exc:
                            print(f"[ExecutionEngine] Parallel task '{task.get('id')}' raised exception: {exc}")
                            self.task_state_machine.set_subtask_state(task.get("id"), TaskState.FAILED)
                            all_results.append({"task_id": task.get("id"), "status": "failed", "error": str(exc)})
                        completed_count += 1

            self.emit("progress_update", {
                "completed": completed_count,
                "total": total_tasks,
                "pct": int((completed_count / total_tasks) * 100),
            })
            self.emit("stage_finished", {"stage": stage_num})

        self.task_state_machine.to_verifying("Verifying results")
        # Synthesize final output
        synthesis = self._synthesize_final_output(plan.raw_request, context)
        self.task_state_machine.to_responding("Generating response")
        self.emit("execution_finished", {"final_output": synthesis})
        self.task_state_machine.to_completed("Task completed successfully")

        # Learn new skill if novel workflow
        self._learn_skill_if_new(plan, all_results)

        return {
            "request": plan.raw_request,
            "mode": "DAG_EXECUTION",
            "subtask_results": all_results,
            "final_answer": synthesis,
            "sources": [],
            "task_state": self.task_state_machine.get_summary(),
        }

    def _synthesize_final_output(self, request: str, context: Dict[str, Any]) -> str:
        ctx_bullets = "\n".join([
            f"- {v.get('name', k)}: {(v.get('output') or '')[:400]}"
            for k, v in context.items()
        ])
        depth = IntentDetector.classify(request).get("depth", "MODERATE")
        answer = self.model_router.generate(
            f"Owner's request: {request}\n\nWhat the steps produced:\n{ctx_bullets}\n\n"
            f"Answer the request using these results.",
            depth=depth, memory_query=request,
        )
        return answer or f"Completed steps:\n{ctx_bullets}"

    def _learn_skill_if_new(self, plan: TaskPlan, results: List[Dict[str, Any]]) -> None:
        if plan.matched_skill:
            skill_path = os.path.join(SKILLS_DIR, f"{plan.matched_skill}.json")
            if os.path.exists(skill_path):
                try:
                    with open(skill_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    data["success_count"] = data.get("success_count", 0) + 1
                    data["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    with open(skill_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                except Exception:
                    pass
            return

        # Only learn a genuinely multi-step workflow that fully succeeded. Learning every
        # one-liner is how the skill store filled with "learned_hey_siri_how_are_you".
        if len(plan.stages) < 2 or not results or any(r.get("status") != "completed" for r in results):
            return

        skill_id = "learned_" + re.sub(r"[^a-zA-Z0-9]+", "_", plan.raw_request[:30]).strip("_").lower()
        new_skill = {
            "skill_id": skill_id,
            "name": f"Workflow: {plan.raw_request[:40]}",
            "description": f"Automated workflow learned from user request: '{plan.raw_request}'",
            "triggers": [word for word in plan.raw_request.lower().split() if len(word) > 4][:5],
            "tools": list({r.get("action_type", "general") for stage in plan.stages for r in stage}),
            "steps": [r.get("name", "step") for stage in plan.stages for r in stage],
            "gotchas": [r.get("notes") for r in results if r.get("notes") and "Self-healed" in r.get("notes")],
            "success_count": 1,
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

        try:
            os.makedirs(SKILLS_DIR, exist_ok=True)
            with open(os.path.join(SKILLS_DIR, f"{skill_id}.json"), "w", encoding="utf-8") as f:
                json.dump(new_skill, f, indent=2)
            print(f"[ExecutionEngine] Learned new skill and persisted to: {skill_id}.json")
        except Exception as e:
            print(f"[ExecutionEngine] Could not persist new skill: {e}")

    def _handle_owner_command(self, user_prompt: str) -> Optional[Dict[str, Any]]:
        """Handle explicit owner commands for memory management."""
        prompt_lower = user_prompt.lower().strip()

        # Audio toggle
        if prompt_lower in ("mute", "stop reading", "stop talking", "be quiet", "audio off", "turn off audio"):
            self.user_memory.update_preferences(audio_output=False)
            return {"request": user_prompt, "mode": "OWNER_COMMAND",
                    "final_answer": "Audio off. I'll answer in text only.",
                    "sources": [], "subtask_results": []}

        if prompt_lower in ("unmute", "read that", "say it", "audio on", "turn on audio", "speak up", "read it aloud"):
            self.user_memory.update_preferences(audio_output=True)
            answer = "Audio on. I'll read my answers aloud."
            try:
                from voice.tts import speak
                speak(answer, wait=False)
            except Exception as exc:
                print(f"[ExecutionEngine] TTS unavailable: {exc}")
            return {"request": user_prompt, "mode": "OWNER_COMMAND",
                    "final_answer": answer, "sources": [], "subtask_results": []}

        # Remember command
        if prompt_lower.startswith("remember ") or prompt_lower.startswith("remember that "):
            content = user_prompt[len("remember "):].strip()
            if prompt_lower.startswith("remember that "):
                content = user_prompt[len("remember that "):].strip()
            self.user_memory.add_context(
                key=f"note_{int(time.time())}",
                value=content,
                category="owner_note"
            )
            return {
                "request": user_prompt,
                "mode": "OWNER_COMMAND",
                "final_answer": f"Remembered: {content}",
                "sources": [],
                "subtask_results": [],
            }

        # Forget command
        if prompt_lower.startswith("forget "):
            key = user_prompt[len("forget "):].strip()
            if self.user_memory.remove_context(key):
                return {
                    "request": user_prompt,
                    "mode": "OWNER_COMMAND",
                    "final_answer": f"Forgot: {key}",
                    "sources": [],
                    "subtask_results": [],
                }
            else:
                return {
                    "request": user_prompt,
                    "mode": "OWNER_COMMAND",
                    "final_answer": f"No memory found with key: {key}",
                    "sources": [],
                    "subtask_results": [],
                }

        # "Who am I?" is a question, not a request for a data dump. Answer it in a sentence.
        if prompt_lower.strip(" ?.") in ("who am i", "who is my owner", "do you know who i am"):
            p = self.user_memory.get_profile()
            loc = p.location or {}
            where = ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
            return {
                "request": user_prompt, "mode": "OWNER_COMMAND",
                "final_answer": f"You are {p.name}, my {p.relationship} - {'an ' if p.role[:1].lower() in 'aeiou' else 'a '}{p.role} in {where}.",
                "sources": [], "subtask_results": [],
            }

        # Show memory/profile commands (a deliberate dump of everything stored)
        if any(phrase in prompt_lower for phrase in [
            "what do you remember about me",
            "what do you know about me",
            "show my profile",
            "show my memory",
            "who is my owner"
        ]):
            profile = self.user_memory.get_profile()
            prefs = self.user_memory.get_preferences()
            projects = self.user_memory.get_projects("active")
            context = self.user_memory.get_context()

            response_parts = []
            if profile.name:
                response_parts.append(f"**Owner Profile:**\n- Name: {profile.name}\n- Relationship: {profile.relationship}\n- Role: {profile.role}\n- Location: {profile.location}")

            if prefs:
                response_parts.append(f"\n**Preferences:**\n- Language: {prefs.language}\n- Timezone: {prefs.timezone}\n- Response Length: {prefs.response_length}\n- Voice Enabled: {prefs.voice_enabled}\n- Headless Browser Default: {prefs.headless_browser_default}")

            if projects:
                proj_lines = [f"- {p.name}: {p.description}" for p in projects]
                response_parts.append(f"\n**Active Projects:**\n" + "\n".join(proj_lines))

            if context:
                ctx_lines = [f"- {c.key}: {c.value} ({c.category})" for c in context]
                response_parts.append(f"\n**Important Context:**\n" + "\n".join(ctx_lines))

            if not response_parts:
                response_parts.append("No persistent memory stored yet.")

            return {
                "request": user_prompt,
                "mode": "OWNER_COMMAND",
                "final_answer": "\n".join(response_parts),
                "sources": [],
                "subtask_results": [],
            }

        # Update profile command
        if prompt_lower.startswith("update my profile ") or prompt_lower.startswith("set my "):
            # Parse simple updates: "set my name to X", "update my role to X"
            if "name" in prompt_lower and ("to" in prompt_lower or "is" in prompt_lower):
                parts = user_prompt.split("to")[-1].split("is")[-1].strip()
                self.user_memory.update_profile(name=parts)
                return {
                    "request": user_prompt,
                    "mode": "OWNER_COMMAND",
                    "final_answer": f"Updated profile name to: {parts}",
                    "sources": [],
                    "subtask_results": [],
                }
            if "role" in prompt_lower and ("to" in prompt_lower or "is" in prompt_lower):
                parts = user_prompt.split("to")[-1].split("is")[-1].strip()
                self.user_memory.update_profile(role=parts)
                return {
                    "request": user_prompt,
                    "mode": "OWNER_COMMAND",
                    "final_answer": f"Updated profile role to: {parts}",
                    "sources": [],
                    "subtask_results": [],
                }

        # Capability status command
        if any(phrase in prompt_lower for phrase in [
            "what can you do",
            "show capabilities",
            "capability status",
            "what are your capabilities"
        ]):
            summary = self.capability_registry.get_status_summary()
            lines = [f"Total Capabilities: {summary['total']}"]
            lines.append(f"Healthy: {summary['healthy']}, Degraded: {summary['degraded']}, Unavailable: {summary['unavailable']}")
            lines.append("\n**Capabilities by Category:**")
            for cat in set(c.category for c in self.capability_registry.get_all_capabilities()):
                caps = self.capability_registry.get_capabilities_by_category(cat)
                for cap in caps:
                    lines.append(f"  - {cap.name}: {cap.status} - {cap.description[:60]}...")

            return {
                "request": user_prompt,
                "mode": "OWNER_COMMAND",
                "final_answer": "\n".join(lines),
                "sources": [],
                "subtask_results": [],
            }

        # Doctor command
        if prompt_lower in ["run diagnostics", "ishti doctor", "doctor", "system check"]:
            from orchestrator.doctor import run_doctor
            report = run_doctor()
            status = "HEALTHY" if not report["errors"] else f"DEGRADED ({len(report['errors'])} errors, {len(report['warnings'])} warnings)"
            return {
                "request": user_prompt,
                "mode": "OWNER_COMMAND",
                "final_answer": f"System Diagnostics Complete: {status}\n\nRun the DOCTOR view in the Web UI for detailed results.",
                "sources": [],
                "subtask_results": [],
            }

        return None


if __name__ == "__main__":
    engine = ExecutionEngine()
    test_req = "What is the latest version of Python?"
    print("Testing process_request with quick search query:", test_req)
    res = engine.process_request(test_req)
    print("\nAnswer:\n", res.get("final_answer"))
    print("\nSources:\n", res.get("sources"))
