"""Unit test for WhatsApp conversation context extraction, early stopping, and prompt injection."""
import re
import sys
import os

ROOT = r"D:\ISHTI"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti.skills.whatsapp import skill as wa


def test_read_chat_messages_extraction():
    snap = """
- generic "Chat list" [ref=e10]
- generic "Ali" [ref=e20]
- region "Message list" [ref=e50]:
  - generic "Ali: Hey Ishtiaq, are we meeting at 3 PM today? 11:20 am" [ref=e51]
  - generic "You: Let me check my schedule 11:22 am" [ref=e52]
  - generic "Ali: Sure, let me know soon because I need to book the room. 11:25 am" [ref=e53]
- textbox "Type a message to Ali" [ref=e60]
"""
    messages = wa.read_chat_messages(snap, max_messages=10)
    assert len(messages) >= 2, f"Expected at least 2 messages, got {len(messages)}"
    assert any(m["sender"] == "Ali" and "room" in m["text"].lower() for m in messages)
    assert any(m["sender"] == "You" for m in messages)


def test_early_stopping():
    # Long chat history: 15 messages, question occurs at message 12
    lines = ['- region "Message list":']
    for i in range(10):
        lines.append(f'  - generic "Ali: Old message {i} 10:{i:02d} am"')
    lines.append('  - generic "Ali: Can we reschedule our project sync to 4 PM? 11:15 am"')
    lines.append('  - generic "You: Let me verify 11:16 am"')
    lines.append('  - generic "Ali: Thanks, please confirm when you can. 11:17 am"')
    lines.append('- textbox "Type a message"')
    snap = "\n".join(lines)

    messages = wa.read_chat_messages(snap, max_messages=10)
    # Early stopping should capture the recent question and turns, not the full 15
    assert len(messages) <= 6, f"Expected early stop <= 6, got {len(messages)}"
    assert any("reschedule" in m["text"].lower() or "confirm" in m["text"].lower() for m in messages)


def test_context_formatting():
    messages = [
        {"sender": "Qasim", "text": "Did you finish the financial report?", "time": "2:15 pm"},
        {"sender": "You", "text": "Almost done, exporting the charts now", "time": "2:16 pm"},
        {"sender": "Qasim", "text": "Great, please send it over as soon as it is ready.", "time": "2:18 pm"}
    ]
    formatted = wa.format_chat_context("Qasim", messages)
    assert "Qasim: Did you finish the financial report?" in formatted
    assert "You: Almost done" in formatted
    assert "Recent WhatsApp conversation with Qasim" in formatted


if __name__ == "__main__":
    test_read_chat_messages_extraction()
    test_early_stopping()
    test_context_formatting()
    print("[SUCCESS] All WhatsApp context tests passed!")
