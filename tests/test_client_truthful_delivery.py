"""Synthetic CRM delivery and provenance regressions; no owner stores or transports."""
import pytest

from ishti import crm_contract, rgraph
from ishti.facts import Facts
from ishti.skills.clients import skill


@pytest.fixture(autouse=True)
def scratch_clients(tmp_path, monkeypatch):
    facts = Facts(str(tmp_path / "facts.sqlite"))
    monkeypatch.setattr(skill, "get_facts", lambda: facts)
    monkeypatch.setattr(crm_contract, "CRM_STORE_PATH", str(tmp_path / "crm.json"))
    monkeypatch.setattr(rgraph, "edges_of", lambda *a, **k: [])
    yield facts
    facts.db.close()


def test_bound_draft_never_fakes_delivery_or_event(scratch_clients):
    client = skill._client("Ayesha", create=True)
    session = {"draft": "Ayesha-only reply", "draft_for": client["name"], "draft_client_id": client["id"]}
    result = skill.op_send("send draft to Ayesha", session)
    assert result["delivery"] == "not_attempted"
    assert "Nothing was sent" in result["reply"]
    assert "pending" not in session
    assert scratch_clients.db.execute("SELECT COUNT(*) FROM client_events WHERE kind='sent'").fetchone()[0] == 0


def test_unknown_recipient_does_not_create_profile():
    result = skill.op_send("send draft to Unknown", {"draft": "private text", "draft_for": "ayesha"})
    assert "no profile was created" in result["reply"]
    assert skill.known_clients() == []


def test_other_client_cannot_receive_bound_draft():
    first = skill._client("Ayesha", create=True)
    skill._client("Bilal", create=True)
    result = skill.op_send("send draft to Bilal", {
        "draft": "Ayesha private reply", "draft_for": first["name"], "draft_client_id": first["id"]})
    assert "different client" in result["reply"]
    assert "Ayesha private reply" not in result["reply"]


def test_draft_persists_exact_profile_binding(monkeypatch):
    from ishti import llm
    client = skill._client("Ayesha Khan", create=True)
    monkeypatch.setattr(llm, "ask", lambda *a, **k: "Drafted reply")
    session = {}
    result = skill.op_draft("draft a reply to Ayesha", session)
    assert result["draft_for"] == "ayesha khan"
    assert session["draft_client_id"] == client["id"]
    assert session["draft"] == "Drafted reply"


def test_roster_reads_explicit_clients_even_without_sqlite_rows(monkeypatch):
    monkeypatch.setattr(rgraph, "edges_of", lambda *a, **k: [
        {"s_name": "me", "d_name": "Sana", "rel": "client", "source_type": "explicit_user"},
        {"s_name": "me", "d_name": "Inferred", "rel": "client", "source_type": "observed"},
        {"s_name": "me", "d_name": "Brother", "rel": "brother", "source_type": "explicit_user"},
        {"s_name": "Someone Else", "d_name": "Unrelated", "rel": "client", "source_type": "explicit_user"},
    ])
    reply = skill.op_client_roster("who are my clients", {})["reply"]
    assert "Sana" in reply and "explicit owner client relationship" in reply
    assert "JQ AI Solutions" not in reply
    assert all(name not in reply for name in ("Inferred", "Brother", "Unrelated"))


def test_same_name_contract_record_never_inherits_other_profile_invoice(scratch_clients):
    client = skill._client("Ayesha", create=True)
    with scratch_clients.db:
        scratch_clients.db.execute(
            "INSERT INTO client_invoices(client_id,label,amount,state,created) VALUES(?,?,?,?,?)",
            (client["id"], "PRIVATE SQLITE PROJECT", 50, "due", 1))
    record = crm_contract.create_client("Ayesha", company="Separate Studio")
    reply = skill.op_client_roster("who are my clients", {})["reply"]
    contract_line = next(line for line in reply.splitlines() if record["client_id"] in line)
    assert "Separate Studio" in contract_line
    assert "PRIVATE SQLITE PROJECT" not in contract_line


def test_invoice_send_has_no_fabricated_destination_or_approval():
    session = {}
    result = skill.op_send_invoice_gate("send invoice to client", session)
    assert result["delivery"] == "not_attempted"
    assert "pending" not in session
    assert "billing@jqai.com" not in result["reply"]


@pytest.mark.parametrize("operation,text", [
    (skill.op_due_work, "what client work is due this week"),
    (skill.op_invoice_draft_custom, "create invoice draft for Ayesha work this month"),
    (skill.op_proj_status, "what is the status of Kestrel deployment"),
    (skill.op_followup_cond, "remind me to follow up with this client if they haven't replied in 3 days"),
    (skill.op_active_summary, "summarize active client projects"),
])
def test_empty_store_never_invents_projects_or_completed_actions(operation, text):
    reply = operation(text, {})["reply"]
    assert "JQ AI Solutions" not in reply
    assert "Deployment is active" not in reply
    assert "Scheduled conditional reminder" not in reply
    assert "October 7" not in reply
    assert "$1,250" not in reply


def test_contract_context_uses_exact_record_without_merging_same_name():
    first = crm_contract.create_client("Ayesha", company="First Studio")
    second = crm_contract.create_client("Ayesha", company="Second Studio")
    assert skill.context_load("Ayesha") is None
    context = skill.grounding_for(first["client_id"])
    assert "First Studio" in context and "Second Studio" not in context
    assert first["client_id"] in context and second["client_id"] not in context
    result = skill.op_context("client context " + first["client_id"], {})
    assert "First Studio" in result["reply"]


def test_active_summary_and_status_use_only_public_recorded_project_events():
    client = skill._client("Ayesha", create=True)
    skill._event(client["id"], "project", "Widget: design review pending")
    skill._event(client["id"], "project", "SECRET PROJECT", private=1)
    summary = skill.op_active_summary("summarize active client projects", {})["reply"]
    status = skill.op_proj_status("status of Widget deployment", {})["reply"]
    assert "design review pending" in summary and "design review pending" in status
    assert "SECRET PROJECT" not in summary
    assert "current deployment is unverified" in status


def test_invoice_draft_keeps_other_clients_amounts_isolated(scratch_clients):
    first = skill._client("Ayesha", create=True)
    second = skill._client("Bilal", create=True)
    with scratch_clients.db:
        scratch_clients.db.executemany(
            "INSERT INTO client_invoices(client_id,label,amount,state,created) VALUES(?,?,?,?,?)", [
                (first["id"], "Design", 500, "due", 1),
                (second["id"], "SECRET OTHER INVOICE", 9999, "due", 1),
            ])
    reply = skill.op_invoice_draft_custom("create invoice draft for Ayesha work this month", {})["reply"]
    assert "Design" in reply and "500.00" in reply
    assert "SECRET OTHER INVOICE" not in reply and "9,999" not in reply


def test_due_work_uses_explicit_dates_and_excludes_private_or_past_events():
    import datetime as dt
    client = skill._client("Ayesha", create=True)
    today = dt.date.today()
    skill._event(client["id"], "milestone", f"Release review due {today.isoformat()}")
    skill._event(client["id"], "milestone", f"PRIVATE DEADLINE {today.isoformat()}", private=1)
    skill._event(client["id"], "milestone", f"PAST DEADLINE {(today - dt.timedelta(days=10)).isoformat()}")
    skill._event(client["id"], "note", f"UNSTRUCTURED NOTE {today.isoformat()}")
    reply = skill.op_due_work("what work is due this week", {})["reply"]
    assert "Release review" in reply
    assert all(text not in reply for text in ("PRIVATE DEADLINE", "PAST DEADLINE", "UNSTRUCTURED NOTE"))
