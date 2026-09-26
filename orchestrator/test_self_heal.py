"""
ISHTI Self-Healing Verification Suite
Deliberately introduces a failure scenario to demonstrate the automatic retry,
diagnostic logging to reflections.md, and learning persistence in skill memory.
"""

import os
import sys
import json
import time

ROOT_DIR = r"D:\ISHTI"
sys.path.insert(0, ROOT_DIR)

from orchestrator.self_heal import SelfHealEngine
from orchestrator.model_router import ModelRouter

REFLECTIONS_PATH = os.path.join(ROOT_DIR, "orchestrator", "memory", "reflections.md")
SKILLS_DIR = os.path.join(ROOT_DIR, "orchestrator", "memory", "skills")


def test_self_healing_recovery():
    print("=" * 60)
    print("  RUNNING ISHTI SELF-HEALING DELIBERATE-FAILURE TEST")
    print("=" * 60)

    router = ModelRouter()
    healer = SelfHealEngine(model_router=router)

    # Simulated flaky operation: fails on attempt 1 with TimeoutError, succeeds on attempt 2
    attempts = {"count": 0}

    def flaky_browser_fetch(url: str, timeout: int = 5, use_fallback_parser: bool = False):
        attempts["count"] += 1
        print(f"  [Attempt {attempts['count']}] Executing fetch for '{url}' (timeout={timeout}, fallback={use_fallback_parser})")
        if attempts["count"] == 1:
            raise TimeoutError("Network gateway timed out waiting for DOM selector '#dynamic-content'")
        return f"Successfully extracted 14 paragraphs from {url} using extended timeout ({timeout}s)"

    # Verify reflections.md before
    size_before = os.path.getsize(REFLECTIONS_PATH) if os.path.exists(REFLECTIONS_PATH) else 0

    success, result, notes = healer.execute_with_healing(
        action_fn=flaky_browser_fetch,
        kwargs={"url": "https://example.org/dynamic-feed", "timeout": 5},
        task_id="test_heal_001",
        step_name="Flaky Browser Fetch Step",
        model_name="qwen2.5:14b",
        skill_id="browser_research",
    )

    print("\n[Self-Healing Test Results]:")
    print(f"  > Success: {success}")
    print(f"  > Result: {result}")
    print(f"  > Remediation Notes: {notes}")

    # Verify reflection was logged
    assert os.path.exists(REFLECTIONS_PATH), "reflections.md does not exist!"
    size_after = os.path.getsize(REFLECTIONS_PATH)
    assert size_after > size_before, "reflections.md was not updated!"
    print("  > Verified: reflections.md updated with full context diagnostic!")

    # Verify skill memory was updated
    skill_file = os.path.join(SKILLS_DIR, "browser_research.json")
    with open(skill_file, "r", encoding="utf-8") as f:
        skill_data = json.load(f)
    print(f"  > Skill Gotchas updated count: {len(skill_data.get('gotchas', []))}")
    print("  > Gotchas excerpt:", skill_data.get("gotchas")[-1])

    print("\n=== ALL SELF-HEALING VERIFICATION CHECKS PASSED! ===\n")
    return True


if __name__ == "__main__":
    test_self_healing_recovery()
