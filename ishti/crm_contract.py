"""CRM Scope & Data Contract Policy.

Guarantees:
1. Strict separation: Personal contacts (friends, family in rgraph) and Business Clients are partitioned.
2. Inbound WhatsApp/Telegram/chat messages DO NOT auto-create CRM records by default.
3. Explicit opt-in only: CRM client creation requires an explicit owner intent or conversion directive.
4. Source-linked trace: every CRM client tracks source channel, interaction audit, and business scope.
"""
import os
import csv
import json
import time
import re
import threading
import uuid
import hashlib
import secrets
from typing import Dict, Any, List, Optional, Tuple
from ishti.test_mode import scratch_path

CRM_STORE_PATH = scratch_path(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "crm_clients.json"))
_STORE_LOCK = threading.RLock()
_DELETE_APPROVALS = {}
DELETE_APPROVAL_TTL = 120


def _record_digest(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def prepare_delete(client_id):
    with _STORE_LOCK:
        record = _load_crm_store().get(client_id)
        if not record or client_id != record.get("client_id"):
            raise ValueError("An exact contract CRM record ID is required")
        now = time.monotonic()
        for key, value in list(_DELETE_APPROVALS.items()):
            if value[2] <= now:
                _DELETE_APPROVALS.pop(key)
        if len(_DELETE_APPROVALS) >= 128:
            raise RuntimeError("Too many pending deletion approvals")
        token = secrets.token_urlsafe(32)
        _DELETE_APPROVALS[token] = (client_id, _record_digest(record), now + DELETE_APPROVAL_TTL)
        return {"approval_token": token, "client_id": client_id,
                "name": record["contact_name"], "expires_in": DELETE_APPROVAL_TTL}


def delete_approved(client_id, confirm_name, approval_token):
    with _STORE_LOCK:
        approval = _DELETE_APPROVALS.pop(str(approval_token or ""), None)
        if not approval or approval[0] != client_id or approval[2] <= time.monotonic():
            raise PermissionError("Deletion approval is missing, expired or already used")
        store = _load_crm_store()
        record = store.get(client_id)
        if not record or record.get("contact_name") != confirm_name or _record_digest(record) != approval[1]:
            raise PermissionError("Client changed; review a new deletion approval")
        store.pop(client_id)
        _save_crm_store(store)
        return True

# Default boundary: Personal contacts and business CRM are kept isolated
SEPARATE_PERSONAL_AND_BUSINESS = True
AUTO_INGEST_FROM_CHATS = False

CONVERSION_PATTERNS = [
    r"\badd\s+(?:him|her|them|\w+)\s+as\s+(?:a\s+)?(?:client|customer|lead)\b",
    r"\bcreate\s+(?:a\s+)?crm\s+(?:record|entry|client|lead)\b",
    r"\bconvert\s+(?:(?:this\s+)?(?:lead|contact|chat|person|\w+)\s+)?(?:to|into)\s+(?:a\s+)?(?:client|customer|crm)\b",
    r"\bmark\s+(?:as\s+)?(?:a\s+)?(?:client|business\s+contact)\b",
    r"\bregister\s+(?:new\s+)?client\b",
]
CONVERSION_RE = re.compile("|".join(f"(?:{p})" for p in CONVERSION_PATTERNS), re.I)


def is_crm_conversion_command(text: str) -> bool:
    """True only when the user explicitly commands a contact to be added to business CRM."""
    return bool(CONVERSION_RE.search(text or ""))


def _load_crm_store() -> Dict[str, Any]:
    if not os.path.exists(CRM_STORE_PATH):
        return {}
    try:
        with open(CRM_STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        raise RuntimeError("CRM store could not be read; existing records were preserved") from exc


def _save_crm_store(data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(CRM_STORE_PATH), exist_ok=True)
    temp_path = CRM_STORE_PATH + "." + uuid.uuid4().hex + ".tmp"
    try:
        with open(temp_path, "x", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, CRM_STORE_PATH)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def create_client(
    name: str,
    company: str = "",
    channel_source: str = "direct",
    phone: str = "",
    email: str = "",
    status: str = "lead",
    financial_terms: str = "",
    notes: str = "",
    tags: Optional[List[str]] = None,
    allow_auto: bool = False
) -> Dict[str, Any]:
    """Creates a business CRM record adhering to the data contract.

    Rejects automatic creation unless allow_auto is explicitly True or explicit command is given.
    """
    if not allow_auto and not AUTO_INGEST_FROM_CHATS and channel_source.strip().lower() in ("whatsapp", "telegram"):
        raise PermissionError("Inbound chat contacts cannot be added to business CRM without an explicit owner action")

    clean_name = str(name).strip()
    if not clean_name:
        raise ValueError("Client name is required")

    client_id = "crm_" + uuid.uuid4().hex
    now = time.time()

    record = {
        "client_id": client_id,
        "contact_name": clean_name.title(),
        "company": company.strip(),
        "channel_source": channel_source.strip().lower(),
        "phone": phone.strip(),
        "email": email.strip(),
        "status": status.strip().lower(),
        "scope": "business_crm",
        "financial_terms": financial_terms.strip(),
        "notes": notes.strip(),
        "tags": tags or ["business"],
        "created_at": now,
        "updated_at": now,
    }

    with _STORE_LOCK:
        store = _load_crm_store()
        store[client_id] = record
        _save_crm_store(store)
    return record


def get_client(client_id_or_name: str) -> Optional[Dict[str, Any]]:
    """Retrieves a client by id or name."""
    store = _load_crm_store()
    cid = client_id_or_name.lower().replace(" ", "_")
    if cid in store:
        return store[cid]
    asked_name = client_id_or_name.lower().strip().replace("_", " ")
    matches = [rec for rec in store.values()
               if rec.get("contact_name", "").lower() == asked_name]
    return matches[0] if len(matches) == 1 else None


def list_clients(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lists all registered CRM clients, optionally filtered by status."""
    store = _load_crm_store()
    clients = list(store.values())
    if status:
        stat = status.lower().strip()
        clients = [c for c in clients if c.get("status") == stat]
    return sorted(clients, key=lambda c: -c.get("updated_at", 0))


def delete_client(client_id: str) -> bool:
    """Removes a CRM client record."""
    with _STORE_LOCK:
        store = _load_crm_store()
        cid = client_id.lower().replace(" ", "_")
        if cid not in store:
            matches = [key for key, rec in store.items()
                       if rec.get("contact_name", "").lower().replace(" ", "_") == cid]
            cid = matches[0] if len(matches) == 1 else ""
        if cid in store:
            store.pop(cid)
            _save_crm_store(store)
            return True
        return False


def can_ingest_from_message(text: str, sender_name: str) -> Tuple[bool, str]:
    """Evaluates whether an inbound message should trigger CRM client creation.

    By default returns (False, reason) unless explicit conversion directive was given.
    """
    if not AUTO_INGEST_FROM_CHATS and not is_crm_conversion_command(text):
        return False, "CRM auto-ingestion from chat is disabled (Personal/Business separation policy)"
    return True, "Explicit conversion directive detected"


def purge_all_crm_data() -> int:
    """Removes all CRM client records permanently."""
    with _STORE_LOCK:
        store = _load_crm_store()
        count = len(store)
        _save_crm_store({})
        return count


def export_crm(fmt: str = "json") -> str:
    """Exports all CRM records to a local file."""
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    export_dir = os.path.join(root_dir, "data", "exports")
    os.makedirs(export_dir, exist_ok=True)
    store = _load_crm_store()
    clients = list(store.values())
    if str(fmt).lower() == "csv":
        out_path = os.path.join(export_dir, f"crm_export_{int(time.time())}.csv")
        if clients:
            keys = list(clients[0].keys())
            with open(out_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(clients)
        else:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("client_id,contact_name,company,status,financial_terms,created_at\n")
        return out_path
    out_path = os.path.join(export_dir, f"crm_export_{int(time.time())}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(clients, f, indent=2)
    return out_path
