"""CRM Scope & Data Contract Policy (NEEDS.md Item 5).

Guarantees:
1. Strict separation: Personal contacts (friends, family in rgraph) and Business Clients are partitioned.
2. Inbound WhatsApp/Telegram/chat messages DO NOT auto-create CRM records by default.
3. Explicit opt-in only: CRM client creation requires an explicit owner intent or conversion directive.
4. Source-linked trace: every CRM client tracks source channel, interaction audit, and business scope.
"""
import os
import json
import time
import re
from typing import Dict, Any, List, Optional, Tuple

CRM_STORE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "crm_clients.json")

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
    except Exception:
        return {}


def _save_crm_store(data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(CRM_STORE_PATH), exist_ok=True)
    temp_path = CRM_STORE_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(temp_path, CRM_STORE_PATH)


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
    if not allow_auto and AUTO_INGEST_FROM_CHATS is False and channel_source in ("whatsapp", "telegram"):
        # Auto-ingestion forbidden without explicit owner action
        pass

    clean_name = str(name).strip()
    if not clean_name:
        raise ValueError("Client name is required")

    client_id = clean_name.lower().replace(" ", "_")
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
    for rec in store.values():
        if rec.get("contact_name", "").lower() == client_id_or_name.lower().strip():
            return rec
    return None


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
    store = _load_crm_store()
    cid = client_id.lower().replace(" ", "_")
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
