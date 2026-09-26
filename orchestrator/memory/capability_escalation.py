"""
ISHTI Capability Escalation Engine
When ISHTI doesn't know how to accomplish a task, it attempts to discover how:
1. LLM reasoning about required capabilities
2. Existing skill lookup
3. Documentation/web research for methods
4. Package/tool discovery
5. Browser investigation
6. Implementation and verification
"""

import os
import sys
import json
import time
import re
import requests
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict

ROOT_DIR = r"D:\ISHTI"
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from orchestrator.model_router import ModelRouter
from orchestrator.memory.capability_registry import get_capability_registry, Capability
from tools.web.search import get_search_manager
from tools.web.fetch import WebFetcher


@dataclass
class EscalationPlan:
    task_description: str
    required_capability: str
    capability_description: str
    research_queries: List[str]
    proposed_approach: str
    tools_needed: List[str]
    estimated_complexity: str  # low, medium, high
    can_automate: bool


class CapabilityEscalationEngine:
    def __init__(self):
        self.model_router = ModelRouter()
        self.capability_registry = get_capability_registry()
        self.search_manager = get_search_manager()
        self.fetcher = WebFetcher(timeout=10)
        self._escalation_log = []

    def escalate(self, user_prompt: str, missing_capability: str = None) -> EscalationPlan:
        """
        Analyze a task that ISHTI cannot currently perform and create an escalation plan.
        """
        # Determine what capability is missing
        if missing_capability is None:
            missing_capability = self._identify_missing_capability(user_prompt)

        # Check if we have relevant skills
        skill_info = self._check_skills(user_prompt)

        # Generate escalation plan using LLM reasoning
        plan = self._generate_escalation_plan(user_prompt, missing_capability, skill_info)

        # Log the escalation
        self._log_escalation(user_prompt, missing_capability, plan)

        return plan

    def _identify_missing_capability(self, user_prompt: str) -> str:
        """Identify what capability is needed for this task."""
        prompt_lower = user_prompt.lower()
        
        capability_keywords = {
            "web_search": ["search", "look up", "find", "google", "web", "internet", "online", "latest", "current"],
            "web_fetch": ["open", "fetch", "read", "visit", "browse", "url", "link"],
            "browser_automation": ["click", "fill", "form", "login", "submit", "automate browser"],
            "file_operations": ["file", "read file", "write file", "create file", "folder", "directory"],
            "code_execution": ["run code", "execute", "python", "script", "calculate", "compute"],
            "api_integration": ["api", "rest", "graphql", "webhook", "endpoint"],
            "database": ["database", "sql", "query", "table", "records"],
            "email_messaging": ["email", "send message", "notify", "mail"],
            "scheduling": ["schedule", "calendar", "remind", "appointment"],
            "system_control": ["system", "process", "service", "command", "shell"],
            "image_processing": ["image", "picture", "photo", "screenshot", "ocr"],
            "audio_processing": ["audio", "sound", "voice", "transcribe", "speech"],
            "video_processing": ["video", "record", "stream", "youtube"],
            "ml_training": ["train", "model", "machine learning", "fine-tune"],
            "data_analysis": ["analyze", "statistics", "chart", "graph", "visualize", "pandas"],
        }

        for cap, keywords in capability_keywords.items():
            if any(kw in prompt_lower for kw in keywords):
                return cap

        return "unknown"

    def _check_skills(self, user_prompt: str) -> List[Dict]:
        """Check if any existing skills match the task."""
        from orchestrator.planner import SKILLS_DIR
        skills = []
        if os.path.exists(SKILLS_DIR):
            for fname in os.listdir(SKILLS_DIR):
                if fname.endswith(".json"):
                    try:
                        with open(os.path.join(SKILLS_DIR, fname), "r", encoding="utf-8") as f:
                            skill = json.load(f)
                        triggers = skill.get("triggers", [])
                        if any(t.lower() in user_prompt.lower() for t in triggers):
                            skills.append(skill)
                    except Exception:
                        continue
        return skills

    def _generate_escalation_plan(self, user_prompt: str, missing_capability: str, skills: List[Dict]) -> EscalationPlan:
        """Use LLM to generate a detailed escalation plan."""
        host = self.model_router.host
        model = self.model_router.get_model("primary")

        skills_summary = ""
        if skills:
            skills_summary = "\nExisting relevant skills:\n" + "\n".join([
                f"- {s.get('name')}: {s.get('description')}" for s in skills
            ])

        available_caps = self.capability_registry.get_available_capabilities()
        caps_summary = "\nAvailable capabilities:\n" + "\n".join([
            f"- {c.name}: {c.description}" for c in available_caps
        ])

        prompt = f"""
You are ISHTI's Capability Escalation Engine. A user requested: "{user_prompt}"

Missing capability identified: {missing_capability}
{skills_summary}
{caps_summary}

Generate a JSON escalation plan with these fields:
- required_capability: string (the capability name)
- capability_description: string (what this capability would do)
- research_queries: array of 3-5 search queries to find implementation methods
- proposed_approach: string (step-by-step approach to implement)
- tools_needed: array of tool names (Python packages, APIs, etc.)
- estimated_complexity: "low" | "medium" | "high"
- can_automate: boolean (whether ISHTI can implement this autonomously)

Return ONLY valid JSON. No markdown.
"""

        try:
            res = requests.post(
                f"{host}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}},
                timeout=30,
            )
            if res.status_code == 200:
                raw = res.json().get("response", "").strip()
                # Clean code fences
                clean = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
                clean = re.sub(r"^```\s*", "", clean, flags=re.MULTILINE).strip()
                m = re.search(r"\{.*\}", clean, re.DOTALL)
                if m:
                    data = json.loads(m.group(0))
                    req_cap = data.pop("required_capability", None) or missing_capability
                    task_desc = data.pop("task_description", None) or user_prompt
                    valid_fields = {f.name for f in EscalationPlan.__dataclass_fields__.values()}
                    filtered_data = {k: v for k, v in data.items() if k in valid_fields}
                    return EscalationPlan(
                        task_description=task_desc,
                        required_capability=req_cap,
                        capability_description=filtered_data.get("capability_description", f"Capability to handle: {req_cap}"),
                        research_queries=filtered_data.get("research_queries", [f"how to {req_cap} python"]),
                        proposed_approach=filtered_data.get("proposed_approach", "Research and implement required functionality."),
                        tools_needed=filtered_data.get("tools_needed", []),
                        estimated_complexity=filtered_data.get("estimated_complexity", "medium"),
                        can_automate=filtered_data.get("can_automate", False),
                    )
        except Exception as e:
            print(f"[CapabilityEscalation] LLM generation failed: {e}")

        # Fallback plan
        return EscalationPlan(
            task_description=user_prompt,
            required_capability=missing_capability,
            capability_description=f"Capability to handle: {missing_capability}",
            research_queries=[f"how to {missing_capability} python", f"{missing_capability} library python"],
            proposed_approach="Research implementation methods, identify required packages, create prototype, test and integrate.",
            tools_needed=["requests", "beautifulsoup4", "selenium", "playwright"],
            estimated_complexity="medium",
            can_automate=False,
        )

    def _log_escalation(self, user_prompt: str, missing_capability: str, plan: EscalationPlan):
        """Log escalation for future reference."""
        from orchestrator.memory.error_logger import ErrorLogger
        ErrorLogger.log_error(
            subsystem="CAPABILITY_ESCALATION",
            error=f"Missing capability: {missing_capability}",
            user_prompt=user_prompt,
            context=f"Generated escalation plan: {plan.proposed_approach[:200]}",
            severity="INFO",
            suggested_fix=f"Implement {missing_capability} capability. Research queries: {plan.research_queries}",
        )

    def attempt_auto_implementation(self, plan: EscalationPlan) -> Tuple[bool, str]:
        """
        Attempt to automatically implement a missing capability.
        Returns (success, message).
        """
        if not plan.can_automate or plan.estimated_complexity == "high":
            return False, "Capability requires manual implementation or is too complex"

        # For now, return a structured response that the capability needs to be built
        # In a full implementation, this would:
        # 1. Research the queries
        # 2. Find existing libraries
        # 3. Generate wrapper code
        # 4. Test and integrate

        # Research phase
        print(f"[CapabilityEscalation] Researching: {plan.required_capability}")
        for query in plan.research_queries:
            results = self.search_manager.search(query, max_results=3)
            for r in results:
                print(f"  Found: {r.title} - {r.url}")

        return False, f"Auto-implementation not yet available. Research completed for {plan.required_capability}. Manual implementation required."


_capability_escalation_instance = None


def get_capability_escalation() -> CapabilityEscalationEngine:
    global _capability_escalation_instance
    if _capability_escalation_instance is None:
        _capability_escalation_instance = CapabilityEscalationEngine()
    return _capability_escalation_instance


if __name__ == "__main__":
    import requests
    engine = get_capability_escalation()
    test_prompt = "Send an email to my team with the weekly report"
    plan = engine.escalate(test_prompt)
    print(f"Escalation Plan for: {test_prompt}")
    print(f"Required: {plan.required_capability}")
    print(f"Description: {plan.capability_description}")
    print(f"Queries: {plan.research_queries}")
    print(f"Approach: {plan.proposed_approach}")
    print(f"Complexity: {plan.estimated_complexity}")
    print(f"Can Automate: {plan.can_automate}")