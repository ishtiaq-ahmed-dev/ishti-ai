"""Files skill: PC file search (user folders + the ISHTI checkout), short answer with paths."""
import re

from ishti.skills import trace
from orchestrator.intent_detector import IntentDetector


# the legacy classifier calls every "search for X" a PC search; only the owner's own files are this skill's
LOCAL = re.compile(r"\b(?:on\s+(?:my\s+|the\s+)?(?:pc|computer|laptop|drive|disk)|files?|folders?|pdfs?|docs?|documents?|"
                   r"downloads|desktop|pictures|photos|videos|music)\b|\.[a-z0-9]{2,4}\b|[A-Za-z]:\\\\", re.I)

# word chars plus hyphen/dot/space, then "folder": "Downloads folder", "JQ AI client folder", "old_project folder"
_WORD_FOLDER = r"[A-Za-z0-9][A-Za-z0-9 ._-]*\s+folder"

# Checklist phrasings that mean "search my files" even without the word "search/find/locate":
# "Open my Downloads folder", "What's in my Projects folder?", "Copy my resume to the JQ AI client folder",
# "Find all PDFs I downloaded this week", "Delete the old_project folder".
FILES_OPEN = re.compile(r"\bopen\s+(?:my\s+|the\s+)?(?:downloads|desktop|documents|pictures|videos|music|projects?|"
                        + _WORD_FOLDER + r")\b", re.I)
FILES_LIST = re.compile(r"\b(?:what|which|list|show|how\s+many|count)\b.*\b(?:in\s+(?:my\s+|the\s+)?"
                        r"(?:downloads|desktop|documents|pictures|videos|music|projects?|[A-Za-z0-9][A-Za-z0-9 ._-]*)\s*"
                        r"(?:folder|directory))\b", re.I)
FILES_COPY = re.compile(r"\bcopy\s+.*\s+to\s+(?:my\s+|the\s+)?(?:" + _WORD_FOLDER + r"|[A-Za-z0-9][A-Za-z0-9 ._-]*(?:\s+folder)?)\b", re.I)
FILES_PDF_WEEK = re.compile(r"\b(?:find|search|locate|look)\b.*\b(?:pdf|pdfs)\b.*\b(?:download|this\s+week|recently)\b"
                             r"|\b(?:pdfs?|pdf)\b.*\b(?:download|this\s+week|recently|new\s+)?\b", re.I)
# DELETE_FOLDER: "Delete the old_project folder", "delete my project folder", "remove the downloads folder".
# Kept separate from the action regexes above because a bare "delete the ... folder" is unambiguously about the 
# owner's own files (not general knowledge) and the run() path hands it to fileops if a path is found.
DELETE_FOLDER = re.compile(r"\b(?:delete|remove|get\s+rid\s+of|trash)\s+(?:the\s+|my\s+)?(?:old\s+)?(?:project\s+)?folder\b", re.I)
# "Find my thesis proposal file", "locate my tax documents": "find/locate my <words> file(s)" is about the
# owner's own files, but the legacy classifier calls it LOCAL_KNOWLEDGE and files never saw it (checklist #4).
FIND_MY_FILE = re.compile(r"\b(?:find|locate|look\s+for|search\s+for)\s+my\s+.+?\b(?:file|files|document|documents|notes?|proposal|report|resume|cv|invoice|receipt|pdfs?)\b", re.I)


def match(text):
    t = re.sub(r"\b(?:the|a|an|my)\s+", "", text, flags=re.I)
    # Playback / total-runtime of videos belongs to the video skill, which opens or sums the real files
    # (checklist #171, #175). "what's in my videos folder" has no play/runtime cue and stays ours below.
    if re.search(r"\bvideos?\b|\bclips?\b", text, re.I) \
            and re.search(r"\b(?:play|watch|runtime|total\s+duration|how\s+long|duration)\b", text, re.I):
        return False
    if FILES_OPEN.search(text) or FILES_LIST.search(text) or FILES_COPY.search(text) or FILES_PDF_WEEK.search(text) \
            or FIND_MY_FILE.search(text):
        return True
    # "delete the old_project folder" is fileops' action regex; only claim it here as a last resort so
    # it isn't lost to the chat brain when fileops' path regex doesn't fire.
    if DELETE_FOLDER.search(t) and not IntentDetector.classify(t)["mode"] == "CHAT":
        return True
    return (IntentDetector.classify(t)["mode"] == "PC_SEARCH" and bool(LOCAL.search(t)))


def query_of(text):
    q = re.sub(r"^(?:please\s+)?(?:search|find|locate|look)\b.*?\\b(?:for|file|files|pc)\s+(?:for\s+|named\s+|called\s+)?",
               "", text, flags=re.I)
    q = re.sub(r"\b(?:on|in)\s+(?:my\s+)?(?:pc|computer|downloads|documents|desktop)\b|\bcontaining\b.*", "", q, flags=re.I)
    return q.strip(" .?!'\\\"") or text


def run(text, session):
    from desktop.computer_use import get_computer_use_agent
    exts = [f".{e.lower()}" for e in re.findall(r"\.([a-z0-9]{2,5})\b", text, flags=re.I)] or None
    m = re.search(r"\bcontaining\s+['\"]?(.+?)['\"]?\s*$", text, flags=re.I)
    q = m.group(1) if m else query_of(text)
    trace(session, "search", f"search your folders for '{q}'", status="start")
    hits = get_computer_use_agent().search_pc(q, extensions=exts, max_results=8, search_content=bool(m))
    trace(session, "search", f"{len(hits)} file(s) for '{q}'", results=[h["path"] for h in hits])
    if not hits:
        return {"reply": f"No files matching '{q}' in your user folders.", "sources": []}
    lines = [f"{i}. {h['path']} ({h['size']}, {h['modified']})" for i, h in enumerate(hits, 1)]
    return {"reply": f"Found {len(hits)} for '{q}':\n" + "\n".join(lines), "sources": [], "files": hits}


if __name__ == "__main__":
    assert match("find files named readme in D:/work/docs")
    assert not match("what is a folder")
    print("[self-check] files OK - routing")
