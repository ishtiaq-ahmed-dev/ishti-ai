"""CRM dashboard mapping and deletion checks with scratch data only."""
import asyncio
from unittest.mock import patch

import pytest
from ishti import crm_contract
from interface.web_ui import server


@pytest.fixture(autouse=True)
def scratch_crm(tmp_path, monkeypatch):
    monkeypatch.setattr(crm_contract, "CRM_STORE_PATH", str(tmp_path / "crm.json"))


class Body:
    def __init__(self, value):
        self.value = value

    async def json(self):
        return self.value


def test_dashboard_maps_both_business_profile_sources():
    record = crm_contract.create_client("Ayesha", company="Studio")
    with patch("ishti.skills.clients.skill.list_profiles", return_value=[
        {"id": "sqlite:7", "name": "Bilal", "read_only": True, "status": "active"}
    ]):
        result = asyncio.run(server.crm_clients())
    assert result["count"] == 2
    by_id = {item["id"]: item for item in result["clients"]}
    assert by_id[record["client_id"]]["name"] == "Ayesha"
    assert by_id["sqlite:7"]["read_only"] is True


def test_delete_requires_exact_id_and_confirmed_name():
    record = crm_contract.create_client("Ayesha")
    wrong = asyncio.run(server.crm_delete(Body({"client_id": record["client_id"], "confirm_name": "Bilal"})))
    assert wrong.status_code == 409
    assert crm_contract.get_client(record["client_id"]) is not None
    vague = asyncio.run(server.crm_delete(Body({"client_id": "Ayesha", "confirm_name": "Ayesha"})))
    assert vague.status_code == 400
    approved = asyncio.run(server.crm_delete(Body({"client_id": record["client_id"], "confirm_name": "Ayesha"})))
    assert approved == {"ok": True}
    assert crm_contract.get_client(record["client_id"]) is None
    blocked = asyncio.run(server.crm_delete(Body({"purge_all": True})))
    assert blocked.status_code == 501
