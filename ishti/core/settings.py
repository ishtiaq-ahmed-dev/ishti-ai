"""Typed Settings system (Part C4).

One pydantic schema with validation, defaults and per-setting descriptions. Storage:

  - data/settings.json           non-secret settings (validated on load; defaults fill gaps)
  - data/settings.secrets.json   API keys etc. (best-effort user-only ACL; NEVER exported —
                                 export() writes placeholders only). The OS keychain is
                                 used instead when the `keyring` package is installed.

Every field keeps its meaning next to it (json_schema_extra description), and
SettingsService exposes describe() (schema for the UI), get/update, export/import and
reset_section(). `update()` validates first, then applies — an invalid value never lands
on disk. Fields map 1:1 onto the Settings UI sections:
  architecture, models, voice_stt, voice_tts, skills, memory, learning, agents,
  integrations, confirmation, privacy, observability.
"""
from __future__ import annotations

import json
import os
import stat
import threading
from typing import Literal

from pydantic import BaseModel, Field, field_validator

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


# --------------------------------------------------------------------- schema


class ArchitectureSettings(BaseModel):
    active_profile: str = Field("router_single_agent", description="Which architecture answers (see the Architecture Registry)")
    confirm_before_switch: bool = Field(True, description="Ask yes/no before switching by voice/text command")
    shortlist_min: int = Field(5, ge=0, le=20, description="Minimum skills shown to the LLM")
    shortlist_max: int = Field(20, ge=5, le=40, description="Maximum skills shown to the LLM (the 5-20 window)")


class ModelsSettings(BaseModel):
    backend: Literal["ollama", "llamacpp", "vllm", "openai_compat"] = Field(
        "ollama", description="Which OpenAI-compatible local backend serves the LLM")
    base_url: str = Field("http://127.0.0.1:11434/v1", description="LLM endpoint base URL")
    main_model: str = Field("qwen3:14b", description="The brain: think/plan/tool-calls/final answer")
    validation_model: str = Field("qwen3:14b", description="Judges tool results and final answers")
    fallback_model: str = Field("ishti-qwen2.5:14b", description="Used when the main model isn't pulled")
    coder_model: str = Field("qwen3-coder:30b-a3b", description="Code questions")
    fast_model: str = Field("llama3.2:3b", description="Cheap small tasks")
    context_length: int = Field(32768, ge=2048, le=131072, description="Context window (must fit in VRAM)")
    temperature: float = Field(0.3, ge=0.0, le=2.0, description="Default sampling temperature")
    top_p: float = Field(0.95, ge=0.0, le=1.0, description="Default top_p")
    thinking_mode: bool = Field(True, description="Qwen3 thinking blocks on/off")
    max_tokens: int = Field(1024, ge=64, le=32768, description="Default generation cap")


class VoiceSTTSettings(BaseModel):
    engine: Literal["faster_whisper", "text_fallback"] = Field(
        "faster_whisper", description="Local STT engine (text_fallback = no microphone)")
    whisper_model: str = Field("turbo", description="Whisper model size / path")
    device: Literal["cuda", "cpu", "auto"] = Field("cuda", description="Compute device")
    compute: Literal["float16", "int8_float16", "int8", "auto"] = Field("float16", description="Compute precision")
    language: str = Field("en", description="Primary language (en/ur/ps)")
    input_mode: Literal["push_to_talk", "vad", "wake_word"] = Field(
        "wake_word", description="How a voice turn starts")
    vad_threshold: float = Field(0.6, ge=0.0, le=1.0, description="Voice-activity threshold")
    barge_in: bool = Field(True, description="Speech interrupts the assistant")


class VoiceTTSSettings(BaseModel):
    enabled: bool = Field(True, description="Speak the answers")
    engine: Literal["kokoro", "piper", "off"] = Field("kokoro", description="Local TTS engine")
    voice: str = Field("Female 1 - Neutral", description="Voice profile name from the roster")
    speed: float = Field(1.0, ge=0.5, le=2.0, description="Speech rate multiplier")
    stream_sentences: bool = Field(True, description="Start speaking the first sentence while the rest generates")


class SkillsSettings(BaseModel):
    enabled: list[str] = Field(default_factory=list, description="Empty = all; otherwise only these run")
    disabled: list[str] = Field(default_factory=list, description="Never routed to")
    disabled_domains: list[str] = Field(default_factory=list, description="Whole domains switched off (Skill Registry)")
    terminal_allow: list[str] = Field(default_factory=lambda: ["git", "python", "pip", "pytest", "node", "npm", "dir", "ls", "echo"],
                                      description="Terminal commands the sandbox may run (Tier-2 B5)")
    terminal_deny: list[str] = Field(default_factory=lambda: ["format", "rd", "rmdir", "del", "erase", "diskpart", "reg", "shutdown", "taskkill"],
                                     description="Terminal commands always refused")
    high_risk_enabled: bool = Field(False, description="Registry/driver/account operations (B8) - disabled by default")


class MemorySettings(BaseModel):
    enabled: bool = Field(True, description="Store and recall conversation state/memory")
    state_db: str = Field("", description="Conversation-state DB path (empty = default)")
    recall_k: int = Field(6, ge=1, le=50, description="How many memory hits to inject")
    max_distance: float = Field(0.45, ge=0.0, le=2.0, description="Vector-recall distance cutoff")
    encrypt_at_rest: bool = Field(False, description="Encrypt memory contents at rest (key in the OS keychain)")
    assembly_conversation_state: int = Field(400, ge=0, le=8000, description="Context budget: conversation state (chars)")
    assembly_relevant_memories: int = Field(900, ge=0, le=16000, description="Context budget: relevant memories (chars)")
    assembly_relevant_people: int = Field(500, ge=0, le=8000, description="Context budget: relevant people (chars)")
    assembly_relevant_relationships: int = Field(500, ge=0, le=8000, description="Context budget: relationships (chars)")
    assembly_relevant_procedures: int = Field(700, ge=0, le=8000, description="Context budget: procedures (chars)")
    assembly_relevant_lessons: int = Field(600, ge=0, le=8000, description="Context budget: lessons (chars)")
    assembly_relevant_skills: int = Field(300, ge=0, le=4000, description="Context budget: relevant skills (chars)")
    assembly_current_task_state: int = Field(300, ge=0, le=4000, description="Context budget: task state (chars)")
    assembly_available_tools: int = Field(800, ge=0, le=8000, description="Context budget: available tools (chars)")
    assembly_total: int = Field(4500, ge=500, le=32000, description="Context budget: whole memory block (chars)")


class LearningSettings(BaseModel):
    enabled: bool = Field(True, description="Behavior-data collection (never changes weights at runtime)")
    proactive: bool = Field(False, description="Volunteer suggestions unprompted")


class AgentsSettings(BaseModel):
    max_subagents: int = Field(4, ge=1, le=12, description="Parallel specialist sub-agents")
    subagent_timeout_s: int = Field(300, ge=10, le=3600, description="Per-sub-agent cap")
    subagents_receive_resolved_query: bool = Field(
        True, description="Sub-agents get the resolved request + state summary (D3), never the raw message")
    pattern: Literal["supervisor", "sequential", "fan_out", "hierarchical"] = Field(
        "supervisor", description="Multi-agent execution pattern (selectable per task type)")
    max_iterations: int = Field(4, ge=1, le=10, description="Plan-act-verify-fix rounds per agent before escalating")
    max_tool_calls: int = Field(12, ge=1, le=60, description="Tool-call budget per agent run")
    max_total_tokens: int = Field(60000, ge=1000, le=1000000, description="Token budget per orchestrated task")


class IntegrationsSettings(BaseModel):
    flowise_url: str = Field("", description="Flowise API base URL (enables the flowise_flow profile)")
    flowise_api_key: str = Field("", description="Flowise API key (stored as a secret)", json_schema_extra={"secret": True})
    flowise_flows: dict[str, str] = Field(default_factory=dict, description="intent text -> Flowise flow id (which flow answers which request)")
    searxng_url: str = Field("http://127.0.0.1:8890", description="Local SearXNG search endpoint")
    hibp_api_key: str = Field("", description="HIBP breach-check key (stored as a secret)", json_schema_extra={"secret": True})


class ConfirmationSettings(BaseModel):
    require_for_verbs: list[str] = Field(
        default_factory=lambda: ["send", "delete", "remove", "pay", "buy", "order", "purchase", "post",
                                 "publish", "submit", "install", "uninstall", "shut down", "restart",
                                 "transfer", "forward", "empty", "log in", "sign in", "kill"],
        description="High-impact actions that always ask first (Rule 6)")
    confirm_t2_skills: bool = Field(True, description="Skills with T2 tier always confirm")
    repeat_guard_window_s: int = Field(20, ge=0, le=600, description="Re-ask when the same sensitive action repeats within this window")


class PrivacySettings(BaseModel):
    redact_secrets_in_logs: bool = Field(True, description="Mask secrets/PII in JSON logs")
    redact_patterns: list[str] = Field(
        default_factory=lambda: [r"[\w.+-]+@[\w-]+\.[\w.]+", r"\b(?:\+92|0)\d{9,10}\b", r"\b(?:\d[ -]?){13,19}\b"],
        description="Regexes redacted from logs (emails, phones, cards)")
    remember_chats: bool = Field(True, description="Persist chat history")
    allow_t3: dict[str, bool] = Field(
        default_factory=dict,
        description="T3 (dangerous) skills explicitly allowed: skill name -> true. Same shape the dispatcher reads "
                    "(CONFIG['allow_t3'].get(name)); an older list of names is accepted and converted")

    @field_validator("allow_t3", mode="before")
    @classmethod
    def _allow_t3_list_to_mapping(cls, v):
        """Settings saved while the schema said list[str] hold ["skill_a", ...]: keep them working as {name: True}."""
        if isinstance(v, (list, tuple, set)):
            return {str(n): True for n in v}
        return v


class ObservabilitySettings(BaseModel):
    json_logs: bool = Field(True, description="Structured JSON logs")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field("INFO", description="Log verbosity")
    trace_events: bool = Field(True, description="Emit stage trace events to the UI")
    log_file: str = Field("", description="JSON log file (empty = data/logs/ishti.jsonl)")


class Settings(BaseModel):
    architecture: ArchitectureSettings = Field(default_factory=ArchitectureSettings)
    models: ModelsSettings = Field(default_factory=ModelsSettings)
    voice_stt: VoiceSTTSettings = Field(default_factory=VoiceSTTSettings)
    voice_tts: VoiceTTSSettings = Field(default_factory=VoiceTTSSettings)
    skills: SkillsSettings = Field(default_factory=SkillsSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    learning: LearningSettings = Field(default_factory=LearningSettings)
    agents: AgentsSettings = Field(default_factory=AgentsSettings)
    integrations: IntegrationsSettings = Field(default_factory=IntegrationsSettings)
    confirmation: ConfirmationSettings = Field(default_factory=ConfirmationSettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

    model_config = {"json_schema_extra": {
        "sections": ["architecture", "models", "voice_stt", "voice_tts", "skills", "memory",
                     "learning", "agents", "integrations", "confirmation", "privacy", "observability"]}}


SECTIONS = ["architecture", "models", "voice_stt", "voice_tts", "skills", "memory",
            "learning", "agents", "integrations", "confirmation", "privacy", "observability"]


# --------------------------------------------------------------------- storage

def _data_dir() -> str:
    env = os.environ.get("ISHTI_SETTINGS_DIR", "").strip()  # tests: never touch the owner's data
    if env:
        return env
    try:
        from ishti import ROOT
        return os.path.join(ROOT, "data")
    except Exception:
        return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")


def _user_only(path: str):
    """Best-effort user-only file permissions (NTFS ACLs are handled by the OS defaults;
    POSIX chmod works when running under WSL)."""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as _exc:
        swallowed('ishti/core/settings.py:_user_only', _exc)


def _keyring_available() -> bool:
    try:
        import keyring  # noqa: F401
        return True
    except Exception:
        return False


class SettingsService:
    """Loads, validates, updates, exports, imports and resets settings. One instance,
    thread-safe; secrets never leave `secrets.json` / the keychain."""

    def __init__(self, data_dir: str | None = None):
        self.data_dir = data_dir or _data_dir()
        os.makedirs(self.data_dir, exist_ok=True)
        self.path = os.path.join(self.data_dir, "settings.json")
        self.secrets_path = os.path.join(self.data_dir, "settings.secrets.json")
        self._lock = threading.Lock()
        self.settings = self._load()

    # ---- load / save --------------------------------------------------------
    def _load(self) -> Settings:
        data: dict = {}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        secrets = self._load_secrets()
        self._merge_secrets_into(data, secrets)
        try:
            return Settings.model_validate(data)     # invalid values -> defaults + a warning
        except Exception as exc:
            print(f"[settings] invalid settings.json ({exc.__class__.__name__}) — using defaults")
            return Settings()

    def _load_secrets(self) -> dict:
        if _keyring_available():
            try:
                import keyring
                raw = keyring.get_password("ishti", "settings_secrets")
                if raw:
                    return json.loads(raw)
            except Exception:
                pass
        try:
            with open(self.secrets_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_secrets(self, secrets: dict):
        if _keyring_available():
            try:
                import keyring
                keyring.set_password("ishti", "settings_secrets", json.dumps(secrets))
                return
            except Exception as _exc:
                swallowed('ishti/core/settings.py:SettingsService._save_secrets', _exc)
        with open(self.secrets_path, "w", encoding="utf-8") as f:
            json.dump(secrets, f, indent=2, ensure_ascii=False)
        _user_only(self.secrets_path)

    def _merge_secrets_into(self, data: dict, secrets: dict):
        """Secret values live outside settings.json; merged in memory only."""
        for section, fields in secrets.items():
            if isinstance(fields, dict) and isinstance(data.get(section), dict):
                data[section].update(fields)

    def _split_secrets(self, data: dict) -> tuple[dict, dict]:
        plain, secrets = json.loads(json.dumps(data)), {}
        schema = Settings.model_json_schema()
        props = schema.get("$defs", {})
        for section, fields in data.items():
            sec_schema = props.get(SECTION_SCHEMA_NAMES.get(section, section), {})
            for k, v in (fields or {}).items():
                prop = sec_schema.get("properties", {}).get(k) or {}
                # pydantic merges json_schema_extra into the property dict itself
                extra = prop.get("json_schema_extra")
                extra = extra if isinstance(extra, dict) else prop
                if isinstance(extra, dict) and extra.get("secret") and v:
                    secrets.setdefault(section, {})[k] = v
                    plain[section].pop(k, None)
        return plain, secrets

    def _save(self):
        data = self.settings.model_dump()
        plain, secrets = self._split_secrets(data)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(plain, f, indent=2, ensure_ascii=False)
        self._save_secrets(secrets)

    # ---- API -----------------------------------------------------------------
    def get(self) -> dict:
        with self._lock:
            return self.settings.model_dump()

    def update(self, patch: dict) -> dict:
        """Validate first, then apply — invalid values raise ValueError, never land.
        (Returns the dump directly: get() would re-take the non-reentrant lock.)"""
        with self._lock:
            current = self.settings.model_dump()
            merged = _deep_merge(current, patch or {})
            try:
                new = Settings.model_validate(merged)
            except Exception as exc:
                raise ValueError(f"invalid settings: {exc}") from exc
            self.settings = new
            self._save()
            return self.settings.model_dump()

    def reset_section(self, section: str) -> dict:
        with self._lock:
            if section not in SECTIONS:
                raise ValueError(f"unknown section '{section}'")
            fresh = Settings()
            setattr(self.settings, section, getattr(fresh, section))
            self._save()
            return self.settings.model_dump()

    def reset_all(self) -> dict:
        with self._lock:
            self.settings = Settings()
            self._save()
            return self.settings.model_dump()

    def export(self) -> dict:
        """Everything EXCEPT secrets (placeholders show where they'd go)."""
        with self._lock:
            data = self.settings.model_dump()
            _, secrets = self._split_secrets(data)
            for section, fields in secrets.items():
                for k in fields:
                    if data.get(section, {}).get(k):
                        data[section][k] = "***"
            return {"version": 1, "exported_at": _now(), "settings": data}

    def import_settings(self, payload: dict, replace_secrets: bool = False) -> dict:
        with self._lock:
            data = payload.get("settings") if isinstance(payload, dict) and "settings" in payload else payload
            merged = _deep_merge(self.settings.model_dump(), data or {})
            # placeholders from a previous export must not overwrite real secrets
            for section, fields in (data or {}).items():
                if isinstance(fields, dict):
                    for k, v in fields.items():
                        if v == "***" and not replace_secrets:
                            merged.get(section, {}).pop(k, None)
            try:
                new = Settings.model_validate(merged)
            except Exception as exc:
                raise ValueError(f"invalid settings file: {exc}") from exc
            self.settings = new
            self._save()
            return self.settings.model_dump()

    # ---- schema for the UI ----------------------------------------------------
    def describe(self) -> dict:
        schema = Settings.model_json_schema()
        defs = schema.get("$defs", {})
        out = {"sections": []}
        for section in SECTIONS:
            s = defs.get(_schema_name(section))
            if not s:
                continue
            fields = []
            for name, prop in s.get("properties", {}).items():
                extra = prop.get("json_schema_extra") or {}
                fields.append({
                    "name": name,
                    "type": prop.get("type", prop.get("anyOf", "")),
                    "enum": prop.get("enum"),
                    "default": prop.get("default"),
                    "description": prop.get("description", ""),
                    "secret": bool(extra.get("secret")) if isinstance(extra, dict) else False,
                })
            out["sections"].append({"id": section, "title": s.get("title", section),
                                    "description": s.get("description", ""), "fields": fields})
        return out


SECTION_SCHEMA_NAMES = {
    "architecture": "ArchitectureSettings", "models": "ModelsSettings", "voice_stt": "VoiceSTTSettings",
    "voice_tts": "VoiceTTSSettings", "skills": "SkillsSettings", "memory": "MemorySettings",
    "learning": "LearningSettings", "agents": "AgentsSettings", "integrations": "IntegrationsSettings",
    "confirmation": "ConfirmationSettings", "privacy": "PrivacySettings",
    "observability": "ObservabilitySettings"}


def _schema_name(section: str) -> str:
    return SECTION_SCHEMA_NAMES.get(section, section)


def _deep_merge(base: dict, patch: dict) -> dict:
    out = json.loads(json.dumps(base))
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S")


_service: SettingsService | None = None
_svc_lock = threading.Lock()


def get_settings_service() -> SettingsService:
    global _service
    with _svc_lock:
        if _service is None:
            _service = SettingsService()
        return _service


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp(prefix="ishti_settings_")
    svc = SettingsService(data_dir=tmp)
    # defaults load, describe covers all 12 sections
    assert len(svc.describe()["sections"]) == 12
    # update validates: bad temperature refused, disk untouched
    try:
        svc.update({"models": {"temperature": 9.9}})
        raise SystemExit("invalid value accepted!")
    except ValueError:
        pass
    svc.update({"models": {"temperature": 0.7}, "integrations": {"flowise_api_key": "sk-test"}})
    assert svc.get()["models"]["temperature"] == 0.7
    # secret kept out of settings.json, placeholder on export, import restores the rest
    with open(svc.path, encoding="utf-8") as f:
        assert "sk-test" not in f.read(), "secret leaked into settings.json"
    exp = svc.export()
    assert exp["settings"]["integrations"]["flowise_api_key"] == "***"
    svc2 = SettingsService(data_dir=os.path.join(tmp, "two"))
    got = svc2.import_settings(exp)
    assert got["models"]["temperature"] == 0.7
    secrets2 = svc2._load_secrets().get("integrations", {})
    assert not secrets2.get("flowise_api_key"), secrets2  # the *** placeholder was dropped, not stored
    svc2.reset_section("models")
    assert svc2.get()["models"]["temperature"] == 0.3
    print("[self-check] core.settings OK — validation, secret split, export/import, reset")
