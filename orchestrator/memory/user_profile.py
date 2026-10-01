"""
ISHTI Persistent User Memory System
Stores and retrieves stable owner profile, preferences, projects, and context.
"""

import os
import re
import json
import shutil
import tempfile
import time
from contextlib import contextmanager
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict, field

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MEMORY_DIR = os.path.join(ROOT_DIR, "orchestrator", "memory")
USER_PROFILE_PATH = os.path.join(MEMORY_DIR, "user_profile.json")
PREFERENCES_PATH = os.path.join(MEMORY_DIR, "preferences.json")
PROJECTS_PATH = os.path.join(MEMORY_DIR, "projects.json")
CONTEXT_PATH = os.path.join(MEMORY_DIR, "important_context.json")

os.makedirs(MEMORY_DIR, exist_ok=True)


@dataclass
class UserProfile:
    name: str = ""
    relationship: str = ""
    role: str = ""
    location: Dict[str, str] = field(default_factory=dict)
    known_since: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserProfile":
        return cls(**data)


@dataclass
class Preferences:
    language: str = "en"
    timezone: str = "Asia/Karachi"
    response_length: str = "short"
    voice_enabled: bool = True
    audio_output: bool = True
    default_weather_city: str = "Peshawar"
    headless_browser_default: bool = True
    confirm_consequential: bool = True
    theme: str = "cyber"
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Preferences":
        return cls(**data)


@dataclass
class Project:
    project_id: str
    name: str
    description: str
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""
    tags: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Project":
        return cls(**data)


@dataclass
class ImportantContext:
    key: str
    value: str
    category: str
    created_at: str = ""
    expires_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ImportantContext":
        return cls(**data)


class UserMemory:
    def __init__(self):
        self._profile: Optional[UserProfile] = None
        self._preferences: Optional[Preferences] = None
        self._projects: List[Project] = []
        self._context: List[ImportantContext] = []
        self._load_all()

    def _load_all(self):
        self._load_profile()
        self._load_preferences()
        self._load_projects()
        self._load_context()

    def _load_profile(self):
        if os.path.exists(USER_PROFILE_PATH):
            try:
                with open(USER_PROFILE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._profile = UserProfile.from_dict(data)
            except Exception:
                self._profile = UserProfile()
        else:
            self._profile = UserProfile()
            self._init_default_profile()

    def _init_default_profile(self):
        self._profile = UserProfile(
            name="Ishtiaq Ahmed",
            relationship="owner",
            role="AI student",
            location={"city": "Peshawar", "country": "Pakistan"},
            known_since=time.strftime("%Y-%m-%dT%H:%M:%S"),
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        self.save_profile()

    def _load_preferences(self):
        if os.path.exists(PREFERENCES_PATH):
            try:
                with open(PREFERENCES_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._preferences = Preferences.from_dict(data)
            except Exception:
                self._preferences = Preferences()
        else:
            self._preferences = Preferences(
                timezone="Asia/Karachi",
                updated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            )
            self.save_preferences()

    def _load_projects(self):
        if os.path.exists(PROJECTS_PATH):
            try:
                with open(PROJECTS_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._projects = [Project.from_dict(p) for p in data]
            except Exception:
                self._projects = []
        else:
            self._projects = []

    def _load_context(self):
        if os.path.exists(CONTEXT_PATH):
            try:
                with open(CONTEXT_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._context = [ImportantContext.from_dict(c) for c in data]
            except Exception:
                self._context = []
        else:
            self._context = []

    def _save_json(self, path: str, data: Any):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[UserMemory] Failed to save {path}: {e}")

    # Profile methods
    def get_profile(self) -> UserProfile:
        return self._profile

    def update_profile(self, **kwargs) -> UserProfile:
        for key, value in kwargs.items():
            if hasattr(self._profile, key):
                setattr(self._profile, key, value)
        self._profile.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.save_profile()
        return self._profile

    def save_profile(self):
        self._save_json(USER_PROFILE_PATH, self._profile.to_dict())

    # Preferences methods
    def get_preferences(self) -> Preferences:
        return self._preferences

    def update_preferences(self, **kwargs) -> Preferences:
        for key, value in kwargs.items():
            if hasattr(self._preferences, key):
                setattr(self._preferences, key, value)
        self._preferences.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.save_preferences()
        return self._preferences

    def save_preferences(self):
        self._save_json(PREFERENCES_PATH, self._preferences.to_dict())

    # Projects methods
    def get_projects(self, status: str = None) -> List[Project]:
        if status:
            return [p for p in self._projects if p.status == status]
        return self._projects

    def get_project(self, project_id: str) -> Optional[Project]:
        for p in self._projects:
            if p.project_id == project_id:
                return p
        return None

    def add_project(self, name: str, description: str, tags: List[str] = None, context: Dict = None) -> Project:
        for existing in self._projects:
            if existing.name.lower() == name.lower():
                return existing  # dedupe: never store the same project twice
        project_id = f"proj_{int(time.time())}_{len(self._projects)}"
        project = Project(
            project_id=project_id,
            name=name,
            description=description,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            tags=tags or [],
            context=context or {},
        )
        self._projects.append(project)
        self.save_projects()
        return project

    def update_project(self, project_id: str, **kwargs) -> Optional[Project]:
        for p in self._projects:
            if p.project_id == project_id:
                for key, value in kwargs.items():
                    if hasattr(p, key):
                        setattr(p, key, value)
                p.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
                self.save_projects()
                return p
        return None

    def save_projects(self):
        self._save_json(PROJECTS_PATH, [p.to_dict() for p in self._projects])

    # Context methods
    def get_context(self, category: str = None) -> List[ImportantContext]:
        now = time.time()
        valid = []
        for c in self._context:
            if c.expires_at:
                try:
                    exp = time.mktime(time.strptime(c.expires_at, "%Y-%m-%dT%H:%M:%S"))
                    if exp < now:
                        continue
                except Exception:
                    pass
            if category is None or c.category == category:
                valid.append(c)
        return valid

    def add_context(self, key: str, value: str, category: str = "general", expires_at: str = "") -> ImportantContext:
        ctx = ImportantContext(
            key=key,
            value=value,
            category=category,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            expires_at=expires_at,
        )
        self._context = [c for c in self._context if c.key != key and c.value.strip().lower() != value.strip().lower()]
        self._context.append(ctx)
        self.save_context()
        return ctx

    def remove_context(self, key: str) -> bool:
        before = len(self._context)
        self._context = [c for c in self._context if c.key != key]
        if len(self._context) < before:
            self.save_context()
            return True
        return False

    def save_context(self):
        self._save_json(CONTEXT_PATH, [c.to_dict() for c in self._context])

    # Query methods for LLM context injection
    def get_relevant_memory(self, query: str, max_items: int = 5) -> Dict[str, Any]:
        """Returns relevant memory for a given query."""
        q_lower = query.lower()
        result = {
            "profile": self._profile.to_dict() if self._profile else {},
            "preferences": self._preferences.to_dict() if self._preferences else {},
            "relevant_projects": [],
            "relevant_context": [],
        }

        # Find relevant projects
        for p in self._projects:
            if p.status != "active":
                continue
            if any(tag.lower() in q_lower for tag in p.tags) or \
               p.name.lower() in q_lower or \
               any(word in q_lower for word in p.description.lower().split() if len(word) > 4):
                result["relevant_projects"].append(p.to_dict())

        # Find relevant context by shared content words (was: whole-value containment, which never matched)
        q_words = {w for w in re.findall(r"[a-z0-9']+", q_lower) if len(w) > 3}
        for c in self._context:
            c_words = {w for w in re.findall(r"[a-z0-9']+", c.value.lower()) if len(w) > 3}
            if c.key.lower() in q_lower or (q_words & c_words):
                result["relevant_context"].append(c.to_dict())

        # Limit results
        result["relevant_projects"] = result["relevant_projects"][:max_items]
        result["relevant_context"] = result["relevant_context"][:max_items]

        return result

    def format_for_llm(self, query: str = "") -> str:
        """Formats relevant memory as text for LLM context injection."""
        mem = self.get_relevant_memory(query)
        parts = []

        if mem["profile"]:
            p = mem["profile"]
            loc = p.get("location", {}) or {}
            parts.append(
                "OWNER (established facts - never ask for these again):\n"
                f"- Name: {p.get('name', 'Unknown')}\n"
                f"- Relationship: {p.get('relationship', 'owner')} - you are his assistant\n"
                f"- Role: {p.get('role', 'Unknown')}\n"
                f"- Lives in: {loc.get('city', '')}, {loc.get('country', '')}\n"
                f"- Default city for weather and local questions: "
                f"{self._preferences.default_weather_city or loc.get('city', '')}"
            )

        if mem["relevant_projects"]:
            proj_lines = []
            for p in mem["relevant_projects"]:
                proj_lines.append(f"- {p['name']}: {p['description']} (tags: {', '.join(p['tags'])})")
            parts.append("ACTIVE PROJECTS:\n" + "\n".join(proj_lines))

        if mem["relevant_context"]:
            ctx_lines = []
            for c in mem["relevant_context"]:
                ctx_lines.append(f"- {c['key']}: {c['value']} ({c['category']})")
            parts.append("IMPORTANT CONTEXT:\n" + "\n".join(ctx_lines))

        if not parts:
            return ""

        return "\n[Persistent User Memory]:\n" + "\n\n".join(parts) + "\n\n"


_user_memory_instance: Optional[UserMemory] = None


def get_user_memory() -> UserMemory:
    global _user_memory_instance
    if _user_memory_instance is None:
        _user_memory_instance = UserMemory()
    return _user_memory_instance


@contextmanager
def scratch_memory():
    """For tests: owner-memory reads and writes go to a temp copy; the real files are never touched.
    (Tests used to overwrite the owner's real role and add junk projects/notes - AUDIT.md B7.)"""
    global USER_PROFILE_PATH, PREFERENCES_PATH, PROJECTS_PATH, CONTEXT_PATH, _user_memory_instance
    real = (USER_PROFILE_PATH, PREFERENCES_PATH, PROJECTS_PATH, CONTEXT_PATH)
    tmp = tempfile.mkdtemp(prefix="ishti_mem_")
    for p in real:
        if os.path.exists(p):
            shutil.copy(p, tmp)
    USER_PROFILE_PATH, PREFERENCES_PATH, PROJECTS_PATH, CONTEXT_PATH = (os.path.join(tmp, os.path.basename(p)) for p in real)
    _user_memory_instance = None
    import orchestrator.deep_research as research  # research runs archive reports too
    import orchestrator.memory.conversation as conversation  # ...and every request writes chat sessions
    real_research, research.RESEARCH_MEM_DIR = research.RESEARCH_MEM_DIR, tmp
    real_conv = conversation._conversation_manager_instance
    conversation._conversation_manager_instance = conversation.ConversationManager(os.path.join(tmp, "sessions"))
    try:
        yield tmp
    finally:
        USER_PROFILE_PATH, PREFERENCES_PATH, PROJECTS_PATH, CONTEXT_PATH = real
        research.RESEARCH_MEM_DIR = real_research
        conversation._conversation_manager_instance = real_conv
        _user_memory_instance = None
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    mem = get_user_memory()
    print("Profile:", mem.get_profile())
    print("Preferences:", mem.get_preferences())
    print("Projects:", [p.name for p in mem.get_projects()])
    print("Context:", mem.get_context())
    print("\nFormatted for LLM:")
    print(mem.format_for_llm("ISHTI project"))