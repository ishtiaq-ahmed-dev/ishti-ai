"""Owner commands: audio on/off, who am I, what do you know about me, what can you do, run diagnostics."""
import re

from ishti.facts import get_facts

I = re.I
AUDIO_OFF = re.compile(r"^(?:mute|stop (?:reading|talking)|be quiet|(?:turn )?(?:audio|voice|sound) off|turn off (?:the )?(?:audio|voice|sound))[.!]?$", I)
AUDIO_ON = re.compile(r"^(?:unmute|read (?:that|it)(?: aloud| out loud)?|say it|speak up|(?:turn )?(?:audio|voice|sound) on|turn on (?:the )?(?:audio|voice|sound))[.!]?$", I)
WHO = re.compile(r"^(?:who am i|do you know who i am|who is your owner)\W*$", I)
KNOW = re.compile(r"^(?:what do you (?:know|remember) about me|show (?:me )?my (?:profile|memory|facts)|what have you saved)\W*$", I)
CAN = re.compile(r"^(?:what can you do|what are your (?:skills|capabilities)|show (?:your )?(?:skills|capabilities)|help)\W*$", I)
DOCTOR = re.compile(r"^(?:run (?:the )?diagnostics|(?:ishti )?doctor|system check|health check)\W*$", I)


def match(text):
    t = text.strip()
    return any(rx.match(t) for rx in (AUDIO_OFF, AUDIO_ON, WHO, KNOW, CAN, DOCTOR))


def run(text, session):
    t = text.strip()
    if AUDIO_OFF.match(t) or AUDIO_ON.match(t):
        from orchestrator.memory.user_profile import get_user_memory
        on = bool(AUDIO_ON.match(t))
        get_user_memory().update_preferences(audio_output=on)
        return {"reply": "Audio on. I'll read my answers aloud." if on else "Audio off.", "sources": []}
    if WHO.match(t):
        o = get_facts().get("owner")
        where = ", ".join(x for x in (o.get("city"), o.get("country")) if x)
        return {"reply": f"You're {o.get('name', 'my owner')}" + (f", {o['role']}" if o.get("role") else "")
                         + (f", in {where}" if where else "") + ".", "sources": []}
    if KNOW.match(t):
        f = get_facts()
        with f.lock:
            rows = f.db.execute("SELECT entity, attr, value FROM facts ORDER BY key, attr").fetchall()
        return {"reply": "\n".join(f"- {e} {a}: {v}" for e, a, v in rows) or "Nothing saved yet.", "sources": []}
    if CAN.match(t):
        from ishti.dispatcher import SKILLS
        lines = [f"- {n} ({s.status}): {s.manifest['route']}" for n, s in SKILLS.items()]
        return {"reply": "Skills:\n" + "\n".join(lines), "sources": []}
    from orchestrator.doctor import IshtiDoctor
    report = IshtiDoctor().check_all()
    bad = report["errors"] + report["warnings"]
    return {"reply": ("All checks passed." if not bad else "Issues:\n" + "\n".join(f"- {b}" for b in bad[:8]))
                     + " Full report: DOCTOR tab.", "sources": []}
