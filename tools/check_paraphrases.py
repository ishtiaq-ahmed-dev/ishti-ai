# -*- coding: utf-8 -*-
"""Check that the self-knowledge trigger recognises its paraphrases in all four languages.

Run as a script (not via `python -c`) because the Windows console codepage is cp1252 and cannot carry
Arabic-script text - passing the strings through the shell silently mangles them.
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ishti.skills.meta.skill import OPS   # noqa: E402

RX = dict(OPS)["self_check"]

CASES = {
    "EN": ["who are you", "whose assistant are you", "what pc are you running on",
           "what gpu do i have", "how much ram do i have", "which models are you using",
           "what skills do you have", "refresh your self-knowledge", "your pc specs",
           "what are your specs"],
    "UR": ["تم کون ہو", "میرے کمپیوٹر کی سپیفیکیشن کیا ہیں", "میری گرافکس کارڈ کون سی ہے",
           "تم کون کون سی ماڈل استعمال کرتے ہو", "تمہارے پاس کون کون سی اسکل ہیں",
           "تم کیا نہیں کر سکتے"],
    "PS": ["ته ول څوک یې", "زما کمپیوټر څه ډول دی", "زما ګرافیکس کارډ څه دی",
           "ته کوم ډول ماډلونه کارول", "ته څه مهارتونه لرې", "ته څه نه شې کولی"],
    "RU": ["tum kaun ho", "mere computer ki specs kya hain", "meri graphics card konsi hai",
           "kaun kaun si models use karte ho", "tumhare paas kaun kaun si skills hain",
           "tum kya nahi kar sakte"],
}
MUST_NOT = ["what is the weather tomorrow in Peshawar", "play some music",
            "merge this folder of images into one pdf"]

misses, false_pos = [], []
for lang, cases in CASES.items():
    for text in cases:
        if not RX.search(text):
            misses.append((lang, text))
for text in MUST_NOT:
    if RX.search(text):
        false_pos.append(text)

out = io.open(os.path.join(ROOT, "data", "paraphrase_report.txt"), "w", encoding="utf-8")
total = sum(len(v) for v in CASES.values())
out.write("paraphrases checked: %d\nmisses: %d\nfalse positives: %d\n" % (total, len(misses), len(false_pos)))
for lang, t in misses:
    out.write("MISS %s %r\n" % (lang, t))
for t in false_pos:
    out.write("FALSE POSITIVE %r\n" % t)

# --- why did it miss? probe the pieces, from this file (never through the console) ---------------
import re  # noqa: E402

probe = CASES["PS"][3]
out.write("\n--- probe ---\n")
out.write("text: %r\n" % probe)
for frag in ("کوم", r"کوم\s*ډول\s*ماډل", "کوم ډول ماډلونه"):
    out.write("frag %-28r -> %s\n" % (frag, bool(re.search(frag, probe))))
src = io.open(os.path.join(ROOT, "ishti", "skills", "meta", "skill.py"), encoding="utf-8").read()
i = src.find("کوم")
seg = src[i - 2:i + 20]
out.write("file segment: %r\n" % seg)
out.write("segment in text: %s\n" % bool(re.search(re.escape(seg.split("|")[0]), probe)))
out.write("seg codepoints: %s\n" % [hex(ord(c)) for c in seg[:12]])
sub = probe[3:19]
out.write("text codepoints: %s\n" % [hex(ord(c)) for c in sub])
out.close()
print("checked=%d misses=%d false_positives=%d (details in data/paraphrase_report.txt)"
      % (total, len(misses), len(false_pos)))
