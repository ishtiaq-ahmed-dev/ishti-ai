"""Resume parser (inventory domain 9 #7): a CV (PDF/text) -> structured JSON, locally.

"parse my resume D:/cv.pdf", "extract details from this cv D:/cv.txt",
"resume to json D:/docs/ismael.pdf"

Extraction is regex + section-header heuristics: contact block (email/phone/links), a
SKILLS section split on separators, EDUCATION and EXPERIENCE entries with their date lines,
and a section map (which line ranges each section spans). The result prints as JSON and is
saved to data/extracted/<name>.resume.json. Nothing is uploaded (T1, local write only).
Fields it cannot find are null — never invented.
"""
import json
import os
import re

from ishti.skills import trace

I = re.I

PATH = r"(?P<path>[A-Za-z]:[\\/][^\"']+?\.(?:pdf|txt|md)|[\w./\\ -]+?\.(?:pdf|txt|md))"
ASKS = re.compile(
    rf"^\s*(?:parse\s+(?:my\s+|the\s+)?res(?:ume|umé)|res(?:ume|umé)\s+to\s+json|"
    rf"extract\s+(?:the\s+)?(?:details|fields)\s+from\s+(?:this\s+)?(?:cv|res(?:ume|umé)))\s*"
    rf"(?:from\s+)?{PATH}\s*$", I)

SECTION_HEADS = ("experience", "work experience", "employment", "education", "skills",
                 "projects", "certifications", "summary", "profile", "objective", "contact")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}")
PHONE = re.compile(r"(?:\+92|0)?[\s-]?3\d{2}[\s-]?\d{7}\b|\+\d{1,3}[\s-]?\d{3}[\s-]?\d{6,10}")
LINK = re.compile(r"(?:https?://|www\.)[^\s,)]+|linkedin\.com/[\w/-]+|github\.com/[\w/-]+")
DATE_LINE = re.compile(r"(?:19|20)\d{2}\s*(?:-|–|to)\s*(?:(?:19|20)\d{2}|present|current)|(?:19|20)\d{2}", I)


def _text_of(path):
    if not os.path.isfile(path):
        return None
    if path.lower().endswith(".pdf"):
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                return "\n".join(p.extract_text() or "" for p in pdf.pages)
        except Exception:
            return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _sections(lines):
    """line index -> section name, from ALL-CAPS or Title headers."""
    heads = {}
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or len(s) > 40:
            continue
        clean = re.sub(r"[^a-z ]", "", s.lower()).strip()
        if clean in SECTION_HEADS:
            heads[i] = clean
    return heads


def _span(heads, start, total):
    ends = sorted(e for e in heads if e > start) + [total]
    return start + 1, ends[0]


def parse(path):
    text = _text_of(path)
    if text is None:
        return f"Could not read {path} (missing file or unreadable PDF)."
    lines = text.splitlines()
    heads = _sections(lines)
    head = "\n".join(lines[:12])  # contact block lives up top
    out = {
        "name": None, "email": None, "phone": None, "links": [],
        "skills": [], "education": [], "experience": [], "sections": {},
    }
    if (m := EMAIL.search(head)):
        out["email"] = m.group(0)
    if (m := PHONE.search(head)):
        out["phone"] = re.sub(r"\s+", " ", m.group(0)).strip()
    out["links"] = LINK.findall(head)[:5]
    # name: first non-empty line without @/http/digits, before any section head
    for ln in lines[:8]:
        s = ln.strip()
        if s and not EMAIL.search(s) and not LINK.search(s) and not DATE_LINE.search(s) \
                and not any(i in heads for i in [lines.index(ln) if ln in lines else -1]) \
                and not s.lower().startswith(("curriculum", "resume", "cv")):
            words = s.split()
            if 1 < len(words) < 6 and all(w[:1].isupper() or w[:1].isalpha() for w in words):
                out["name"] = s
                break
    for idx, name in heads.items():
        a, b = _span(heads, idx, len(lines))
        out["sections"][name] = {"lines": [a, b]}
    def block(sec):
        if sec not in out["sections"]:
            return []
        a, b = out["sections"][sec]["lines"]
        return [l.strip() for l in lines[a:b] if l.strip()]
    sk = block("skills")
    if sk:
        items = re.split(r"[,;•|/]|\n", "\n".join(sk))
        out["skills"] = [s.strip() for s in items if 1 < len(s.strip()) < 40][:25]
    for sec, key in (("experience", "experience"), ("work experience", "experience"),
                     ("employment", "experience"), ("education", "education")):
        for ln_ in block(sec):
            if DATE_LINE.search(ln_) or len(ln_) > 12:
                entry = {"line": ln_[:120], "dates": DATE_LINE.findall(ln_)}
                if entry not in out[key]:
                    out[key].append(entry)
            if len(out[key]) >= 12:
                break
    out_dir = os.path.join("data", "extracted")
    os.makedirs(out_dir, exist_ok=True)
    stem = re.sub(r"[^\w-]", "_", os.path.splitext(os.path.basename(path))[0])
    fp = os.path.join(out_dir, f"{stem}.resume.json")
    with open(fp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    found = [k for k in ("name", "email", "phone") if out[k]]
    found += [k for k in ("skills", "education", "experience") if out[k]]
    return (f"**Parsed {os.path.basename(path)}** — found: {', '.join(found) or 'nothing usable'}.\n"
            f"```json\n{json.dumps({k: out[k] for k in ('name', 'email', 'phone', 'links', 'skills')}, ensure_ascii=False, indent=1)[:800]}\n```\n"
            f"Full record (with sections + experience/education lines): `{fp}`")


def match(text):
    return bool(ASKS.match(text.strip()))


def run(text, session):
    m = ASKS.match(text.strip())
    if not m:
        return None
    trace(session, "resumeparse", "extract")
    path = m.group("path").strip()
    return {"reply": parse(path), "sources": [path] if os.path.isfile(path) else []}


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    cv = os.path.join(tmp, "cv.txt")
    open(cv, "w", encoding="utf-8").write(
        "Ishtiaq Ahmed\n"
        "Peshawar, Pakistan | ishtiaq@example.com | +92 300 1234567\n"
        "github.com/ishtiaq | linkedin.com/in/ishtiaq\n"
        "\nSKILLS\nPython, pandas, sql, ffmpeg; communication\n"
        "\nEXPERIENCE\nData Analyst, ACME — 2021 - present\nBuilt dashboards and pipelines.\n"
        "Intern, Beta Corp — 2020 - 2021\nCleaned data.\n"
        "\nEDUCATION\nBSc Computer Science, IMSciences — 2017 - 2021\n")

    assert match("parse my resume " + cv)
    assert match("resume to json " + cv)
    assert match("extract details from this cv " + cv)
    assert not match("parse my feelings")
    assert not match("resume the video")              # verb, not the noun

    r = run("parse my resume " + cv, {"_resumeparse_out": None})
    assert "Ishtiaq Ahmed" in r["reply"], r
    assert "ishtiaq@example.com" in r["reply"] and "+92 300 1234567" in r["reply"], r
    assert "Python" in r["reply"] and "pandas" in r["reply"], r
    assert "found:" in r["reply"], r
    fp = os.path.join("data", "extracted", "cv.resume.json")
    assert os.path.isfile(fp), r                      # the real data/ output (self-check runs once)
    got = json.load(open(fp, encoding="utf-8"))
    assert got["experience"] and got["education"], got
    r2 = run("parse my resume D:/nope/cv.pdf", {})
    assert "Could not read" in r2["reply"]
    print("[self-check] resumeparse OK - contact block, skills split, experience/education with "
          "dates, JSON in data/extracted; missing fields are null, never invented")
