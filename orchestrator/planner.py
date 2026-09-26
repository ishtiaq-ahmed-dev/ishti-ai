"""
ISHTI Task Planner & Dependency Analyzer
Breaks user requests into discrete DAG execution graphs (independent parallel steps vs sequential dependent steps),
checks Skill Memory first, and flags consequential actions for Human-in-the-Loop gating.
"""

import json
import os
import re
from typing import Dict, Any, List, Optional
import requests

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
SKILLS_DIR = os.path.join(os.path.dirname(__file__), "memory", "skills")


class TaskPlan:
    def __init__(self, raw_request: str):
        self.raw_request = raw_request
        self.matched_skill: Optional[str] = None
        self.stages: List[List[Dict[str, Any]]] = []  # Grouped by parallelizable stages
        self.has_consequential: bool = False
        self.consequential_tasks: List[Dict[str, Any]] = []


class Planner:
    def __init__(self, config_path: str = CONFIG_PATH):
        self.config_path = config_path
        self._load_config()

    def _load_config(self) -> None:
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.config = json.load(f)
        else:
            self.config = {"ollama": {"host": "http://127.0.0.1:11434", "primary_model": "qwen2.5:14b", "fast_model": "llama3.2:3b"}}

    def find_matching_skill(self, request: str) -> Optional[Dict[str, Any]]:
        """Checks skills memory before invoking LLM planning."""
        req_lower = request.lower()
        if not os.path.exists(SKILLS_DIR):
            return None

        for fname in os.listdir(SKILLS_DIR):
            if fname.endswith(".json"):
                fpath = os.path.join(SKILLS_DIR, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        skill = json.load(f)
                    triggers = skill.get("triggers", [])
                    if any(t.lower() in req_lower for t in triggers):
                        print(f"[Planner] Found matching skill in memory: '{skill.get('name')}'")
                        return skill
                except Exception:
                    continue
        return None

    def create_plan(self, user_prompt: str, history_context: str = "") -> TaskPlan:
        """
        Decomposes user prompt into parallel and sequential subtasks.
        Optionally receives conversation history for multi-turn context.
        """
        plan = TaskPlan(user_prompt)
        matched_skill = self.find_matching_skill(user_prompt)
        if matched_skill:
            plan.matched_skill = matched_skill.get("skill_id")

        # Check for multi-intent prompts (e.g. "look up weather and summarize file")
        # Query local Ollama model to generate structured task graph
        ollama_host = self.config.get("ollama", {}).get("host", "http://127.0.0.1:11434")
        # Use primary or fast model
        model = self.config.get("ollama", {}).get("primary_model", "qwen2.5:14b")

        system_prompt = (
            "You are ISHTI's Planner. Decompose this user request into discrete execution subtasks.\n"
            "Identify which subtasks are independent (can run concurrently in parallel) and which depend on prior steps.\n"
            "Flag 'is_consequential': true if the step sends a message, publishes content, or spends money.\n"
            "Return strictly a JSON array of task objects with keys:\n"
            "- id: (string, e.g. 'step_1')\n"
            "- name: (string, brief label)\n"
            "- action_type: (one of: 'browser_research', 'file_analysis', 'system_command', 'pc_search', 'app_control', 'video_edit', 'browser_form', 'message_draft', 'synthesis')\n"
            "- query_or_target: (string query or file path)\n"
            "- is_consequential: (boolean)\n"
            "- consequential_details: (string draft or warning if consequential, else '')\n"
            "- depends_on: (array of step ids, or [] if independent)\n"
            "Do NOT include markdown backticks or commentary."
        )

        tasks = []
        try:
            res = requests.post(
                f"{ollama_host}/api/generate",
                json={
                    "model": model,
                    "prompt": f"System: {system_prompt}\n{history_context}User Request: {user_prompt}\nOutput JSON:",
                    "stream": False,
                    "options": {"temperature": 0.1},
                },
                timeout=30,
            )
            if res.status_code == 200:
                raw = res.json().get("response", "").strip()
                clean = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
                clean = re.sub(r"^```\s*", "", clean, flags=re.MULTILINE).strip()
                # Find JSON array bracket
                m = re.search(r"\[.*\]", clean, re.DOTALL)
                if m:
                    tasks = json.loads(m.group(0))
        except Exception as e:
            print(f"[Planner] LLM decomposition failed: {e}. Using rule-based fallback decomposition.")

        # Fallback heuristic decomposition if model failed or returned empty
        if not tasks:
            tasks = self._rule_based_decomposition(user_prompt)

        # Organize tasks into stages (topological sort by dependencies)
        plan.stages = self._build_execution_stages(tasks)

        # Scan for consequential actions
        for task in tasks:
            if task.get("is_consequential"):
                plan.has_consequential = True
                plan.consequential_tasks.append(task)

        return plan

    def _rule_based_decomposition(self, prompt: str) -> List[Dict[str, Any]]:
        """Heuristic fallback for decomposing composite requests."""
        p_lower = prompt.lower()
        tasks = []

        # Check for composite "and" requests (e.g., "look up weather and summarize file")
        has_weather = "weather" in p_lower or "search" in p_lower or "look up" in p_lower
        has_file = "file" in p_lower or "summarize" in p_lower or "read" in p_lower
        has_message = "send" in p_lower or "message" in p_lower or "email" in p_lower or "post" in p_lower

        step_idx = 1
        indep_steps = []

        if has_weather:
            step_id = f"step_{step_idx}"
            tasks.append({
                "id": step_id,
                "name": "Web Research Query",
                "action_type": "browser_research",
                "query_or_target": prompt,
                "is_consequential": False,
                "consequential_details": "",
                "depends_on": [],
            })
            indep_steps.append(step_id)
            step_idx += 1

        if has_file:
            step_id = f"step_{step_idx}"
            tasks.append({
                "id": step_id,
                "name": "Sandbox File Analysis",
                "action_type": "file_analysis",
                "query_or_target": "d:\\ISHTI\\orchestrator\\memory\\reflections.md",
                "is_consequential": False,
                "consequential_details": "",
                "depends_on": [],
            })
            indep_steps.append(step_id)
            step_idx += 1

        if has_message:
            step_id = f"step_{step_idx}"
            tasks.append({
                "id": step_id,
                "name": "Draft & Dispatch Real-World Message",
                "action_type": "message_draft",
                "query_or_target": prompt,
                "is_consequential": True,
                "consequential_details": f"Draft message based on prompt: '{prompt}'. Requires user approval before dispatch.",
                "depends_on": indep_steps,
            })
            step_idx += 1

        if not tasks:
            # Default single-step general query
            tasks.append({
                "id": "step_1",
                "name": "Execute Request",
                "action_type": "synthesis",
                "query_or_target": prompt,
                "is_consequential": False,
                "consequential_details": "",
                "depends_on": [],
            })

        return tasks

    def _build_execution_stages(self, tasks: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Groups tasks into stages where all tasks in a stage run in parallel."""
        completed_ids = set()
        remaining = list(tasks)
        stages = []

        while remaining:
            current_stage = []
            next_remaining = []

            for task in remaining:
                deps = set(task.get("depends_on", []))
                # If dependencies are satisfied by previous stages
                if deps.issubset(completed_ids):
                    current_stage.append(task)
                else:
                    next_remaining.append(task)

            if not current_stage:
                # Cycle or unresolved dependency: force run remaining sequentially
                current_stage.append(next_remaining.pop(0))

            for task in current_stage:
                completed_ids.add(task.get("id"))

            stages.append(current_stage)
            remaining = next_remaining

        return stages


if __name__ == "__main__":
    planner = Planner()
    test_req = "Look up today's weather in Tokyo and also summarize reflections.md, then draft an email report."
    print("Testing Planner on prompt:", test_req)
    plan = planner.create_plan(test_req)
    print("\nPlanned Stages:")
    for i, stage in enumerate(plan.stages):
        print(f"Stage {i+1} (Parallel batch count: {len(stage)}):")
        for t in stage:
            print(f"  - [{t['id']}] {t['name']} ({t['action_type']}) | Consequential: {t['is_consequential']} | Deps: {t['depends_on']}")
