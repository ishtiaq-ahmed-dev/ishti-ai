"""Independent read-only plan steps run at the same time; anything that writes or uses an earlier result stays in order."""
import threading
import time

from ishti import workflows


class _Skill:
    def __init__(self, tier):
        self.manifest = {"tier": tier}


def _run(log, delay=0.3):
    def run_tool(skills, tool, text, session):
        log.append(("start", tool, threading.current_thread().name))
        time.sleep(delay)
        return {"reply": f"{tool}:{text}", "verified": True}
    return run_tool


def test_three_lookups_take_one_lookups_time_and_keep_their_order():
    skills = {"weather": _Skill("T0"), "web_search": _Skill("T0")}
    plan = workflows.new({}, [{"tool": "weather", "input": "Lahore"}, {"tool": "weather", "input": "Karachi"},
                              {"tool": "web_search", "input": "PSL score"}])
    log, t0 = [], time.monotonic()
    out = workflows.execute({}, plan, _run(log), skills)
    assert time.monotonic() - t0 < 0.75                                  # sequential would be ~0.9 s
    assert [r["reply"] for r in plan["results"]] == ["weather:Lahore", "weather:Karachi", "web_search:PSL score"]
    assert plan["state"] == "completed" and "weather:Lahore" in str(out)


def test_writes_and_dependent_steps_stay_sequential():
    skills = {"weather": _Skill("T0"), "notes": _Skill("T1")}
    plan = workflows.new({}, [{"tool": "notes", "input": "note: a"}, {"tool": "weather", "input": "x"},
                              {"tool": "weather", "input": "{{step:2}}"}])
    assert workflows._parallel_batch(plan, skills) == []                 # a T1 step first: nothing runs early
    plan["next"] = 1
    assert workflows._parallel_batch(plan, skills) == [1]                # the next one depends on step 2's result


def test_a_failed_parallel_step_does_not_sink_the_others():
    skills = {"weather": _Skill("T0"), "web_search": _Skill("T0")}
    plan = workflows.new({}, [{"tool": "weather", "input": "Lahore"}, {"tool": "web_search", "input": "x"}])

    def run_tool(skills_, tool, text, session):
        if tool == "web_search":
            raise RuntimeError("offline")
        return {"reply": "sunny", "verified": True}
    workflows.execute({}, plan, run_tool, skills)
    assert plan["results"][0]["reply"] == "sunny" and plan["results"][1]["status"] == "failed"
    assert plan["state"] == "completed" and plan["failed"] == [1]
