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
import shutil
import threading
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
        self._write_lock = threading.RLock()
        os.makedirs(self.sessions_dir, exist_ok=True)
        self.active_session_id: Optional[str] = None
        try:
            self.purge_trash()  # an undo window that was interrupted (crash/close) must not leak folders
        except Exception as _exc:
            swallowed('orchestrator/memory/conversation.py:ConversationManager.__init__', _exc)
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
        with self._write_lock:
            session = self.load_session(session_id)
            if not session:
                session = self.get_or_create_session(session_id)

            msg = {
            "id": f"msg_{time.strftime('%H%M%S')}_{uuid.uuid4().hex[:12]}",
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

    def feedback_target(self, session_id: str, message_id: str) -> Optional[Dict[str, Any]]:
        """Return only an assistant message and its preceding owner request in this session."""
        if not re.fullmatch(r"sess_[A-Za-z0-9_]+", str(session_id)):
            return None
        with self._write_lock:
            messages = self.get_messages(session_id)
            for index, item in enumerate(messages):
                if item.get("id") == message_id and item.get("role") == "ishti":
                    request = (item.get("metadata") or {}).get("request") or next((
                        row.get("content", "") for row in reversed(messages[:index])
                        if row.get("role") == "user"), "")
                    if request:
                        return {"message": item, "request": request}
        return None

    def save_feedback(self, session_id: str, message_id: str, rating: str, *, reason: str = "",
                      diagnosis: str = "", revision: str = "") -> Dict[str, Any]:
        """One atomic session update keeps the rating and optional answer revision together."""
        if rating not in {"positive", "negative"} or not re.fullmatch(r"sess_[A-Za-z0-9_]+", str(session_id)):
            raise ValueError("Invalid feedback target")
        with self._write_lock:
            session = self.load_session(session_id)
            if not session:
                raise ValueError("Chat not found")
            messages = session.get("messages", [])
            index = next((i for i, row in enumerate(messages) if row.get("id") == message_id and row.get("role") == "ishti"), None)
            if index is None or not any(row.get("role") == "user" for row in messages[:index]):
                raise ValueError("Answer not found in this chat")
            target = messages[index]
            original_request = (target.get("metadata") or {}).get("request") or next((
                row.get("content", "") for row in reversed(messages[:index]) if row.get("role") == "user"), "")
            current = (target.get("metadata") or {}).get("feedback") or {}
            if current.get("rating") == rating and (rating == "positive" or current.get("revision_id")):
                existing = next((row for row in messages if row.get("id") == current.get("revision_id")), None)
                return {"target": target, "revision": existing, "duplicate": True}
            successor = None
            if rating == "negative" and revision.strip():
                successor = {"id": f"msg_{time.strftime('%H%M%S')}_{uuid.uuid4().hex[:12]}",
                    "role": "ishti", "content": revision.strip()[:12000],
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "sources": json.loads(json.dumps(target.get("sources") or [])),
                    "metadata": {"revision_of": message_id, "diagnosis": diagnosis[:500], "request": original_request}}
                messages.append(successor)
            meta = target.setdefault("metadata", {})
            meta["feedback"] = {"rating": rating, "reason": reason[:1000], "diagnosis": diagnosis[:500],
                                "revision_id": successor["id"] if successor else None}
            session["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._save_session(session_id, session)
            return {"target": target, "revision": successor, "duplicate": False}

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

        seen_ids = set()
        for fname in os.listdir(self.sessions_dir):
            if fname.endswith(".json") and fname.startswith("sess_") and not fname.startswith("sess_test"):
                file_sid = fname[:-5]
                fpath = os.path.join(self.sessions_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        sid = file_sid or data.get("session_id")
                        if sid in seen_ids:
                            continue
                        seen_ids.add(sid)
                        sessions.append({
                            "session_id": sid,
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

    def rename_session(self, session_id: str, title: str) -> Optional[str]:
        """Set a chat's title (one line, max 80 chars). Returns the saved title, or None if the chat is gone."""
        session = self.load_session(session_id)
        clean = re.sub(r"\s+", " ", str(title or "")).strip()[:80]
        if not session or not clean or "/" in session_id or "\\" in session_id:
            return None
        session["title"] = clean
        self._save_session(session_id, session)
        return clean

    def trash(self, ids) -> str:
        """Move chats out of the list into data/sessions/.trash/<token>/ so undo can put them back (8 s toast).
        Returns the token restore() needs. Never touches a chat that is not there."""
        token = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        folder = os.path.join(self.sessions_dir, ".trash", token)
        os.makedirs(folder, exist_ok=True)
        want = {str(i) for i in (ids or []) if str(i).startswith("sess_") and "/" not in str(i) and "\\" not in str(i)}
        for sid in want:
            src = os.path.join(self.sessions_dir, f"{sid}.json")
            if os.path.isfile(src):
                try:
                    os.replace(src, os.path.join(folder, f"{sid}.json"))
                except OSError as _exc:
                    swallowed('orchestrator/memory/conversation.py:ConversationManager.trash', _exc)
        if self.active_session_id in want:
            self._ensure_initial_session()
        return token

    def restore(self, token) -> int:
        """Put the chats from one trash folder back; returns how many came back."""
        folder = os.path.join(self.sessions_dir, ".trash", os.path.basename(str(token or "")))
        back = 0
        if os.path.isdir(folder):
            for name in os.listdir(folder):
                if name.endswith(".json"):
                    try:
                        os.replace(os.path.join(folder, name), os.path.join(self.sessions_dir, name))
                        back += 1
                    except OSError as _exc:
                        swallowed('orchestrator/memory/conversation.py:ConversationManager.restore', _exc)
            try:
                os.rmdir(folder)
            except OSError as _exc:  # a file the owner dropped in there: leave the folder
                swallowed('orchestrator/memory/conversation.py:ConversationManager.restore.rmdir', _exc)
        return back

    def purge_trash(self, max_age_s: int = 3600) -> int:
        """Drop trash folders left behind by a closed undo window. Startup hygiene; never a live chat."""
        base = os.path.join(self.sessions_dir, ".trash")
        gone = 0
        if os.path.isdir(base):
            for name in os.listdir(base):
                path = os.path.join(base, name)
                try:
                    if os.path.isdir(path) and time.time() - os.path.getmtime(path) > max_age_s:
                        shutil.rmtree(path)
                        gone += 1
                except OSError as _exc:
                    swallowed('orchestrator/memory/conversation.py:ConversationManager.purge_trash', _exc)
        return gone

    def export_markdown(self, ids) -> str:
        """One readable Markdown document for the chosen chats (newest first): title, date, every message and source."""
        docs = []
        for sid in ids or []:
            s = self.load_session(sid)
            if not s:
                continue
            lines = [f"## {s.get('title') or sid}", "",
                     f"*{s.get('created_at', '')} · {len(s.get('messages', []))} messages*", ""]
            for m in s.get("messages", []):
                who = "**You**" if m.get("role") == "user" else "**ISHTI**"
                lines += [f"{who} · {m.get('timestamp', '')}", "", str(m.get("content", "")).strip(), ""]
                for src in m.get("sources") or []:
                    if isinstance(src, dict) and src.get("url"):
                        lines.append(f"- [{src.get('title') or src['url']}]({src['url']})")
                if any(isinstance(src, dict) and src.get("url") for src in (m.get("sources") or [])):
                    lines.append("")
            docs.append("\n".join(lines))
        when = time.strftime("%Y-%m-%d %H:%M")
        return (f"# ISHTI chats\n\nExported {when} · {len(docs)} chat(s) · made on this PC, nothing was sent\n\n"
                + "\n---\n\n".join(docs))

    def _save_session(self, session_id: str, data: Dict[str, Any]) -> None:
        fpath = os.path.join(self.sessions_dir, f"{session_id}.json")
        tmp = fpath + "." + uuid.uuid4().hex + ".tmp"
        try:
            with open(tmp, "x", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, fpath)
        finally:
            try:
                os.remove(tmp)
            except FileNotFoundError:
                pass


_conversation_manager_instance: Optional[ConversationManager] = None

def get_conversation_manager() -> ConversationManager:
    global _conversation_manager_instance
    if _conversation_manager_instance is None:
        _conversation_manager_instance = ConversationManager()
    return _conversation_manager_instance
