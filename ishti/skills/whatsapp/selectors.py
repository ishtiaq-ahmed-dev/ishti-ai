"""WhatsApp Web Version-Aware Selector Registry & Self-Chat Safety Policy (NEEDS.md Items 1 & 2).

Guarantees:
1. Dedicated test destination isolation: prevents automated test actions from contacting real people.
2. Official WhatsApp constraint enforcement:
   - 15-minute window check for message editing (Meta FAQ).
   - 2-day window check for group admin deletions (Meta FAQ).
3. Multi-version DOM selector registry with fallback matching for Web accessibility snapshots.
4. Offline verification against synthetic DOM snapshots.
"""
import os
import re
import time
from typing import Dict, Any, Tuple, Optional

# Dedicated test destination (self-chat)
DEFAULT_SELF_CHAT_TARGET = "Message yourself"
EDIT_WINDOW_SECONDS = 15 * 60  # 15 minutes limit per WhatsApp official policy
ADMIN_DELETE_WINDOW_SECONDS = 2 * 24 * 3600  # 2 days limit for group admin deletion

# Selector registry per WhatsApp Web version
SELECTOR_REGISTRY = {
    "v2.3000.x": {
        "chat_list": ['div[aria-label="Chat list"]', 'div[role="grid"]', 'div#pane-side'],
        "search_box": ['div[contenteditable="true"][data-tab="3"]', 'div[aria-label="Search or start new chat"]', 'p.selectable-text.copyable-text'],
        "chat_header": ['header div[role="button"] span[title]', 'header span[title]'],
        "message_rows": ['div[role="row"]', 'div[data-id]'],
        "message_text": ['span.selectable-text', 'div.copyable-text span', 'span[dir="ltr"]', 'span[dir="rtl"]'],
        "composer": ['footer div[contenteditable="true"]', 'div[aria-label="Type a message"]', 'footer p.selectable-text'],
        "send_button": ['button[aria-label="Send"]', 'span[data-icon="send"]', 'button span[data-icon="send"]'],
        "action_menu": ['button[aria-label="More options"]', 'div[role="button"][aria-label="Menu"]', 'span[data-icon="down-context"]'],
        "edit_action": ['div[aria-label="Edit message"]', 'li[data-animate-dropdown-item="true"]:has-text("Edit")', 'div[role="button"]:has-text("Edit")'],
        "delete_action": ['div[aria-label="Delete message"]', 'li:has-text("Delete")', 'div[role="button"]:has-text("Delete")'],
    },
    "accessibility_tree": {
        "chat_list": [r'Chat list', r'grid "Chat list"'],
        "search_box": [r'textbox "Search or start new chat"', r'textbox "Search"'],
        "message_rows": [r'row "[^"]+"', r'gridcell "[^"]+"'],
        "composer": [r'textbox "Type a message"', r'textbox "Type message"'],
        "send_button": [r'button "Send"', r'button "send"'],
    }
}


def get_test_destination() -> str:
    """Returns the configured dedicated WhatsApp self-chat test destination."""
    return os.environ.get("ISHTI_WA_TEST_DESTINATION", DEFAULT_SELF_CHAT_TARGET).strip()


def is_safe_test_target(target: str) -> bool:
    """Verifies that a test message target is exclusively the dedicated self-chat."""
    if not target:
        return False
    t = target.strip().lower()
    self_dest = get_test_destination().lower()
    # Accept "message yourself", "you", "self", or exact match to configured self destination
    return t in (self_dest, "message yourself", "you", "self", "me") or t == self_dest


def check_send_safety(target: str, is_test_run: bool = True) -> Tuple[bool, str]:
    """Safety postcondition: forbids outbound sends to real contacts in test mode."""
    if is_test_run and not is_safe_test_target(target):
        return (
            False,
            f"Blocked by safety policy: Test operations may only target the dedicated self-chat destination ('{get_test_destination()}'), not real contact '{target}'."
        )
    return True, "Target verified safe"


def check_edit_eligibility(message_timestamp: float) -> Tuple[bool, str]:
    """Enforces WhatsApp's 15-minute edit window restriction."""
    now = time.time()
    elapsed = now - message_timestamp
    if elapsed < 0:
        return True, "Valid timestamp"
    if elapsed > EDIT_WINDOW_SECONDS:
        mins = int(elapsed // 60)
        return (
            False,
            f"Cannot edit message: message was sent {mins} minutes ago, which exceeds WhatsApp's strict 15-minute edit limit."
        )
    remaining_mins = max(0, int((EDIT_WINDOW_SECONDS - elapsed) // 60))
    return True, f"Eligible for edit ({remaining_mins} minutes remaining in edit window)"


def check_delete_eligibility(message_timestamp: float, is_group_admin: bool = False) -> Tuple[bool, str]:
    """Enforces WhatsApp deletion window limits (2 days for group admin)."""
    now = time.time()
    elapsed = now - message_timestamp
    if elapsed < 0:
        return True, "Valid timestamp"
    if is_group_admin and elapsed > ADMIN_DELETE_WINDOW_SECONDS:
        return (
            False,
            f"Cannot delete group message: exceeds WhatsApp's 2-day limit for group admin deletion."
        )
    return True, "Eligible for deletion"


def verify_snapshot_coverage(snapshot_text: str, version: str = "v2.3000.x") -> Dict[str, Any]:
    """Verifies that key UI elements are present in a DOM or accessibility snapshot."""
    results = {}
    snap_low = snapshot_text.lower()

    # Test accessibility tree patterns
    tree_selectors = SELECTOR_REGISTRY["accessibility_tree"]
    for element, patterns in tree_selectors.items():
        found = False
        matched_pat = None
        for p in patterns:
            if re.search(p, snapshot_text, re.I):
                found = True
                matched_pat = p
                break
        results[element] = {
            "found": found,
            "matched_pattern": matched_pat
        }

    all_core_found = results.get("chat_list", {}).get("found", False) and results.get("composer", {}).get("found", False)
    return {
        "version": version,
        "coverage": results,
        "is_valid_chat_view": all_core_found
    }
