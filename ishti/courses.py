"""Course, teacher, and class memory store.

Tracks academic courses, instructors, and class details with timestamps and provenance.
Keeps academic records separate from the family relationship graph.
"""
from __future__ import annotations

from ishti.core.logutil import swallowed
import os
import re
import sqlite3
import threading
import time
from typing import Dict, List, Optional

from ishti import ROOT
from ishti.test_mode import scratch_path

DB_PATH = scratch_path(os.path.join(ROOT, "data", "courses.db"))
LOCK = threading.RLock()
_conn: Optional[sqlite3.Connection] = None


def conn() -> sqlite3.Connection:
    global _conn
    with LOCK:
        if _conn is None:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("""
                CREATE TABLE IF NOT EXISTS courses(
                    id INTEGER PRIMARY KEY,
                    course_name TEXT UNIQUE COLLATE NOCASE,
                    teacher_name TEXT NOT NULL,
                    provenance TEXT DEFAULT '',
                    created_at REAL,
                    updated_at REAL
                )
            """)
            _conn.commit()
        return _conn


def norm_course(name: str) -> str:
    """Normalize course name for indexing."""
    t = (name or "").strip()
    t = re.sub(r"^(?:my\s+)?(?:course|class|subject)\s+(?:for|in|of)\s+", "", t, flags=re.I)
    t = re.sub(r"\s+(?:course|class|subject)$", "", t, flags=re.I)
    return t.strip()


def set_course_teacher(course: str, teacher: str, provenance: str = "owner") -> Dict[str, str]:
    """Store or update a course and its teacher."""
    c_clean = norm_course(course)
    t_clean = (teacher or "").strip()
    if not c_clean or not t_clean:
        return {}

    now = time.time()
    c = conn()
    with LOCK, c:
        existing = c.execute("SELECT * FROM courses WHERE course_name = ?", (c_clean,)).fetchone()
        prev_teacher = existing["teacher_name"] if existing else None
        if existing:
            c.execute(
                "UPDATE courses SET teacher_name=?, provenance=?, updated_at=? WHERE course_name=?",
                (t_clean, provenance, now, c_clean),
            )
        else:
            c.execute(
                "INSERT INTO courses (course_name, teacher_name, provenance, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (c_clean, t_clean, provenance, now, now),
            )

    # Also record in exact facts store for unified retrieval
    try:
        from ishti.facts import get_facts
        get_facts().set(f"course:{c_clean.lower()}", "teacher", t_clean)
    except Exception as _exc:
        swallowed('ishti/courses.py' + ':80', _exc)

    return {
        "course": c_clean,
        "teacher": t_clean,
        "previous_teacher": prev_teacher,
        "updated": bool(existing),
    }


def get_teacher_for_course(course: str) -> Optional[str]:
    """Find the teacher for a given course name."""
    c_clean = norm_course(course)
    if not c_clean:
        return None
    c = conn()
    with LOCK:
        # 1. Exact match
        row = c.execute("SELECT teacher_name FROM courses WHERE course_name = ?", (c_clean,)).fetchone()
        if row:
            return row["teacher_name"]
        # 2. Substring / LIKE match
        row = c.execute("SELECT teacher_name FROM courses WHERE course_name LIKE ? OR ? LIKE '%' || course_name || '%'",
                        (f"%{c_clean}%", c_clean)).fetchone()
        if row:
            return row["teacher_name"]
    return None


def list_courses() -> List[Dict[str, str]]:
    """List all courses and their instructors."""
    c = conn()
    with LOCK:
        rows = c.execute("SELECT course_name, teacher_name, provenance, updated_at FROM courses ORDER BY course_name").fetchall()
        return [{"course": r["course_name"], "teacher": r["teacher_name"], "provenance": r["provenance"]} for r in rows]


def find_course_query(text: str) -> Optional[Dict[str, str]]:
    """Detect if text is a course/teacher declaration or query.
    
    Examples:
      - "my teacher for Prog for AI is M Khan Afridi"
      - "prog for AI names and the teacher name is M khan afridi"
      - "who teaches Prog for AI"
      - "who is my teacher" / "list my teachers and classes"
      - "update teacher of Prog for AI to Dr Tariq"
    """
    t = (text or "").strip()

    # 1. Store/Declaration: "my teacher for X is Y" or "teacher of X is Y" or "X ... teacher name is Y"
    m_decl = re.search(
        r"(?:(?:my|the)\s+)?teacher\s+(?:for|of|in)\s+(?P<c1>[\w\s.-]+?)\s+is\s+(?P<t1>[A-Za-z][\w\s.-]+)"
        r"|(?P<c2>[\w\s.-]+?)\s+(?:names?\s+and\s+)?(?:the\s+)?teacher(?:\s+name)?\s+is\s+(?P<t2>[A-Za-z][\w\s.-]+)"
        r"|(?:update|change)\s+(?:the\s+)?teacher\s+of\s+(?P<c3>[\w\s.-]+?)\s+to\s+(?P<t3>[A-Za-z][\w\s.-]+)",
        t,
        re.I,
    )
    if m_decl:
        course = (m_decl.group("c1") or m_decl.group("c2") or m_decl.group("c3") or "").strip()
        teacher = (m_decl.group("t1") or m_decl.group("t2") or m_decl.group("t3") or "").strip()
        # Clean filler words
        course = re.sub(r"\bnames?\b", "", course, flags=re.I).strip(" ,.-")
        if course and teacher and len(course) >= 2 and len(teacher) >= 2:
            action = "update" if m_decl.group("c3") else "set"
            return {"action": action, "course": course, "teacher": teacher}

    # 2. Query who teaches X: "who teaches X" or "who is the teacher of X" or "who is my teacher for X"
    m_who = re.search(
        r"\b(?:who\s+(?:teaches|is\s+teaching|takes)|who(?:'s|\s+is)\s+(?:the|my)?\s+teacher\s+(?:for|of|in))\s+(?P<c>[\w\s.-]+)\??$",
        t,
        re.I,
    )
    if m_who:
        course = m_who.group("c").strip(" ?.,")
        return {"action": "get", "course": course}

    # 3. List all: "who is my teacher", "who are my teachers", "list my teachers", "my teachers and classes"
    if re.search(r"\b(?:who\s+(?:is|are)\s+my\s+teachers?|list\s+(?:all\s+)?(?:my\s+)?(?:teachers|classes|courses)|show\s+(?:my\s+)?teachers|my\s+teachers\s+and\s+classes)\b", t, re.I):
        return {"action": "list"}

    return None
