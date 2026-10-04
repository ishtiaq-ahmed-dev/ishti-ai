"""Unit tests for WhatsApp selectors, self-chat isolation, and edit/delete eligibility."""
import os
import time
import pytest
from ishti.skills.whatsapp.selectors import (
    is_safe_test_target,
    check_send_safety,
    check_edit_eligibility,
    check_delete_eligibility,
    verify_snapshot_coverage,
    EDIT_WINDOW_SECONDS,
)

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fixtures", "whatsapp")


def test_self_chat_isolation():
    # Only self-chat destinations are allowed during test runs
    assert is_safe_test_target("Message yourself")
    assert is_safe_test_target("self")
    assert is_safe_test_target("me")
    assert is_safe_test_target("you")

    # Real people are rejected during test runs
    assert not is_safe_test_target("Zubair Client")
    assert not is_safe_test_target("+92 300 0000000")

    safe, msg = check_send_safety("Zubair Client", is_test_run=True)
    assert not safe
    assert "Blocked by safety policy" in msg


def test_edit_eligibility_window():
    now = time.time()

    # Message sent 5 minutes ago -> eligible
    eligible, msg = check_edit_eligibility(now - (5 * 60))
    assert eligible
    assert "remaining in edit window" in msg

    # Message sent 20 minutes ago -> exceeds 15 min limit
    expired, reason = check_edit_eligibility(now - (20 * 60))
    assert not expired
    assert "exceeds WhatsApp's strict 15-minute edit limit" in reason


def test_delete_eligibility_window():
    now = time.time()

    # Within 2 days for admin -> eligible
    eligible, _ = check_delete_eligibility(now - (3600 * 24), is_group_admin=True)
    assert eligible

    # Exceeds 2 days for admin -> rejected
    expired, reason = check_delete_eligibility(now - (3600 * 49), is_group_admin=True)
    assert not expired
    assert "exceeds WhatsApp's 2-day limit" in reason


def test_snapshot_verification_against_fixture():
    fixture_path = os.path.join(FIXTURES_DIR, "dom_snapshot_v2_3000.txt")
    assert os.path.exists(fixture_path)

    with open(fixture_path, "r", encoding="utf-8") as f:
        snapshot_text = f.read()

    res = verify_snapshot_coverage(snapshot_text)
    assert res["is_valid_chat_view"] is True
    assert res["coverage"]["chat_list"]["found"] is True
    assert res["coverage"]["composer"]["found"] is True
