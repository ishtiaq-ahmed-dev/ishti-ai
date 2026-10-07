"""Small regression guards for fixes that have no better home. Offline, no models."""
import os

from ishti.core.settings import PrivacySettings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_allow_t3_schema_matches_what_the_dispatcher_reads():
    """The dispatcher does CONFIG.get("allow_t3", {}).get(skill): a mapping. The settings schema used to say list[str]
    (a list would raise at .get). It is a mapping now, and a list saved by an older build is converted, not lost."""
    assert PrivacySettings().allow_t3 == {}
    assert PrivacySettings(allow_t3=["deploy", "wipe"]).allow_t3 == {"deploy": True, "wipe": True}
    assert PrivacySettings(allow_t3={"deploy": True}).allow_t3.get("deploy") is True


def test_arch2_first_pass_prompt_tells_the_model_to_use_tools_for_live_data_and_exact_solvers():
    """Owner-decided follow-up of the arch1/arch2 comparison: arch2 answered a weather question from memory and tried
    to solve a sudoku itself. The prompt must still format cleanly (no stray braces) with both rules present."""
    from ishti.arch2 import pipeline
    text = pipeline.SYSTEM_PASS1.format(tools="- weather: ...", context="(none)")
    assert "ALWAYS call a tool" in text and "never answer from memory" in text
    assert "call it instead of doing the task yourself" in text
    assert "resolve it from the conversation first" in text  # pronoun resolution ("send him X")


def test_every_skill_loads():
    """No skill may be 'broken' (an import error, a missing package): the router skips a broken skill silently."""
    from ishti.skills import Skill
    skills = os.path.join(ROOT, "ishti", "skills")
    broken = []
    for name in sorted(os.listdir(skills)):
        if os.path.isfile(os.path.join(skills, name, "manifest.json")):
            s = Skill(name)
            if s.module is None:
                broken.append((name, s.problem))
    assert not broken, broken


def test_owner_prompt_file_parsing(tmp_path):
    """N1: the owner's own prompts (data/owner_prompts.txt) are what lighter rungs get graded on."""
    from ishti import vram_calibrate
    f = tmp_path / "owner_prompts.txt"
    f.write_text("# my prompts\n\nWhat is 2+2? || 4\nTranslate 'thanks' to Urdu\n  \nplan a trip||Peshawar\n", encoding="utf-8")
    assert vram_calibrate.load_owner_prompts(str(f)) == [
        ("What is 2+2?", "4"), ("Translate 'thanks' to Urdu", None), ("plan a trip", "Peshawar")]
    assert vram_calibrate.load_owner_prompts(str(tmp_path / "missing.txt")) == []


def test_owner_can_confirm_the_profile_in_chat(tmp_path, monkeypatch):
    """N21/N29: "confirm my profile" shows what ISHTI holds, asks yes/no, and only then records the confirmation
    (in data/, never by editing owner_profile.yaml)."""
    from ishti import selfmodel as sm
    from ishti.skills.meta import skill as meta
    monkeypatch.setattr(sm, "CONFIRM", str(tmp_path / "confirmed.json"))
    prof = tmp_path / "owner_profile.yaml"
    prof.write_text('owner:\n  name: "Test Owner"\nconfirmed: false\n', encoding="utf-8")
    monkeypatch.setattr(sm, "PROFILE", str(prof))
    session = {}
    out = meta.run("confirm my profile", session)
    assert "Test Owner" in out["reply"] and out["reply"].endswith("(yes/no)")
    assert sm.profile()["confirmed"] is False             # nothing changes before the yes
    assert "confirmed" in session["pending"]["action"]()
    assert sm.profile()["confirmed"] is True and "confirmed" in prof.read_text(encoding="utf-8")   # yaml untouched
    assert "already confirmed" in meta.run("confirm my details", {})["reply"]


def test_remote_channels_say_what_the_owner_must_do_next():
    """N22/N23: a bot token and a pairing message are the owner's steps - the health output says which one is missing."""
    from ishti.channels.service import next_step
    assert "@BotFather" in next_step("telegram", True, token=False, paired=False, running=False)
    assert "/pair" in next_step("telegram", True, token=True, paired=False, running=False)
    assert "switched off" in next_step("telegram", False, token=True, paired=True, running=False)
    assert next_step("telegram", True, True, True, True) == "Telegram is on and listening."
    assert "risk note" in next_step("whatsapp", True, False, False, False, risk_ack=False)
    # WhatsApp control now runs on the local Baileys bridge: the owner's step is scanning its QR
    assert "scan the QR" in next_step("whatsapp", True, False, False, False, risk_ack=True)
    assert "is off" in next_step("whatsapp", False, False, False, False)
