"""Fallacy detection (inventory domain 6 #19): heuristic flags, honestly labelled candidates.

"check my argument: my opponent is an idiot so his plan fails"
"fallacy check: either we ban cars or the planet dies"

Pattern-based flags for the classic fallacies: ad hominem (attacking the person), false
dilemma (only two options framed), slippery slope (chain-of-catastrophe phrasing), appeal to
authority/.popularity (no source given), hasty generalization (always/never/everyone). Each
flag names the phrase that triggered it and why. The reply states plainly that these are
heuristic candidates for a human to judge — not proof of fallacy. T0, offline.
"""
import re

from ishti.skills import trace

I = re.I

CHECK = re.compile(
    r"^\s*(?:check\s+my\s+argument|fallacy\s+check|argument\s+check)\s*[:\-]\s*(?P<text>.+)$", I)

PATTERNS = [
    ("ad hominem", r"\b(idiot|stupid|moron|dumb|clown|loser|fool)\b.*\b(so|therefore|thus|hence)\b|"
                   r"\b(so|therefore|thus)\b.*\b(idiot|stupid|moron|dumb|clown|loser|fool)\b",
     "attacking the person instead of the argument"),
    ("false dilemma", r"\beither\b[^.?!]{3,80}\bor\b|\bonly two (?:options|choices|ways)\b|"
                      r"\bif you'?re not with us\b",
     "framing exactly two options when more may exist"),
    ("slippery slope", r"\bwill (?:only )?lead to\b|\bnext thing you know\b|\bbefore you know it\b|"
                       r"\bends? up (?:with|in)\b.*\b(?:everyone|total|complete)\b",
     "asserting an unstoppable chain of consequences without evidence"),
    ("appeal to authority", r"\bexperts? (?:say|agree|believe)\b|\bscientists? (?:say|agree)\b|"
                            r"\bstudies show\b(?![:\s]*\()",
     "authority invoked without a checkable source"),
    ("appeal to popularity", r"\beveryone (?:knows|does|agrees)\b|\beverybody (?:knows|does)\b|"
                             r"\bmost people (?:think|say|know)\b",
     "popularity substituted for evidence"),
    ("hasty generalization", r"\b(?:always|never)\b(?:\s+\w+){0,3}\s(?:works?|fails?|happens?)\b|"
                             r"\bone time\b.*\bso (?:it|they) always\b",
     "a sweeping claim from little or no evidence"),
]


def check(text):
    flags = []
    for name, pat, why in PATTERNS:
        m = re.search(pat, text, I)
        if m:
            flags.append((name, m.group(0).strip()[:60], why))
    if not flags:
        return ("No common fallacy patterns found. That is not a guarantee the argument is "
                "sound — only that none of the checked patterns "
                "(ad hominem, false dilemma, slippery slope, appeal to authority/popularity, "
                "hasty generalization) appear.")
    lines = [f"**{len(flags)} fallacy pattern(s) flagged** — heuristic candidates, not proof:", ""]
    for name, phrase, why in flags:
        lines.append(f"- **{name}**: \"{phrase}\" — {why}.")
    lines += ["", "A pattern match means the phrasing resembles the fallacy; whether the "
              "reasoning actually fails is a judgement only a human reading the full context "
              "can make."]
    return "\n".join(lines)


def match(text):
    return bool(CHECK.match(text.strip()))


def run(text, session):
    t = text.strip()
    if (m := CHECK.match(t)):
        trace(session, "argumentcheck", "check")
        return {"reply": check(m.group("text")), "sources": []}
    return None


if __name__ == "__main__":
    assert match("check my argument: my opponent is an idiot so his plan fails")
    assert match("fallacy check: either we ban cars or the planet dies")
    assert not match("check my email")
    assert not match("argument of the perihelion")

    r = check("my opponent is an idiot so his plan fails")
    assert "ad hominem" in r, r
    r2 = check("either we ban cars or the planet dies; everyone knows it")
    assert "false dilemma" in r2 and "appeal to popularity" in r2, r2
    r3 = check("premise one: all men are mortal. premise two: Socrates is a man. "
               "conclusion: Socrates is mortal.")
    assert "No common fallacy patterns" in r3, r3
    assert "not a guarantee" in r3, r3
    r4 = check("studies show that house prices always rise; next thing you know everyone will be renting")
    assert "appeal to authority" in r4 and "slippery slope" in r4, r4
    print("[self-check] argumentcheck OK - planted fallacies flagged by name with the triggering "
          "phrase, clean syllogism passes, candidates-not-proof caveat always stated")
