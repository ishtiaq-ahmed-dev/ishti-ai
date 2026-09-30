"""FIX-9 class regression: redact/pdftables run() must decline, never AttributeError.

The generated edge/failure/injection rows reach run() with text the FILES/INLINE patterns
don't match; both skills called m.group() on None and crashed the whole request.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_redact_declines_unmatched_shapes():
    from ishti.skills.redact import skill
    for row in ("", "redacting sensitive data from text", "x" * 500, "redact"):
        out = skill.run(row, {})
        assert out is None or (isinstance(out, dict) and str(out.get("reply", "")).strip()), \
            f"redact.run({row[:20]!r}) must decline or reply, got {out!r}"


def test_pdftables_declines_unmatched_shapes():
    from ishti.skills.pdftables import skill
    for row in ("", "extract tables from D:/x.pdf", "x" * 500):
        out = skill.run(row, {})
        assert out is None or (isinstance(out, dict) and str(out.get("reply", "")).strip()), \
            f"pdftables.run({row[:20]!r}) must decline or reply, got {out!r}"
