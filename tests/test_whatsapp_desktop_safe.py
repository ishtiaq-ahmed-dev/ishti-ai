"""Blind desktop WhatsApp automation never sends to an unverified chat."""
from unittest.mock import patch

from ishti.skills.whatsapp import skill


def test_desktop_fallback_does_not_touch_mouse_keyboard_or_send():
    with patch("desktop.computer_use.get_computer_use_agent", side_effect=AssertionError("desktop touched")):
        session = {}
        result = skill._desktop_gate(session, "Ali", ["synthetic hello"], None)
        direct = skill.send_desktop("Ali", ["synthetic hello"], None, session)
    assert result["verified"] is False and "did not send" in result["reply"]
    assert direct["verified"] is False and "did not send" in direct["reply"]
    assert not session.get("pending")
