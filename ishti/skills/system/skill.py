"""System skill: hardware telemetry, short."""
import re

from orchestrator.intent_detector import IntentDetector


MINE = re.compile(r"\b(?:my|this|the)\s+(?:pc|computer|laptop|system|machine)(?:'s)?\s+(?:specs?|specifications|status|usage)\b|"
                  r"\b(?:specs?|specifications|usage|status)\s+of\s+(?:my|this)\s+(?:pc|computer|laptop|system|machine)\b", re.I)


def match(text):
    # only this PC's own state; "price and specs of an RTX 5070 Ti" is a web question (web_search)
    return bool(MINE.search(text)) or \
        IntentDetector.classify(re.sub(r"\b(?:the|a|an|my)\s+", "", text, flags=re.I))["mode"] == "PC_TELEMETRY"


def run(text, session):
    from desktop.computer_use import get_computer_use_agent
    d = get_computer_use_agent().get_system_telemetry()
    g = d.get("gpu") if isinstance(d.get("gpu"), dict) else {}
    lines = [f"CPU {d.get('cpu_percent', 0)}% ({d.get('cpu_count', 0)} cores)",
             f"RAM {d.get('ram_used_gb', 0)} / {d.get('ram_total_gb', 0)} GB ({d.get('ram_percent', 0)}%)"]
    if g:
        lines.append(f"GPU {g.get('name', '')}: {g.get('used_vram_mb', 0)} / {g.get('total_vram_mb', 0)} MB VRAM, {g.get('temp_c', '?')}°C")
    lines += [f"Disk {k} {v.get('free_gb', 0)} GB free of {v.get('total_gb', 0)} GB" for k, v in d.get("disks", {}).items()]
    return {"reply": "\n".join(f"- {line}" for line in lines), "sources": [], "telemetry": d}
