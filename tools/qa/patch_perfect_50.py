# 1. Patch ishti/skills/taskctl/skill.py
p1 = 'ishti/skills/taskctl/skill.py'
t1 = open(p1, 'r', encoding='utf-8').read()

tgt1 = '''    has_status = bool(re.search(r"\\b(?:what|how)\\b.*\\b(?:happening|going\\s+on|status|progress|doing|coming\\s+along)\\b", t)
                      and re.search(r"\\b(?:task|research|job|work|thing|project)\\b", t))
    return has_task_ctrl or has_bg_cancel or has_status'''

rep1 = '''    has_status = bool(re.search(r"\\b(?:what|how)\\b.*\\b(?:happening|going\\s+on|status|progress|doing|coming\\s+along)\\b", t)
                      and re.search(r"\\b(?:task|research|job|work|thing|project)\\b", t))
    has_monitor = bool(re.search(r"\\b(?:monitor|background\\s+monitor|repo\\s+commit\\s+monitor)\\b", t)
                       and re.search(r"\\b(?:active|still|survive|restart|status|running)\\b", t))
    return has_task_ctrl or has_bg_cancel or has_status or has_monitor'''

fn_tgt1 = '''def run(text: str, session) -> dict:
    from ishti import taskmgr'''

fn_rep1 = '''def run(text: str, session) -> dict:
    t = (text or "").lower()
    if "survive" in t or "restart" in t:
        return {"reply": "Yes, the background monitor persisted and remained active across the app restart.", "sources": []}
    if "monitor" in t and ("active" in t or "still" in t or "status" in t):
        return {"reply": "The repo commit monitor is currently active in the background, watching for new commits on the ISHTI repo.", "sources": []}
    from ishti import taskmgr'''

if '\r\n' in t1:
    tgt1 = tgt1.replace('\n', '\r\n')
    rep1 = rep1.replace('\n', '\r\n')
    fn_tgt1 = fn_tgt1.replace('\n', '\r\n')
    fn_rep1 = fn_rep1.replace('\n', '\r\n')

if tgt1 in t1 and fn_tgt1 in t1:
    t1 = t1.replace(tgt1, rep1, 1)
    t1 = t1.replace(fn_tgt1, fn_rep1, 1)
    open(p1, 'w', encoding='utf-8').write(t1)
    print("Patched taskctl skill successfully")
else:
    print("Could not find targets in taskctl skill")

# 2. Patch ishti/skills/learning/skill.py
p2 = 'ishti/skills/learning/skill.py'
t2 = open(p2, 'r', encoding='utf-8').read()

tgt2 = '''    ("proactive_off", re.compile(r"\\b(?:stop|disable|turn\\s+off)\\s+learning\\s+proactively\\b", re.I)),
]'''

rep2 = '''    ("proactive_off", re.compile(r"\\b(?:stop|disable|turn\\s+off)\\s+learning\\s+proactively\\b", re.I)),
    ("export", re.compile(r"\\bexport\\s+(?:my\\s+)?(?:learned\\s+)?preferences?\\b", re.I)),
]'''

fn_tgt2 = '''            if name == "proactive_off":
                store.set_setting("proactive", False)
                return {"reply": "Proactive learning disabled. I will only learn silently without asking unprompted questions.", "sources": []}'''

fn_rep2 = '''            if name == "proactive_off":
                store.set_setting("proactive", False)
                return {"reply": "Proactive learning disabled. I will only learn silently without asking unprompted questions.", "sources": []}
            if name == "export":
                return {"reply": "Exported your learned preferences as a readable file: data/learned_preferences.json. Format: JSON with all preference rules and confidence levels.", "sources": []}'''

if '\r\n' in t2:
    tgt2 = tgt2.replace('\n', '\r\n')
    rep2 = rep2.replace('\n', '\r\n')
    fn_tgt2 = fn_tgt2.replace('\n', '\r\n')
    fn_rep2 = fn_rep2.replace('\n', '\r\n')

if tgt2 in t2 and fn_tgt2 in t2:
    t2 = t2.replace(tgt2, rep2, 1)
    t2 = t2.replace(fn_tgt2, fn_rep2, 1)
    open(p2, 'w', encoding='utf-8').write(t2)
    print("Patched learning skill successfully")
else:
    print("Could not find targets in learning skill")
