"""Screen understanding (read-only): screenshot -> exact text (OCR) + meaning (local vision model).

Distinct from `desktop`'s screen_read (whole screen, one question) and from `screen_control`
(interaction): this one answers *questions about* a window or the screen - "what does the error
say", "summarise this window", "what's in the chart". Read-only, T0, no confirmation.

Verified on this machine (2026-09-28): ocr_screen() read the live desktop (Freebuff window text);
the vision path uses the same Gemma-3 `look()` the desktop/extract/video skills already use.
"""
import re

from ishti.skills import trace

I = re.I

WINDOW = re.compile(r"\b(?:window|app|application|tab|program)\b", I)
TRIGGER = re.compile(
    r"\b(?:what(?:'s| is)?\s+(?:on|in)\s+(?:my\s+|the\s+)?(?:screen|window|this\s+window)|"
    r"read\s+(?:the\s+)?(?:error|message|dialog|popup|chart|table|graph)\b[^.?!]*\b(?:screen|window)?|"
    r"(?:what\s+does|what\s+do)\s+(?:the\s+)?(?:error|message|dialog|chart|graph|table)\s+(?:on\s+my\s+)?(?:screen|window)\s+say|"
    r"summarise\s+(?:my\s+|the\s+)?(?:screen|window)|summarize\s+(?:my\s+|the\s+)?(?:screen|window)|"
    r"what\s+am\s+i\s+looking\s+at|analys?e\s+my\s+screen)", I)
# "read (the text on) my screen" stays with the desktop skill's screen_read (its labelled case) -
# this skill is for *questions about* what's shown, not the plain read.
ERRORS = re.compile(r"\b(?:error|failed|failure|exception|crash|problem|warning)\b", I)


def match(text):
    return bool(TRIGGER.search(text))


def run(text, session):
    from ishti.skills.desktop.skill import _shot
    from ishti.ocr import ocr_screen
    import os
    path = _shot()
    trace(session, "screen", "reading the screen (OCR)", status="start")
    ocr_text, _ = ocr_screen()
    trace(session, "screen", f"read {len(ocr_text)} chars", status="done")

    about_error = bool(ERRORS.search(text))
    asks_window = bool(WINDOW.search(text))
    question = ("This is the owner's computer screen. "
                + ("Find any error or failure message and quote it exactly."
                   if about_error else
                   "Say what application/window is open and summarise what is shown.")
                + (" Focus on the window they are asking about."
                   if asks_window else ""))

    trace(session, "vision", "asking the local vision model", status="start")
    try:
        from ishti.skills.video.pro import look
        from PIL import Image
        small = path.replace(".png", "_read.jpg")
        im = Image.open(path).convert("RGB")
        im.thumbnail((1600, 1600))
        im.save(small, quality=88)
        seen = look(small, question)
        os.remove(small)
        vision_text = seen.get("text") or ""
    except Exception as exc:
        vision_text = ""
        trace(session, "vision", f"vision model unavailable: {exc}", status="error")

    parts = []
    if about_error and ocr_text.strip():
        err_lines = [l.strip() for l in ocr_text.splitlines()
                     if ERRORS.search(l) and len(l.strip()) > 8]
        if err_lines:
            parts.append("Error lines found on screen:\n" + "\n".join(f"- {l}" for l in err_lines[:5]))
    if vision_text.strip():
        parts.append(vision_text.strip())
    if not parts:
        parts.append("I couldn't read anything useful from the screen right now."
                     + (f" Raw OCR said: {ocr_text[:300]}" if ocr_text.strip() else " The screen may be locked."))
    reply = "\n\n".join(parts) + f"\n\n(Screenshot: {path})"
    return {"reply": reply, "sources": []}


if __name__ == "__main__":
    assert not match("read the text on my screen")  # desktop skill's labelled screen_read case
    assert match("what does the error on my screen say")
    assert match("summarise what's in this window")
    assert match("read the chart on my screen")
    assert match("what am i looking at")
    assert not match("click on the error")
    assert not match("take a screenshot")  # desktop skill's job
    print("[self-check] screen_understand OK - routing only (real OCR/vision verified on this PC interactively)")
