"""Press release, product description, FAQ (inventory domain 1 #23, #24, #25).

"draft a press release: ISHTI 2.0 launches 2026-10-05 with offline voice"
"write a product description for the walnut-wood clock, 4500 pkr, handmade in chitral"
"generate an faq from: returns accepted within 14 days; warranty 1 year; shipping 3 days"

The owner's stated facts are repeated verbatim — the template only adds connective structure,
never invented numbers, names or claims. T0, pure text.
"""
import os
import re

from ishti.skills import trace

I = re.I

PRESS = re.compile(r"^\s*draft\s+an?\s+press\s+release\s*[:\-]\s*(?P<facts>.+)$", I)
PROD = re.compile(
    r"^\s*(?:write|draft)\s+an?\s+product\s+description\s+(?:for|of)\s+(?P<facts>.+)$", I)
FAQ = re.compile(
    r"^\s*(?:generate|make|create)\s+an?\s+faq\s+(?:from|for)\s*[:\-]?\s*(?P<facts>.+)$", I)


def _sentences(facts):
    parts = re.split(r"\s*;\s*|\.\s+|\n", facts.strip())
    return [p.strip(" .") for p in parts if p.strip(" .")]


def press(facts):
    lines = [f"PRESS RELEASE (DRAFT — facts as stated: nothing added)", "",
             f"FOR IMMEDIATE RELEASE", ""]
    first = facts.split(".")[0].strip()
    lines.append(first.upper() if len(first) < 80 else first)
    lines += ["", facts.strip(), "",
              "About: [FILL: one-line company background]",
              "Contact: [FILL: name, email, phone]", "###"]
    return "\n".join(lines)


def product(facts):
    sents = _sentences(facts)
    bullets = [f"- {s}" for s in sents[1:]] or ["- [FILL: key feature]"]
    lines = [f"{sents[0]}" if sents else "[FILL: product name]", "",
             facts.strip(), "",
             "Highlights:", *bullets,
             "Price and availability: [FILL if not stated above]"]
    return "\n".join(lines)


def faq(facts):
    sents = _sentences(facts)
    lines = ["FREQUENTLY ASKED QUESTIONS", ""]
    for i, s in enumerate(sents, 1):
        q = s.rstrip(".")
        lines += [f"Q{i}. What about: {q}?", f"A{i}. {q}.", ""]
    lines.append("Questions drafted from your stated facts only — reword into natural "
                 "customer language before publishing.")
    return "\n".join(lines)


def match(text):
    t = text.strip()
    return bool(PRESS.match(t) or PROD.match(t) or FAQ.match(t))


def run(text, session):
    t = text.strip()
    if (m := PRESS.match(t)):
        trace(session, "pressrel", "draft")
        return {"reply": press(m.group("facts")), "sources": []}
    if (m := PROD.match(t)):
        trace(session, "pressrel", "product")
        return {"reply": product(m.group("facts")), "sources": []}
    if (m := FAQ.match(t)):
        trace(session, "pressrel", "faq")
        return {"reply": faq(m.group("facts")), "sources": []}
    return None


if __name__ == "__main__":
    assert match("draft a press release: ISHTI 2.0 launches 2026-10-05 with offline voice")
    assert match("write a product description for the walnut-wood clock, 4500 pkr, handmade in chitral")
    assert match("generate an faq from: returns accepted within 14 days; warranty 1 year")
    assert not match("press the button")
    assert not match("describe the product lifecycle")

    r = run("draft a press release: ISHTI 2.0 launches 2026-10-05 with offline voice", {})
    assert "2026-10-05" in r["reply"] and "PRESS RELEASE" in r["reply"], r
    facts = "the walnut-wood clock, 4500 pkr, handmade in chitral"
    r2 = run("write a product description for " + facts, {})
    assert "4500 pkr" in r2["reply"] and "chitral" in r2["reply"], r2
    r3 = run("generate an faq from: returns accepted within 14 days; warranty 1 year", {})
    assert "14 days" in r3["reply"] and "Q1." in r3["reply"] and "Q2." in r3["reply"], r3
    print("[self-check] pressrel OK - press release / product description / FAQ drafts repeat the "
          "stated facts verbatim and add structure only, no invented claims")
