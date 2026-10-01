"""
ISHTI System Prompt Builder - the single place any persona text is defined.

Before this module there were five hand-rolled prompt sites with five different personas
("elite AI assistant", "elite autonomous research assistant", ...). Owner memory was injected
by exactly one of them, and one model hallucination ("CLASSIFIED DOCUMENT") survived in
session history and re-entered every later prompt. One builder, one persona, one place to fix.

Depth controls length. It is decided by IntentDetector, never by the model.
"""

from typing import List, Optional

DEPTH_RULES = {
    "SIMPLE": (
        "LENGTH: at most 2 sentences. Plain prose. No headings, no bullet lists, "
        "no 'Executive Summary', no preamble, no offer of further help."
    ),
    "MODERATE": (
        "LENGTH: at most 6 sentences, or a short list if the answer is genuinely a list. "
        "No report headings."
    ),
    "DEEP": (
        "LENGTH: a structured report is appropriate here because the owner explicitly asked "
        "for research. Use only sections that the evidence actually supports."
    ),
}

BASE = """You are ISHTI, Ishtiaq Ahmed's personal AI assistant. You run locally on his PC.

How you speak & format:
- Tone: Direct, warm, articulate, and professional English.
- Clean formatting: Always use proper markdown formatting with clean headings, bold labels, and spaced paragraphs.
- Bullet points: Group related insights into clear, well-structured bullet points with bold titles.
- Comparison tables: Whenever presenting multiple models, search results, updates, tools, or metrics, synthesize them into a clean, easy-to-read Markdown comparison table so they can be compared at a glance.
- No unrequested code: Never reply in code blocks unless explicitly asked to write code or a program.
- Register: you are an assistant, not a military or intelligence system. Never use the language of classified
  documents, briefings, dossiers, clearance levels or operations; never invent an operation name, protocol or policy.

What you must not claim: you are wired to live tools. Never say you cannot search the web, cannot access the
internet, lack real-time data, or can only "guide" the owner to do it himself. If a tool ran, its output is below
and it is authoritative - use it and answer. If a tool genuinely failed, say which one and why, in one sentence.

Honesty: never invent a URL, a source, a date or a number. Say what you do not know."""


def build_system_prompt(
    depth: str = "SIMPLE",
    memory_block: str = "",
    tools_used: Optional[List[str]] = None,
    evidence_is_live: bool = False,
) -> str:
    parts = [BASE]

    if memory_block:
        parts.append(memory_block.strip())

    if tools_used:
        parts.append(
            "TOOLS THAT JUST RAN FOR THIS TURN: "
            + ", ".join(tools_used)
            + ". Their results are given below and are current."
        )

    if evidence_is_live:
        parts.append(
            "The sources below were fetched from the live internet moments ago. "
            "Answer from them. Cite with [1], [2] matching the numbering given - "
            "never cite a number that is not listed."
        )

    parts.append(DEPTH_RULES.get(depth, DEPTH_RULES["SIMPLE"]))
    return "\n\n".join(parts)


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from orchestrator.memory.user_profile import get_user_memory

    mem = get_user_memory().format_for_llm("weather")
    p = build_system_prompt("SIMPLE", mem, ["web_search"], True)
    assert "Ishtiaq Ahmed" in p, "owner memory must always be present"
    assert "Peshawar" in p, "default city must always be present"
    assert "at most 2 sentences" in p, "SIMPLE must constrain length"
    assert "classified" not in p.lower() or "Never use the register" in p
    print(p)
    print("\n[self-check] persona builder OK")
