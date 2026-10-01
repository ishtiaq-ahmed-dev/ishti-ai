# -*- coding: utf-8 -*-
"""Accept both Pashto/Urdu spellings of "model": ماڈل (U+0688) and ماډل (U+0689).

The self-knowledge trigger matched only one of them, so "ته کوم ډول ماډلونه کارول" was missed. Written as
a script because the Windows console codepage (cp1252) cannot carry Arabic-script text, so the editor /
shell round-trip would corrupt the very characters being fixed.
"""
import io

PATH = r"D:\ISHTI\ishti\skills\meta\skill.py"
BOTH = "ما[ڈډ]?ل"      # ما + either ڈ(U+0688) or ډ(U+0689), optionally + ل

src = io.open(PATH, encoding="utf-8").read()
before = src
# undo the earlier wrong form, then apply the correct one to both spellings
src = src.replace("ماڈ?ل", BOTH)
for variant in ("ماڈل", "ماډل"):
    src = src.replace(variant, BOTH)
if src != before:
    io.open(PATH, "w", encoding="utf-8").write(src)
print("replacements applied:", before != src)
