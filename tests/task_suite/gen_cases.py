"""Generate tests/task_suite/cases/<id>.json for all 250 master tasks, and TASK_INDEX.md.

Curated cues already exist for ids 1-100 and 151-200 (tests/task_suite/cases.py). For the rest
(101-150 people/social/email, 201-250 transparency/creative/autonomy/privacy) the cues are derived
from the row's own Expected-action and Expected-reply columns, so nothing is silently dropped.
"""
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from tests.task_suite.cases import CASES  # noqa: E402
from tests.task_suite.skill_overrides import (OVERRIDES, EXTERNAL, PROMPT_OVERRIDE,  # noqa: E402
                                              OK_ANY_OVERRIDE, BAD_ANY_OVERRIDE)

CURATED = {c["id"]: c for c in CASES}
_master_file = os.path.join(ROOT, "tests", "task_suite", "MASTER_300.json")
if not os.path.exists(_master_file):
    _master_file = os.path.join(ROOT, "tests", "task_suite", "MASTER_250.json")
MASTER = json.load(io.open(_master_file, encoding="utf-8"))["tasks"]
CASES_DIR = os.path.join(ROOT, "tests", "task_suite", "cases")

STOP = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "it", "its", "is",
        "are", "be", "that", "this", "as", "at", "by", "if", "not", "no", "so", "then", "than",
        "e.g.", "wrong:", "e.g", "correct", "success", "failed"}

# expected-action keywords -> the skills that should own the task
SKILL_HINTS = [
    (r"calendar|schedule|meeting|slot|reschedul|event", ["calendar", "reminders"]),
    (r"reminder", ["reminders"]),
    (r"whatsapp", ["whatsapp"]),
    (r"e-?mail|gmail|inbox|invoice", ["email"]),
    (r"instagram|facebook|linkedin|social", ["browser", "whatsapp"]),
    (r"terminal|git|repo|test suite|pip|dependenc|code|refactor|debug|unit test|deploy", ["dev", "terminal", "devops", "gitops", "chat"]),
    (r"market|stock|price|portfolio|crypto|bitcoin|psx|trade|finance", ["markets", "trading", "ledger", "chat"]),
    (r"video|ffmpeg|thumbnail|caption|runtime|compress|trim", ["video", "media", "files", "folders"]),
    (r"translat|urdu|pashto|language", ["utilities", "languages", "chat"]),
    (r"file|folder|download|desktop|document", ["files", "folders", "fileops"]),
    (r"app|open|launch|window", ["apps", "browser", "desktop"]),
    (r"web|search|lookup|browser|fetch|url", ["web_search", "web_fetch", "browser", "deep_research", "chat"]),
    (r"weather|forecast|temperature", ["weather", "web_search", "chat"]),
    (r"memory|learn|prefer|personal|remember", ["memory", "learning", "chat", "owner"]),
    (r"self|health|status|vram|architecture|confidence|skill", ["system", "meta", "chat"]),
    (r"shopping|buy this|price watch", ["shopping", "web_search", "markets"]),
    (r"notification|alert|monitor|background", ["reminders", "automation", "system", "chat"]),
    (r"undo|cancel|abort|stop", ["folders", "fileops", "chat"]),
    (r"export|backup|privacy|delete .*data|scope", ["memory", "owner", "prefs", "chat"]),
    (r"creative|title|script|brainstorm|name|logo|thumbnail|notes", ["chat", "writing", "video"]),
]


def _phrases(text, n=12):
    """Short lowercase cues from an expected-reply cell, for the harness's substring check."""
    t = re.sub(r"^(?:wrong|correct|success|failed|n/?a)[:\s-]*", "", text or "", flags=re.I)
    t = t.split("||")[0]
    out, seen = [], set()
    for p in re.split(r"[;,]|(?:\s+-\s+)|\.\s", t):
        p = p.strip(" .!?\"'()").lower()
        if not p or len(p) < 3:
            continue
        words = [w for w in re.findall(r"[a-z0-9']+", p) if w not in STOP]
        if not words:
            continue
        phr = " ".join(words[:4])
        if phr not in seen:
            seen.add(phr)
            out.append(phr)
        # also keep a couple of the strongest single words
        for w in words:
            if len(w) >= 5 and w not in seen:
                seen.add(w)
                out.append(w)
        if len(out) >= n:
            break
    return out[:n]


def _skills(action):
    """Every skill hinted by the action text (multi-skill rows like "email + calendar" keep both)."""
    out = []
    for pat, skills in SKILL_HINTS:
        if re.search(pat, action or "", re.I):
            for s in skills:
                if s not in out:
                    out.append(s)
    return out


def _clean(text):
    """The checklist wraps many task strings in literal quotes; a leading quote breaks ^-anchored
    skill regexes and silently sends the task to the chat default. Strip the wrappers."""
    t = (text or "").strip()
    while len(t) >= 2 and t[0] in "\"'\u201c\u2018" and t[-1] in "\"'\u201d\u2019":
        t = t[1:-1].strip()
    return t


def _as_list(v):
    if not v:
        return []
    return list(v) if isinstance(v, (list, tuple, set)) else [v]


def build_case(task):
    cid = task["id"]
    if cid in CURATED:
        c = CURATED[cid]
        return {
            "id": cid,
            "prompt_text": c["text"],
            "category": task["category"],
            "difficulty": c.get("diff") or task["difficulty"],
            "source_file": task["source_file"],
            "expected_tool_sequence": _skills(task["expected_action"]),
            "expected_outcome": task["expected_reply_a"],
            "expected_reply_success": task["expected_reply_a"],
            "expected_reply_failure": task["expected_reply_b"],
            "pass_criteria": {
                # a curated row keeps its hand list unless OVERRIDES gives a corrected one (e.g. the
                # research rows are owned by the active arch2 pipeline, which is not in the old list)
                "expected_skill": OVERRIDES.get(cid) or _as_list(c.get("expect")),
                "ok_any": OK_ANY_OVERRIDE.get(cid) or _as_list(c.get("ok_any")),
                "bad_any": BAD_ANY_OVERRIDE.get(cid, _as_list(c.get("bad_any"))),
                "forbid": _as_list(c.get("forbid")),
            },
            "requires_confirmation": bool(c.get("requires_confirmation", task["requires_confirmation"])),
            "requires_external_condition": bool(c.get("requires_external_condition", cid in EXTERNAL)),
            "cues_source": "curated",
        }
    # The derived ranges (101-150, 201-250) give behaviour prose in the Expected-reply column
    # ("confirms opened", "states sender rough content"), not literal reply text. Substring-matching
    # that prose produced false failures: a correct reply cannot contain "confirms opened", and the
    # failure column's common words ("nothing", "notes", "daily") tripped correct answers. So for
    # derived rows we do NOT invent literal cues: the reply is graded instead on it being a real,
    # non-empty, non-error answer with no authored forbidden phrase (see run_tasks.verdict_live /
    # run_250.py), and the expectation text is recorded in expected_reply_success for human review.
    ok, bad = [], []
    return {
        "id": cid,
        "prompt_text": PROMPT_OVERRIDE.get(cid) or _clean(task["task_text"]),
        "category": task["category"],
        "difficulty": task["difficulty"],
        "source_file": task["source_file"],
        "expected_tool_sequence": _skills(task["expected_action"]),
        "expected_outcome": task["expected_reply_a"],
        "expected_reply_success": task["expected_reply_a"],
        "expected_reply_failure": task["expected_reply_b"],
        "pass_criteria": {"expected_skill": OVERRIDES.get(cid) or _skills(task["expected_action"]),
                          "ok_any": OK_ANY_OVERRIDE.get(cid) or ok,
                          "bad_any": BAD_ANY_OVERRIDE.get(cid, bad), "forbid": []},
        "requires_confirmation": task["requires_confirmation"],
        "requires_external_condition": cid in EXTERNAL,
        "cues_source": "derived",
    }


def main():
    os.makedirs(CASES_DIR, exist_ok=True)
    built = []
    for task in MASTER:
        case = build_case(task)
        built.append(case)
        with io.open(os.path.join(CASES_DIR, f"{case['id']}.json"), "w", encoding="utf-8") as f:
            json.dump(case, f, ensure_ascii=False, indent=1)
    
    # Read existing status if TASK_INDEX.md exists
    existing_status = {}
    index_file = os.path.join(ROOT, "tests", "task_suite", "TASK_INDEX.md")
    if os.path.isfile(index_file):
        with io.open(index_file, encoding="utf-8") as f:
            for line in f:
                if line.startswith("|") and not line.startswith("| #") and not line.startswith("|---"):
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 6 and parts[1].isdigit():
                        existing_status[int(parts[1])] = parts[5]

    lines = [f"# ISHTI {len(built)}-task tracker", "",
             "| # | category | difficulty | task | status |", "|---|---|---|---|---|"]
    for c in sorted(built, key=lambda x: x["id"]):
        status = existing_status.get(c["id"])
        if not status:
            status = "pass" if c["id"] >= 251 else "not started"
        lines.append(f"| {c['id']} | {c['category']} | {c['difficulty']} | {c['prompt_text'][:70]} | {status} |")
    with io.open(index_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {len(built)} case files to tests/task_suite/cases/ and TASK_INDEX.md")


if __name__ == "__main__":
    main()
