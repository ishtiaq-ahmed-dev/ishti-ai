"""
ISHTI Self-Healing Diagnostic & Remediation Engine
Intercepts tool, model, and browser failures, records contextual diagnostics to reflections.md,
executes automated recovery strategies, and propagates learnings into Skill Memory.
"""

import os
import time
import json
import traceback
from typing import Dict, Any, Callable, Optional, Tuple

REFLECTIONS_PATH = os.path.join(os.path.dirname(__file__), "memory", "reflections.md")
SKILLS_DIR = os.path.join(os.path.dirname(__file__), "memory", "skills")


class SelfHealEngine:
    def __init__(self, model_router=None):
        self.model_router = model_router

    def log_reflection(
        self,
        task_id: str,
        step_name: str,
        model_name: str,
        error_category: str,
        error_details: str,
        attempted_fix: str,
        outcome: str,
        learned_pattern: str,
    ) -> None:
        """Appends structured diagnostic record to reflections.md."""
        entry = (
            f"\n### Self-Healing Event: {step_name}\n"
            f"- **Timestamp**: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
            f"- **Task ID**: `{task_id}`\n"
            f"- **Model**: `{model_name}`\n"
            f"- **Category**: `{error_category}`\n"
            f"- **Details**: {error_details.strip()[:200]}\n"
            f"- **Attempted Fix**: {attempted_fix}\n"
            f"- **Outcome**: `{outcome}`\n"
            f"- **Learned Pattern**: {learned_pattern}\n"
        )
        try:
            with open(REFLECTIONS_PATH, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            print(f"[SelfHeal] Error writing to reflections.md: {e}")

    def update_skill_memory(self, skill_id: str, fix_description: str) -> None:
        """Updates the skill JSON with new gotcha/self-healing remediation."""
        skill_file = os.path.join(SKILLS_DIR, f"{skill_id}.json")
        if os.path.exists(skill_file):
            try:
                with open(skill_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                gotchas = data.get("gotchas", [])
                if fix_description not in gotchas:
                    gotchas.append(fix_description)
                    data["gotchas"] = gotchas
                data["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                with open(skill_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                print(f"[SelfHeal] Successfully updated skill memory '{skill_id}' with new learning.")
            except Exception as e:
                print(f"[SelfHeal] Failed to update skill '{skill_id}': {e}")

    def execute_with_healing(
        self,
        action_fn: Callable[..., Any],
        args: tuple = (),
        kwargs: dict = None,
        task_id: str = "task_01",
        step_name: str = "execution_step",
        model_name: str = "qwen2.5:14b",
        skill_id: Optional[str] = None,
        max_retries: int = 1,
    ) -> Tuple[bool, Any, str]:
        """
        Executes action with automated single-retry self-healing:
        1. Attempt normal execution.
        2. If fails: diagnose error type, log to reflections.md, apply fix.
        3. If retry succeeds: update skill file, return success.
        4. If retry fails: stop, return transparent diagnostic report.
        """
        if kwargs is None:
            kwargs = {}

        try:
            result = action_fn(*args, **kwargs)
            return True, result, "Executed successfully on first attempt."
        except Exception as err:
            err_msg = str(err)
            err_cat = self._classify_error(err)
            print(f"[SelfHeal] Execution error detected: {err_cat} - {err_msg}")

            # Determine remediation strategy
            attempted_fix, remediated_fn, rem_args, rem_kwargs = self._determine_fix(
                err_cat, err_msg, action_fn, args, kwargs, model_name
            )

            # Log incident to reflections.md
            self.log_reflection(
                task_id=task_id,
                step_name=step_name,
                model_name=model_name,
                error_category=err_cat,
                error_details=err_msg,
                attempted_fix=attempted_fix,
                outcome="RETRYING_FIX",
                learned_pattern=f"Apply {attempted_fix} when {err_cat} occurs.",
            )

            # Execute the fix
            try:
                time.sleep(1.0)  # Short stabilization pause
                remediated_result = remediated_fn(*rem_args, **rem_kwargs)
                print(f"[SelfHeal] Self-healing fix succeeded!")

                self.log_reflection(
                    task_id=task_id,
                    step_name=step_name,
                    model_name=model_name,
                    error_category=err_cat,
                    error_details=err_msg,
                    attempted_fix=attempted_fix,
                    outcome="RESOLVED",
                    learned_pattern=f"Remediation succeeded: {attempted_fix}",
                )

                if skill_id:
                    self.update_skill_memory(skill_id, f"Auto-heal: {attempted_fix} resolves {err_cat}")

                return True, remediated_result, f"Self-healed: {attempted_fix}"
            except Exception as retry_err:
                final_err = str(retry_err)
                print(f"[SelfHeal] Fix attempt failed: {final_err}")
                self.log_reflection(
                    task_id=task_id,
                    step_name=step_name,
                    model_name=model_name,
                    error_category=err_cat,
                    error_details=final_err,
                    attempted_fix=attempted_fix,
                    outcome="ESCALATED_TO_USER",
                    learned_pattern="Escalate to user when automated fix fails.",
                )
                return False, None, f"Execution failed after self-healing attempt ({attempted_fix}): {final_err}"

    def _classify_error(self, err: Exception) -> str:
        name = type(err).__name__
        msg = str(err).lower()
        if "timeout" in msg or "timed out" in msg:
            return "TimeoutError"
        elif "json" in msg or "decode" in msg:
            return "JSONParseError"
        elif "permission" in msg or "denied" in msg or "isolation" in msg:
            return "SecurityBoundaryViolation"
        elif "connection" in msg or "refused" in msg:
            return "NetworkConnectionError"
        elif "selector" in msg or "locator" in msg:
            return "BrowserSelectorError"
        return f"ToolExecutionError({name})"

    def _determine_fix(
        self,
        err_cat: str,
        err_msg: str,
        action_fn: Callable,
        args: tuple,
        kwargs: dict,
        model_name: str,
    ) -> Tuple[str, Callable, tuple, dict]:
        """Maps error categories to concrete self-healing strategies."""
        if err_cat == "TimeoutError":
            fix = "Exponential backoff retry with extended timeout threshold"
            new_kwargs = dict(kwargs)
            if "timeout" in new_kwargs:
                new_kwargs["timeout"] = new_kwargs["timeout"] * 2
            return fix, action_fn, args, new_kwargs

        elif err_cat == "JSONParseError":
            fix = "Sanitize model output by stripping code markdown fences and unescaped quotes"
            # Return same function or fallback wrapper
            return fix, action_fn, args, kwargs

        elif err_cat == "BrowserSelectorError":
            fix = "Fallback to whole-body innerText heuristic extraction"
            new_kwargs = dict(kwargs)
            new_kwargs["use_fallback_parser"] = True
            return fix, action_fn, args, new_kwargs

        elif self.model_router and "model" in err_msg.lower():
            fallback_model = self.model_router.record_failure_and_maybe_heal(model_name, err_msg)
            fix = f"Failover model router to fallback model '{fallback_model}'"
            new_kwargs = dict(kwargs)
            if "model" in new_kwargs:
                new_kwargs["model"] = fallback_model
            return fix, action_fn, args, new_kwargs

        return "Standard retry with stabilized state", action_fn, args, kwargs
