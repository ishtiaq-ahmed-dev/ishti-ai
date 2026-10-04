"""Unit tests for CRM scope, data contract, and personal vs business separation."""
import pytest
from ishti.crm_contract import (
    SEPARATE_PERSONAL_AND_BUSINESS,
    AUTO_INGEST_FROM_CHATS,
    is_crm_conversion_command,
    can_ingest_from_message,
    create_client,
    get_client,
    list_clients,
    delete_client,
)


def test_separation_policy_defaults():
    # By default, personal contacts and business CRM are strictly separated
    assert SEPARATE_PERSONAL_AND_BUSINESS is True
    assert AUTO_INGEST_FROM_CHATS is False


def test_conversion_command_detection():
    assert is_crm_conversion_command("Please add him as a client in the CRM")
    assert is_crm_conversion_command("convert this lead into a client")
    assert is_crm_conversion_command("create CRM record for Tariq")
    assert not is_crm_conversion_command("hello brother how are you")
    assert not is_crm_conversion_command("schedule a dinner tonight")


def test_inbound_chat_does_not_auto_create_crm():
    can_ingest, reason = can_ingest_from_message("Hi Ishtiaq, what is the quote?", sender_name="Zubair")
    assert not can_ingest
    assert "Personal/Business separation policy" in reason


def test_client_crud():
    name = "Test Enterprise Corp"
    rec = create_client(
        name=name,
        company="Enterprise Ltd",
        channel_source="direct",
        phone="+92 300 1122334",
        status="active",
        financial_terms="Net 30"
    )
    assert rec["contact_name"] == name
    assert rec["status"] == "active"

    # Query
    fetched = get_client("test_enterprise_corp")
    assert fetched is not None
    assert fetched["company"] == "Enterprise Ltd"

    # Clean up
    assert delete_client("test_enterprise_corp")
    assert get_client("test_enterprise_corp") is None
