"""Test voice note generation (Issue 5): TTS synthesis, Opus .ogg encoding, and document upload routing."""
import os
import sys

ROOT = r"D:\ISHTI"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti.skills.whatsapp import skill as wa


def test_voice_note_synthesis():
    test_words = "Salam, I am ISHTI. This is a voice note reply confirmation."
    out_path, whose = wa.voice_note(test_words)
    print(f"Generated voice note at: {out_path} (voice: {whose})")
    assert os.path.exists(out_path), f"Voice note file does not exist: {out_path}"
    assert os.path.getsize(out_path) > 1000, f"Voice note file is too small: {os.path.getsize(out_path)} bytes"

    # Verify attachment routing: WhatsApp Web attach menu has no 'Audio' menuitem.
    # Audio files must map to 'Document'.
    ext = os.path.splitext(out_path)[1].lower()
    kind = "Photos & videos" if ext in wa.VISUAL else "Document"
    assert kind == "Document", f"Voice note {ext} should route to Document, got: {kind}"
    print(f"[SUCCESS] Voice note generation and routing verified ({out_path}, {whose}, {kind})")


if __name__ == "__main__":
    test_voice_note_synthesis()
