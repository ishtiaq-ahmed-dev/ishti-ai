"""Unit tests for CRM scope, data contract, and personal vs business separation."""
import pytest
import ishti.crm_contract as crm_contract
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


@pytest.fixture(autouse=True)
def scratch_crm(tmp_path, monkeypatch):
    monkeypatch.setattr(crm_contract, "CRM_STORE_PATH", str(tmp_path / "crm_clients.json"))


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
    with pytest.raises(PermissionError):
        create_client("Zubair", channel_source="whatsapp")


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


def test_same_name_does_not_overwrite_and_corruption_preserves_data():
    first = create_client("Ali", company="Company One")
    second = create_client("Ali", company="Company Two")
    assert first["client_id"] != second["client_id"]
    assert len(list_clients()) == 2
    assert get_client("Ali") is None  # ambiguous names never select an arbitrary record
    assert get_client(first["client_id"])["company"] == "Company One"
    with open(crm_contract.CRM_STORE_PATH, "w", encoding="utf-8") as stream:
        stream.write("{damaged")
    with pytest.raises(RuntimeError):
        create_client("Third")
    assert open(crm_contract.CRM_STORE_PATH, encoding="utf-8").read() == "{damaged"
