"""
ISHTI Multi-Turn Conversation Memory & Session Manager
Maintains conversation dialogue history across turns, supports new chat creation,
and persists sessions to disk in the data/sessions folder
"""

import os
import sys
import time
import json
import uuid
import re
from typing import Dict, Any, List, Optional

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SESSIONS_DIR = os.path.join(ROOT_DIR, "data", "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)


class ConversationManager:
    def __init__(self, sessions_dir: str = SESSIONS_DIR):
        self.sessions_dir = sessions_dir
        os.makedirs(self.sessions_dir, exist_ok=True)
        self.active_session_id: Optional[str] = None
        self._ensure_initial_session()

    def _ensure_initial_session(self) -> str:
        sessions = self.list_sessions()
        if sessions:
            self.active_session_id = sessions[0]["session_id"]
        else:
            self.active_session_id = self.new_chat("New Session")
        return self.active_session_id

    def new_chat(self, title: str = "New Conversation") -> str:
        """Creates a fresh chat session and makes it active. An existing empty session is reused, so
        page loads and repeated "new chat" clicks don't pile up empty sessions."""
        latest = self.list_sessions()[:1]
        if latest and not self.get_messages(latest[0]["session_id"]):
            self.active_session_id = latest[0]["session_id"]
            return self.active_session_id
        session_id = f"sess_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        session_data = {
            "session_id": session_id,
            "title": title,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "messages": []
        }
        self._save_session(session_id, session_data)
        self.active_session_id = session_id
        return session_id

    def get_or_create_session(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Loads existing session or creates new if not found."""
        target_id = session_id or self.active_session_id
        if not target_id:
            target_id = self.new_chat()

        session = self.load_session(target_id)
        if not session:
            target_id = self.new_chat()
            session = self.load_session(target_id)
        self.active_session_id = target_id
        return session

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        sources: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Appends a message to the specified session and auto-titles if first user message."""
        session = self.load_session(session_id)
        if not session:
            session = self.get_or_create_session(session_id)

        msg = {
            "id": f"msg_{time.strftime('%H%M%S')}_{uuid.uuid4().hex[:4]}",
            "role": role,  # 'user' or 'ishti'
            "content": content,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sources": sources or [],
            "metadata": metadata or {},
        }
        session["messages"].append(msg)
        session["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        # Auto-update session title if it's the first user message
        if role == "user" and session.get("title") in ["New Conversation", "New Session", "Untitled"]:
            clean_title = re.sub(r"[^a-zA-Z0-9\s]+", "", content).strip()
            session["title"] = (clean_title[:38] + "...") if len(clean_title) > 38 else (clean_title or "Conversation")

        self._save_session(session_id, session)
        return msg

    def get_history_text(self, session_id: str, max_turns: int = 6) -> str:
        """
        Formats recent turns as clean conversational context for local LLM generation.
        Example:
        [Conversation History]:
        User: What is Python?
        ISHTI: Python is a high-level programming language...
        """
        session = self.load_session(session_id)
        if not session or not session.get("messages"):
            return ""

        msgs = session["messages"]
        recent = msgs[-(max_turns * 2):]  # Get last N turn pairs
        
        lines = []
        for m in recent:
            speaker = "User" if m["role"] == "user" else "ISHTI"
            clean_text = m["content"].strip().replace("\n", " ")
            lines.append(f"{speaker}: {clean_text[:350]}")

        if not lines:
            return ""
        return "\n[Recent Conversation History]:\n" + "\n".join(lines) + "\n\n"

    def get_messages(self, session_id: str) -> List[Dict[str, Any]]:
        session = self.load_session(session_id)
        return session.get("messages", []) if session else []

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Lists all stored chat sessions sorted newest updated first."""
        sessions = []
        if not os.path.exists(self.sessions_dir):
            return []

        for fname in os.listdir(self.sessions_dir):
            if fname.endswith(".json") and fname.startswith("sess_"):
                fpath = os.path.join(self.sessions_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        sessions.append({
                            "session_id": data.get("session_id", fname.replace(".json", "")),
                            "title": data.get("title", "Untitled Session"),
                            "created_at": data.get("created_at", ""),
                            "updated_at": data.get("updated_at", ""),
                            "message_count": len(data.get("messages", [])),
                            "last_message": data["messages"][-1]["content"][:80] if data.get("messages") else "",
                        })
                except Exception as _exc:
                    swallowed('orchestrator/memory/conversation.py:ConversationManager.list_sessions', _exc)

        # Sort newest updated first
        sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        return sessions

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        fpath = os.path.join(self.sessions_dir, f"{session_id}.json")
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def delete_session(self, session_id: str) -> bool:
        fpath = os.path.join(self.sessions_dir, f"{session_id}.json")
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
                if self.active_session_id == session_id:
                    self._ensure_initial_session()
                return True
            except Exception:
                return False
        return False

    def _save_session(self, session_id: str, data: Dict[str, Any]) -> None:
        fpath = os.path.join(self.sessions_dir, f"{session_id}.json")
        try:
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[ConversationManager] Error saving session {session_id}: {e}")


_conversation_manager_instance: Optional[ConversationManager] = None

def get_conversation_manager() -> ConversationManager:
    global _conversation_manager_instance
    if _conversation_manager_instance is None:
        _conversation_manager_instance = ConversationManager()
    return _conversation_manager_instance
