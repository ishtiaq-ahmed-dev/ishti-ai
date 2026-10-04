"""Connector skill routing with all account and network calls mocked."""
from unittest.mock import patch

from ishti.skills.connectors import skill


def test_aliases_and_unrelated_requests():
    assert skill.match("connect Google Drive")
    assert skill.match("disconnect outlook mail")
    assert skill.match("what is connected")
    assert not skill.match("connect this project to the network")


def test_status_and_help_do_not_start_consent():
    with patch.object(skill.oauth, "status", side_effect=lambda name: f"{name}: not linked"), \
            patch("ishti.connectors.start") as start:
        listed = skill.run("what is connected", {})
        help_text = skill.run("what services can i connect", {})
        assert "not linked" in listed["reply"]
        assert "drive" in help_text["reply"]
        start.assert_not_called()


def test_connect_without_registration_reports_real_start_result():
    with patch("ishti.connectors.start", return_value={
        "waiting": False, "reply": "Google app registration is missing."
    }) as start:
        result = skill.run("connect google drive", {"trace": lambda **_ev: None})
        assert result["reply"] == "Google app registration is missing."
        start.assert_called_once_with("drive")


def test_disconnect_is_scoped_to_named_service():
    with patch.object(skill.oauth, "disconnect", return_value="Drive disconnected") as disconnect:
        assert skill.run("disconnect google drive", {})["reply"] == "Drive disconnected"
        disconnect.assert_called_once_with("drive")
