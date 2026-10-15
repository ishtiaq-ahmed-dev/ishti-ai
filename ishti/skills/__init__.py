"""Skill loader. Each skill is a folder: manifest.json + skill.py exposing run(text, session).

run() returns {"reply": str, "sources": [...]} or None ("not mine" - the dispatcher falls
through to the next choice). Optional match(text) -> bool is a deterministic trigger checked
before Laya. A skill that fails to load is marked broken and skipped; nothing else breaks."""
import importlib
import importlib.util
import json
import os
import re
import sys

if "pytest" not in sys.modules:  # every request path imports the skills: a dead console/pipe must never fail a request
    from ishti import safe_stdio
    safe_stdio.install()

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(HERE))


def needs_path():
    """Where runtime gaps and errors are logged: data/needs_runtime.md (ISHTI's own data, never a repo document).
    ISHTI_NEEDS_LOG overrides it (tests point it at a scratch file). Every reader (the meta skill, the
    lifecycle's gap discovery) goes through this one function."""
    if os.environ.get("ISHTI_NEEDS_LOG"):
        return os.environ["ISHTI_NEEDS_LOG"]
    from ishti.test_mode import scratch_path
    return scratch_path(os.path.join(ROOT_DIR, "data", "needs_runtime.md"))


# import name -> pip package, where they differ (manifests list import names)
PIP_NAME = {"PIL": "pillow", "docx": "python-docx", "pptx": "python-pptx", "cv2": "opencv-python", "yaml": "pyyaml",
            "win32gui": "pywin32", "win32api": "pywin32", "bs4": "beautifulsoup4", "yt_dlp": "yt-dlp",
            "screen_brightness_control": "screen-brightness-control", "faster_whisper": "faster-whisper",
            "sklearn": "scikit-learn", "pypdfium2": "pypdfium2", "rapidocr_onnxruntime": "rapidocr_onnxruntime",
            "zxingcpp": "zxing-cpp"}


def install_missing(skill, modules, runner=None, *, approved=False):
    """pip-install the missing packages a skill's manifest declares (only those - never arbitrary names), into
    ISHTI's own Python. Returns what is still missing afterwards (reported by the caller and in the needs log)."""
    import re
    import subprocess
    import sys
    from ishti import CONFIG
    from ishti import test_mode
    if approved is not True or test_mode.enabled() or not CONFIG.get("auto_install", True):
        return modules
    pkgs = [PIP_NAME.get(m, m) for m in modules if re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", m)]
    if len(pkgs) != len(modules) or not pkgs:
        return modules
    run = runner or (lambda args: subprocess.run(args, capture_output=True, text=True, timeout=900,
                                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
    print(f"[skills] {skill}: installing missing {', '.join(pkgs)}")
    p = run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *pkgs])
    importlib.invalidate_caches()
    still = [m for m in modules if importlib.util.find_spec(m) is None]
    if still:
        log_need("error", skill, "auto-install", f"pip couldn't install {', '.join(still)}: "
                 f"{((p.stderr or p.stdout) if hasattr(p, 'stderr') else '')[-160:]}")
    return still


SKILL_CATEGORIES = {
    # System & Desktop
    "apps": "System & Desktop",
    "automation": "System & Desktop",
    "desktop": "System & Desktop",
    "system": "System & Desktop",
    "security": "System & Desktop",
    "meta": "System & Desktop",

    # Files & Storage
    "files": "Files & Storage",
    "folders": "Files & Storage",
    "fileops": "Files & Storage",

    # Web & Internet
    "browser": "Web & Internet",
    "web_search": "Web & Internet",
    "web_fetch": "Web & Internet",
    "webtools": "Web & Internet",
    "online_fallback": "Web & Internet",

    # Communication & Social
    "chat": "Communication & Social",
    "whatsapp": "Communication & Social",
    "email": "Communication & Social",
    "contacts": "Communication & Social",

    # Productivity & Planning
    "calendar": "Productivity & Planning",
    "reminders": "Productivity & Planning",
    "focus": "Productivity & Planning",
    "docs": "Productivity & Planning",
    "planners": "Productivity & Planning",
    "workflows": "Productivity & Planning",
    "digest": "Productivity & Planning",
    "lifelog": "Productivity & Planning",

    # Knowledge & Intelligence
    "memory": "Knowledge & Intelligence",
    "relationships": "Knowledge & Intelligence",
    "deep_research": "Knowledge & Intelligence",
    "gemini": "Knowledge & Intelligence",
    "owner": "Knowledge & Intelligence",

    # Development & Tools
    "dev": "Development & Code",
    "skillsmith": "Development & Code",
    "braintrain": "Development & Code",

    # Media & Creative
    "media": "Media & Creative",
    "video": "Media & Creative",
    "extract": "Media & Creative",
    "capcut": "Media & Creative",

    # Finance & Commerce
    "markets": "Finance & Commerce",
    "shopping": "Finance & Commerce",
    "clients": "Finance & Commerce",
    "ledger": "Finance & Commerce",

    # Utilities & Daily
    "weather": "Utilities & Daily",
    "travel": "Utilities & Daily",
    "utilities": "Utilities & Daily",
    "speedtest": "Utilities & Daily",
    "legacy": "Utilities & Daily",
}


# The manifests grew two names for the same folder ("Finance" / "Finance & Commerce"): the Skills tree shows one folder for each.
CATEGORY_CANON = {
    "Communication & Social": "Communication", "Computer Control": "Computer & System", "System & Desktop": "Computer & System",
    "Utilities & Daily": "Daily Life", "Health": "Health & Habits", "Development & Code": "Development",
    "Documents & Files": "Files & Documents", "Files & Storage": "Files & Documents", "Office": "Files & Documents",
    "Writing & Text": "Writing", "Finance & Commerce": "Finance", "Productivity & Planning": "Productivity",
    "Security & Privacy": "Security", "Knowledge & Intelligence": "Knowledge & Reasoning", "Reasoning": "Knowledge & Reasoning",
}


class Skill:
    def __init__(self, folder, rel_dir=None):
        rel_dir = rel_dir or folder
        self.folder = folder
        self.rel_dir = rel_dir
        self.path = f"ishti/skills/{rel_dir}".replace("\\", "/")
        manifest_path = os.path.join(HERE, rel_dir, "manifest.json")
        with open(manifest_path, encoding="utf-8") as f:
            self.manifest = json.load(f)
        self.name = self.manifest["name"]
        raw = self.manifest.get("category") or SKILL_CATEGORIES.get(self.name, "General")
        self.category = CATEGORY_CANON.get(raw, raw)
        self.manifest["category"] = self.category
        self.manifest["path"] = self.path
        self.manifest["folder"] = folder
        self.module = None
        self.problem = ""
        self.missing_optional = [m for m in self.manifest.get("needs", {}).get("optional_python", [])
                                 if importlib.util.find_spec(m) is None]
        missing = [m for m in self.manifest.get("needs", {}).get("python", [])
                   if importlib.util.find_spec(m) is None]
        if missing:
            fix = " ".join(PIP_NAME.get(m, m) for m in missing)
            self.problem = (f"missing python packages: {', '.join(missing)} (owner-approved install required - "
                            f"run: venv\\Scripts\\python -m pip install {fix})")
        else:
            clean_rel = rel_dir.replace("/", ".").replace("\\", ".")
            mod_path = f"ishti.skills.{clean_rel}.skill"
            try:
                self.module = importlib.import_module(mod_path)
            except Exception:
                try:
                    self.module = importlib.import_module(f"ishti.skills.{folder}.skill")
                except Exception as exc:
                    self.problem = f"{type(exc).__name__}: {exc}"

    @property
    def status(self):
        if self.problem:
            return "broken"
        status = self.manifest.get("status", "working")
        return "partial" if status == "working" and self.missing_optional else status

    def matches(self, text: str) -> bool:
        fn = getattr(self.module, "match", None)
        return bool(fn and fn(text))

    def run(self, text, session):
        return self.module.run(text, session)

    def info(self):
        return {
            **self.manifest,
            "category": self.category,
            "path": self.path,
            "folder": self.folder,
            "status": self.status,
            "problem": self.problem,
            "missing_optional": self.missing_optional,
        }


def load_skills() -> dict:
    skills = {}
    # 1. Direct skills under HERE
    for d in sorted(os.listdir(HERE)):
        manifest_path = os.path.join(HERE, d, "manifest.json")
        if os.path.isfile(manifest_path):
            s = Skill(d)
            skills[s.name] = s

    # 2. Category subfolders (if any)
    for d in sorted(os.listdir(HERE)):
        cat_dir = os.path.join(HERE, d)
        if os.path.isdir(cat_dir) and not os.path.isfile(os.path.join(cat_dir, "manifest.json")) and not d.startswith((".", "_")):
            for sub in sorted(os.listdir(cat_dir)):
                sub_manifest = os.path.join(cat_dir, sub, "manifest.json")
                if os.path.isfile(sub_manifest):
                    s = Skill(sub, rel_dir=f"{d}/{sub}")
                    if s.name not in skills:
                        skills[s.name] = s
    if os.environ.get("ISHTI_INSTRUCTION_SKILLS"):
        from ishti.instruction_skills import guides, InstructionSkill
        import yaml
        try:
            instruction_records = guides()
        except (ValueError, OSError, yaml.YAMLError) as exc:
            log_need("error", "instructions", "load", f"Instruction guides rejected ({type(exc).__name__}); check the selected roots and file format.")
            instruction_records = {}
        for record in instruction_records.values():
            guide = InstructionSkill(record)
            if guide.name in skills:
                raise ValueError("Instruction guide collides with a native skill")
            skills[guide.name] = guide
    return skills


def md_table(headers, rows):
    """A Markdown table (the dashboard renders it; voice reads a summary of the first rows, see voice/tts.speakable)."""
    esc = lambda v: str(v).replace("|", "/").replace("\n", " ")
    return "\n".join(["| " + " | ".join(map(esc, headers)) + " |", "|" + "|".join("---" for _ in headers) + "|"]
                     + ["| " + " | ".join(map(esc, r)) + " |" for r in rows])


def trace(session, name, label, status="done", **detail):
    """Report a tool-level action to the live Thinking panel / graph. No-op outside a traced request."""
    fn = session.get("trace") if isinstance(session, dict) else None
    if fn:
        fn(type="tool", name=name, label=label, status=status, **detail)


YES = {
    "yes", "y", "yeah", "yep", "go ahead", "do it", "ok", "okay", "confirm", "sure", "proceed",
    "done", "continue", "solved", "solved it", "ok done", "carry on", "approved", "approval",
    "i approve", "giving approval", "giving you approval", "i am giving you approval",
    "haan kar do", "kar do", "haan", "jee", "ji", "ji haan", "jee haan", "yes please",
    "yes go ahead", "yes do it", "go for it", "please proceed", "please do it",
}
NO = {"no", "n", "nope", "cancel", "stop", "don't", "dont", "abort", "never mind", "nevermind", "mat karo", "nahin", "nahi"}

_YES_PAT = re.compile(
    r"\b(?:i(?:'m|\s+am)?\s+(?:giving\s+you\s+)?approval|giving\s+approval|approved|confirm(?:ed)?|"
    r"go\s+ahead|proceed|haan\s+kar\s+do|kar\s+do|yes\s+please|yes\s+go\s+ahead)\b",
    re.I,
)
_NO_PAT = re.compile(r"\b(?:cancel|stop|don'?t|abort|never\s*mind|mat\s+karo|nahin|nahi)\b", re.I)


def answer_of(text):
    """'yes' / 'no' / None for a reply to a skill's yes/no question. None = the owner moved on to
    something else: the skill drops its question and the message is routed normally."""
    t = (text or "").strip().lower().strip(" .!")
    if t in YES or _YES_PAT.search(t):
        if not _NO_PAT.search(t):
            return "yes"
    if t in NO or _NO_PAT.search(t):
        return "no"
    return None


def ask_first(session, skill, question, action):
    """The one yes/no gate for sensitive (T2) actions: nothing runs until the owner says yes. action() -> reply
    text. The dispatcher runs it on "yes", cancels on "no", and drops it if the owner moves on to something else."""
    session["pending"] = {"skill": skill, "gate": question, "action": action}
    return {"reply": question.rstrip(" ?") + "? (yes/no)", "sources": []}


def enable_skill(name) -> bool:
    """Switch a shipped-off skill on for good (manifest status -> working), after the owner said yes. Returns True."""
    path = os.path.join(HERE, name, "manifest.json")
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    m["status"] = "working"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2, ensure_ascii=False)
        f.write("\n")
    try:
        from ishti.dispatcher import SKILLS
        if name in SKILLS:
            SKILLS[name].manifest["status"] = "working"
    except Exception as exc:
        print(f"[skills] {name} enabled on disk; live registry not updated ({exc})")
    return True


def log_need(kind, skill, request, detail):
    """Errors and capability gaps go to the needs log (what was asked, what failed, why) instead of being
    swallowed. The same kind+skill+detail is logged once. Logging must never break the caller, but a log that
    cannot be written is reported on stderr once per process instead of vanishing."""
    import sys
    import time
    path = needs_path()
    key = f"[{kind}] {skill}: {detail}"[:200]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, encoding="utf-8") as f:
                if key in f.read():
                    return
        except FileNotFoundError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n- {time.strftime('%Y-%m-%d %H:%M')} {key} - asked: \"{request[:120]}\"")
    except OSError as e:
        if not getattr(log_need, "_warned", False):
            log_need._warned = True
            print(f"[needs] cannot write {path}: {e}", file=sys.stderr)


def ground_dates(text, today=None):
    """Relative dates -> real ones before searching ("this year" -> 2026). Search engines match the
    literal words, so "final this year" otherwise returns whichever year's final ranks highest."""
    import datetime as dt
    import re
    d = today or dt.date.today()
    rules = [(r"\bthis year\b", str(d.year)), (r"\blast year\b", str(d.year - 1)), (r"\bnext year\b", str(d.year + 1)),
             (r"\bthis month\b", d.strftime("%B %Y")), (r"\blast month\b", (d.replace(day=1) - dt.timedelta(days=1)).strftime("%B %Y")),
             (r"\byesterday\b", (d - dt.timedelta(days=1)).strftime("%d %B %Y")), (r"\btoday\b", d.strftime("%d %B %Y"))]
    for rx, value in rules:
        text = re.sub(rx, value, text, flags=re.I)
    return text
