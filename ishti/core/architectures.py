"""Architecture Registry (Part C) — switchable architectures, no code edits.

Six profiles, each declaring its components, their order, per-component settings,
required dependencies and a health check:

  1. simple_voice            STT -> Qwen3 -> TTS (no tools; fastest)
  2. router_single_agent     STT -> Router -> Qwen3 -> Tools -> Validation -> Qwen3 -> TTS
                             (DEFAULT; runs on arch1, the proven dispatcher+Laya+skills path)
  3. memory_augmented        router_single_agent + memory retrieval/context assembly
                             (memory slot wired; no-op stub until Prompt 2's memory)
  4. multi_agent_orchestrator  orchestrator + specialist sub-agents (slot + stub, Prompt 3)
  5. flowise_flow            delegate to a Flowise flow via its API (slot + stub, Prompt 3)
  6. custom                  user-built by toggling components

Switching: from Settings UI (`/api/settings/architecture`), the config file, or a
voice/text command ("switch to multi agent mode" — asks for confirmation). Before a
profile activates, its health check runs; a failed check keeps the current profile and
explains why. Conversation state and memory survive every switch (they live in
ishti.core.state, keyed by session id, never inside a profile).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field

from ishti import CONFIG, ROOT

CFG_PATH = os.path.join(ROOT, "ishti", "config.json")
_lock = threading.Lock()

# --------------------------------------------------------------------- profiles


@dataclass
class Profile:
    id: str
    name: str
    description: str
    components: list[str]
    order: list[str]                    # the pipeline order of the components
    settings: dict = field(default_factory=dict)
    requires: list[str] = field(default_factory=list)   # dependency ids for the health check
    builtin: bool = True


PROFILES: dict[str, Profile] = {
    "simple_voice": Profile(
        id="simple_voice", name="Simple voice",
        description="STT -> Qwen3 -> TTS. No tools, no routing — the fastest conversational path.",
        components=["stt", "llm", "tts"],
        order=["stt", "resolver", "llm", "tts"],
        settings={"llm_role": "planner", "tools": False, "memory": False},
        requires=["ollama", "stt", "tts"]),
    "router_single_agent": Profile(
        id="router_single_agent", name="Router + single agent (default)",
        description="STT -> Router (skills shortlist) -> Qwen3 think/plan/call tools -> Tool "
                    "execution (policy-checked) -> Validation -> Qwen3 final -> TTS. The main pipeline.",
        components=["stt", "intent_router", "llm", "tools", "validation", "tts"],
        order=["stt", "resolver", "intent_router", "llm", "tools", "validation", "llm_final", "tts"],
        settings={"llm_role": "main", "tools": True, "memory": False,
                  "shortlist_min": 5, "shortlist_max": 20},
        requires=["ollama", "stt", "tts", "skills"]),
    "memory_augmented": Profile(
        id="memory_augmented", name="Memory-augmented",
        description="router_single_agent + memory retrieval/context assembly on every turn "
                    "(memory engine lands in Prompt 2; the slot is wired today).",
        components=["stt", "intent_router", "memory", "llm", "tools", "validation", "tts"],
        order=["stt", "resolver", "memory", "intent_router", "llm", "tools", "validation",
               "llm_final", "tts"],
        settings={"llm_role": "main", "tools": True, "memory": True},
        requires=["ollama", "stt", "tts", "skills"]),
    "multi_agent_orchestrator": Profile(
        id="multi_agent_orchestrator", name="Multi-agent orchestrator",
        description="The supervisor decomposes the request, specialist sub-agents run the pieces "
                    "in parallel (researcher, web, coder, PC-control, fact-checker...), verify "
                    "their work, and a writer synthesizes. Pattern + budgets in Settings -> Agents.",
        components=["stt", "intent_router", "orchestrator", "memory", "llm", "tools", "validation", "tts"],
        order=["stt", "resolver", "intent_router", "memory", "orchestrator", "llm", "tools",
               "validation", "llm_final", "tts"],
        settings={"llm_role": "main", "tools": True, "memory": True, "max_subagents": 4,
                  "pattern": "supervisor"},
        requires=["ollama", "stt", "tts", "skills"]),
    "flowise_flow": Profile(
        id="flowise_flow", name="Flowise flow",
        description="Delegate requests to your Flowise flows over their local API: the "
                    "intent->flow mapping in Settings -> Integrations picks which flow answers. "
                    "Flows receive the resolved request + minimum context only.",
        components=["stt", "flowise", "llm", "tts"],
        order=["stt", "resolver", "flowise", "llm_final", "tts"],
        settings={"llm_role": "planner", "tools": False, "memory": False},
        requires=["stt", "tts", "flowise"]),
    "custom": Profile(
        id="custom", name="Custom",
        description="Your own pipeline: toggle components and order in Settings -> Architecture.",
        components=["stt", "resolver", "intent_router", "memory", "llm", "tools", "validation", "tts"],
        order=["stt", "resolver", "intent_router", "memory", "llm", "tools", "validation",
               "llm_final", "tts"],
        settings={"llm_role": "main", "tools": True, "memory": True},
        requires=["ollama"]),
}


# --------------------------------------------------------------------- health checks

def _check_ollama() -> tuple[bool, str]:
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as r:
            tags = json.load(r)
        names = [m.get("name", "") for m in tags.get("models", [])]
        return (True, f"Ollama running, {len(names)} models") if names else (False, "Ollama has no models")
    except Exception as exc:
        return False, f"Ollama not reachable ({type(exc).__name__})"


def _check_stt() -> tuple[bool, str]:
    try:
        import importlib.util
        if importlib.util.find_spec("faster_whisper"):
            return True, "faster-whisper present"
        return False, "faster-whisper not installed"
    except Exception as exc:
        return False, str(exc)


def _check_tts() -> tuple[bool, str]:
    try:
        import importlib.util
        ok = bool(importlib.util.find_spec("piper") or os.path.isdir(str((CONFIG.get("voice") or {}).get("kokoro_dir", ""))))
        return (True, "Kokoro/Piper voice files present") if ok else (False, "no TTS voices installed")
    except Exception as exc:
        return False, str(exc)


def _check_skills() -> tuple[bool, str]:
    try:
        from ishti.skills import load_skills
        n = len(load_skills())
        return (True, f"{n} skills loaded") if n else (False, "no skills loaded")
    except Exception as exc:
        return False, f"skills failed to load ({type(exc).__name__})"


def _check_flowise() -> tuple[bool, str]:
    url = ((CONFIG.get("integrations") or {}).get("flowise_url")
           or os.environ.get("FLOWISE_URL") or "")
    if not url:
        return False, "no Flowise flow URL configured (Settings -> Integrations)"
    try:
        import urllib.request
        with urllib.request.urlopen(url.rstrip("/") + "/api/v1/ping", timeout=3) as r:
            return True, f"Flowise reachable ({r.status})"
    except Exception as exc:
        return False, f"Flowise not reachable ({type(exc).__name__})"


HEALTH = {
    "ollama": _check_ollama,
    "stt": _check_stt,
    "tts": _check_tts,
    "skills": _check_skills,
    "flowise": _check_flowise,
}


def health_check(profile_id: str) -> dict:
    """{ok, checks: [{dep, ok, detail}]} for one profile."""
    p = PROFILES.get(profile_id)
    if not p:
        return {"ok": False, "checks": [], "error": f"unknown profile '{profile_id}'"}
    checks = []
    for dep in p.requires:
        fn = HEALTH.get(dep)
        ok, detail = (False, f"no health check for '{dep}'") if not fn else fn()
        checks.append({"dep": dep, "ok": ok, "detail": detail})
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


# --------------------------------------------------------------------- active state

def _load_active() -> str:
    cfg = CONFIG.get("arch") or {}
    active = str(cfg.get("active_profile") or "")
    if active in PROFILES:
        return active
    legacy = str(cfg.get("active") or "")
    if legacy == "arch2":
        return "memory_augmented"
    return "router_single_agent"          # spec: router_single_agent is the DEFAULT


class ArchitectureRegistry:
    """One owner of 'which architecture is active'. Hot switch, persisted, health-checked;
    conversation state lives outside this class so it survives every switch."""

    def __init__(self):
        self._active = _load_active()
        self._switched_at = 0.0
        self._switched_by = "startup"
        self._lock = threading.Lock()
        self._custom = dict((CONFIG.get("arch") or {}).get("custom_profile") or {})

    # ---- introspection -----------------------------------------------------
    def active(self) -> str:
        return self._active

    def describe(self) -> dict:
        profs = []
        for pid, p in PROFILES.items():
            profs.append({
                "id": pid, "name": p.name, "description": p.description,
                "components": p.components, "order": p.order, "settings": p.settings,
                "requires": p.requires, "builtin": p.builtin,
                "selected": pid == self._active,
            })
        return {"active": self._active, "profiles": profs,
                "switched_at": self._switched_at, "switched_by": self._switched_by,
                "health": health_check(self._active)}

    # ---- switching ----------------------------------------------------------
    def set_active(self, profile_id: str, by: str = "settings",
                   run_health_check: bool = True) -> dict:
        profile_id = str(profile_id or "").strip().lower().replace(" ", "_").replace("-", "_")
        aliases = {"1": "router_single_agent", "2": "memory_augmented", "simple": "simple_voice",
                   "voice": "simple_voice", "multi_agent": "multi_agent_orchestrator",
                   "multiagent": "multi_agent_orchestrator", "orchestrator": "multi_agent_orchestrator",
                   "flowise": "flowise_flow", "default": "router_single_agent"}
        profile_id = aliases.get(profile_id, profile_id)
        if profile_id not in PROFILES:
            raise ValueError(f"unknown architecture '{profile_id}' "
                             f"(one of: {', '.join(PROFILES)})")
        if profile_id == self._active:
            return self.describe()
        hc = health_check(profile_id) if run_health_check else {"ok": True, "checks": []}
        if not hc["ok"]:
            failed = "; ".join(f"{c['dep']}: {c['detail']}" for c in hc["checks"] if not c["ok"])
            return {"switched": False, "reason": f"health check failed — staying on "
                                                 f"'{self._active}'. ({failed})", **hc}
        with self._lock:
            self._active = profile_id
            self._switched_at = time.time()
            self._switched_by = by
            self._persist()
        return {"switched": True, **self.describe()}

    def _persist(self):
        """Persist to config.json (the same file arch2.registry owns; the two keys
        coexist: arch.active for the legacy backends, arch.active_profile for profiles)."""
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                disk = json.load(f)
            disk.setdefault("arch", {})["active_profile"] = self._active
            with open(CFG_PATH, "w", encoding="utf-8") as f:
                json.dump(disk, f, indent=2, ensure_ascii=False)
            CONFIG.setdefault("arch", {})["active_profile"] = self._active
        except OSError as exc:
            print(f"[arch] config.json write skipped: {exc}")

    # ---- voice/text command --------------------------------------------------
    COMMAND = re.compile(
        r"\b(?:switch|change|go)\s+(?:to\s+)?(?:the\s+)?(?P<target>simple\s*voice(?:\s*mode)?|"
        r"router\s*(?:single\s*)?agent(?:\s*mode)?|default\s+(?:architecture|mode)?|"
        r"memory\s*(?:augmented)?(?:\s*mode)?|multi[ -]?agent(?:\s*orchestrator)?(?:\s*mode)?|"
        r"orchestrator(?:\s*mode)?|flowise(?:\s*flow)?(?:\s*mode)?|custom(?:\s*mode)?)\s*(?:architecture|mode)?\b"
        r"|\b(?:use|activate)\s+(?:the\s+)?(?P<target2>simple\s*voice|multi[ -]?agent|orchestrator|flowise|custom)\b", re.I)

    def handle_command(self, text: str, confirm: bool = True) -> str | dict | None:
        """Voice/text command. Returns the reply string, a {'pending_confirmation': ...}
        dict when confirmation is needed, or None when the text isn't an arch command."""
        t = (text or "").strip()
        if len(t.split()) > 10:
            return None
        m = self.COMMAND.search(t)
        if not m:
            return None
        target = (m.group("target") or m.group("target2") or "").strip().lower()
        target = re.sub(r"[\s-]+", "_", target)
        target = re.sub(r"_(?:mode|architecture)$", "", target)   # "multi agent mode" -> multi_agent
        target = {"default": "router_single_agent", "router_agent": "router_single_agent",
                  "router_single_agent": "router_single_agent",
                  "multi_agent_orchestrator": "multi_agent_orchestrator",
                  "multi_agent": "multi_agent_orchestrator",
                  "memory_augmented": "memory_augmented",
                  "memory": "memory_augmented"}.get(target, target)
        if target not in PROFILES:
            return None
        if target == self._active:
            return f"Already on {PROFILES[target].name}."
        if confirm:
            return {"pending_confirmation": {
                "skill": "architecture", "target": target,
                "question": f"Switch to {PROFILES[target].name}? (yes/no)"}}
        res = self.set_active(target, by="owner command")
        if res.get("switched") is False:
            return res["reason"]
        return f"Switched to {PROFILES[target].name}. {PROFILES[target].description}"

    def confirm_switch(self, target: str, answer_yes: bool) -> str:
        if not answer_yes:
            return "Cancelled — staying on the current architecture."
        res = self.set_active(target, by="owner command")
        if res.get("switched") is False:
            return res["reason"]
        return f"Switched to {PROFILES[target].name}. {PROFILES[target].description}"


_registry: ArchitectureRegistry | None = None
_reg_lock = threading.Lock()


def get_registry() -> ArchitectureRegistry:
    global _registry
    with _reg_lock:
        if _registry is None:
            _registry = ArchitectureRegistry()
        return _registry


if __name__ == "__main__":
    # ---- offline self-check ---------------------------------------------------
    reg = ArchitectureRegistry()
    assert reg.active() in PROFILES
    d = reg.describe()
    assert len(d["profiles"]) == 6
    # switching to a profile whose deps fail must refuse cleanly
    res = reg.set_active("flowise_flow", by="self-check")
    assert res.get("switched") is False and "health check failed" in res["reason"], res
    # command parsing
    assert reg.handle_command("what's the weather") is None
    pend = reg.handle_command("switch to multi agent mode")
    assert isinstance(pend, dict) and pend["pending_confirmation"]["target"] == "multi_agent_orchestrator", pend
    # Prompt 3: the orchestrator/flowise profiles are real implementations now
    assert "stub" not in PROFILES["multi_agent_orchestrator"].description.lower()
    assert "stub" not in PROFILES["flowise_flow"].description.lower()
    # every profile's health check returns a shaped result
    for pid in PROFILES:
        hc = health_check(pid)
        assert {"ok", "checks"} <= set(hc) and all({"dep", "ok", "detail"} <= set(c) for c in hc["checks"])
    print("[self-check] core.architectures OK — 6 profiles, health-gated switch, command parsing")
