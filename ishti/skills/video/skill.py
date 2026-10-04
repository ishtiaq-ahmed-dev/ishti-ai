"""Video skill: the local LLM turns the request into one FFmpeg operation (JSON), then
tools/video/video_editor.py runs it (NVENC when available). Outputs are new files; nothing is overwritten."""
import json
import os
import re

from ishti import llm
from ishti.skills import trace
from orchestrator.intent_detector import IntentDetector

OPS = ("trim", "extract_audio", "convert_vertical", "change_speed", "compress", "thumbnail", "text_overlay", "info")
PATH = re.compile(r"[A-Za-z]:[\\/][^\"'<>|?*\n]+?\.(?:mp4|mov|mkv|avi|webm|m4v|wmv)\b", re.I)


VIDEO_FILE = re.compile(r"[\w\-. ]+\.(?:mp4|mov|mkv|avi|webm|m4v|wmv)\b", re.I)
EDIT_VERB = re.compile(r"\b(?:edit|trim|cut|merge|compress|convert|speed\s+up|slow\s+down|caption|subtitle|"
                       r"overlay|watermark|title|add\s+(?:text|the\s+name|a\s+name|music|captions?)|reframe|vertical|"
                       r"extract\s+(?:the\s+)?audio|thumbnail|duration|how\s+long)\b", re.I)
# an editor's pass (ad/promo/reel/effects/CapCut), not a single FFmpeg operation - see pro.py
PRO = re.compile(r"\b(?:ad|ads|advert|advertisement|promo|promotional|commercial|professional(?:ly)?|effects?|cinematic|reel|reels|trailer|teaser|cap\s?cut|like\s+an?\s+editor|showcase|highlight)\b|\ban\s+add\b", re.I)  # "make it like an add" (typo)
ORGANIZE = re.compile(r"\b(?:organi[sz]e|arrange|sort|folders?|files\s+in\s+video)\b", re.I)


PLAY = re.compile(r"\b(?:play|watch|show|open)\b\s+.{0,40}?\bvideos?\b|\bvideos?\b.{0,20}?\b(?:play|watch)\b", re.I)
RUNTIME = re.compile(r"\b(?:total|combined|overall|sum\s+of)\b.{0,30}?\b(?:runtime|duration|length)\b|"
                     r"\b(?:runtime|total\s+duration|how\s+long)\b.{0,40}?\bvideos?\b|\bvideos?\b.{0,30}?\b(?:runtime|total\s+duration|how\s+long)\b", re.I)


def match(text):
    # creative writing / brainstorming / YouTube metadata about videos is chat/writing/web, not FFmpeg video editing
    if re.search(r'\b(?:ideas?|suggest(?:ions?)?|brainstorm|names?|topics?|scripts?|tags?|description|timestamps?|trending|niche|perform|schedule|compare|write\s+(?:a|me|an|the|short)|generate\s+(?:ideas?|titles?)|concept|turn\s+(?:my\s+)?(?:rough\s+)?notes)\b', text, re.I) and not VIDEO_FILE.search(text):
        return False
    # Playback and total-runtime questions about videos aren't edits and must not be lost to folders
    # (checklist #171, #175). Must run before the ORGANIZE guard, which trips on the word "folder".
    if (PLAY.search(text) or RUNTIME.search(text)) and re.search(r"\bvideos?\b|\bclips?\b|\bfootage\b", text, re.I):
        return True
    # needs an edit verb and a video (file or the word); "files in the video folder that are audio" is a folders job
    if ORGANIZE.search(text) and not VIDEO_FILE.search(text):
        return False
    return bool(EDIT_VERB.search(text)) and (bool(VIDEO_FILE.search(text)) or bool(re.search(r"\bvideos?\b", text, re.I))
                                             or IntentDetector.classify(text)["mode"] == "VIDEO_EDIT")


def plan(text):
    out = llm.ask(
        "Turn this video request into JSON with keys: operation (one of " + ", ".join(OPS) + "), "
        "input_file (path or file name as written, or \"\"), start_time and end_time (HH:MM:SS or null), "
        f"speed (number or null), target_size_mb (number or null), overlay_text (the words to put on the video for "
        f"text_overlay, or null), position (top, center or bottom).\nRequest: {text}",
        system=None, temperature=0, response_format={"type": "json_object"})
    try:
        p = json.loads(out)
    except ValueError:
        p = {}
    p["operation"] = p.get("operation") if p.get("operation") in OPS else "info"
    return p


def find_video(name):
    if name and os.path.isfile(name):
        return name
    from desktop.computer_use import get_computer_use_agent
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    hits = get_computer_use_agent().search_pc(stem, extensions=[".mp4", ".mov", ".mkv", ".avi", ".webm"],
                                              max_results=1) if stem else []
    return hits[0]["path"] if hits else None


VIDEO_EXT = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv")
NEWEST = re.compile(r"\b(?:latest|lastest|lastets|last|newest|recent|most\s+recent|top)\b", re.I)
OLDEST = re.compile(r"\b(?:first|oldest|earliest)\b", re.I)
INFO = re.compile(r"\b(?:info|details|duration|how\s+long|length|resolution|size)\b", re.I)
ANSWER_EDIT = re.compile(r"\b(?:speed|slow|faster|audio|mute|9:16|portrait|shorter|smaller|text|name|first|last|"
                         r"seconds?|minutes?|mb)\b", re.I)
EDITS = "make it a professional ad or reel (CapCut project included), trim, add a title/text, make it vertical 9:16, compress, speed up/slow down, extract the audio or a thumbnail"


def recent_videos(text, n=8):
    """Newest videos in the folder the owner names (default: Downloads, Videos, Desktop)."""
    from ishti.skills.folders.skill import all_files, folder_in, known_folders
    _, folder = folder_in(text)
    k = known_folders()
    from ishti.skills.folders.smart import audio_only
    found = [e for d in ([folder] if folder else [k["downloads"], k["videos"], k["desktop"]]) if os.path.isdir(d)
             for e in all_files(d) if os.path.splitext(e.name)[1].lower() in VIDEO_EXT]
    out = []
    for e in sorted(found, key=lambda e: e.stat().st_mtime, reverse=True):
        if len(out) == n:
            break
        if not audio_only(e.path):  # WhatsApp voice notes saved as .mp4 have no picture to edit
            out.append(e.path)
    return out


def pick(answer, choices):
    """The owner's reply to "which video?": a number, part of a name, or latest/first."""
    if not choices:
        return None
    m = re.search(r"\b(\d{1,2})\b", answer)
    if m and 1 <= int(m.group(1)) <= len(choices):
        return choices[int(m.group(1)) - 1]
    if NEWEST.search(answer) or OLDEST.search(answer) or re.search(r"\b(?:top|first|1st)\b", answer, re.I):
        return choices[0]
    if re.search(r"\b(?:last|bottom)\b", answer, re.I):
        return choices[-1]
    filler = {"the", "a", "an", "one", "this", "that", "video", "videos", "file", "files", "named", "called",
              "please", "edit", "open", "play", "show", "mp4", "mov", "mkv", "webm"}
    words = [w for w in re.findall(r"[a-z0-9]+", answer.lower()) if w not in filler and len(w) >= 2]
    if not words:
        words = [w for w in re.findall(r"[a-z0-9]+", answer.lower()) if w not in filler]
    if not words:
        return None
    # 1. Exact or all-token match against clean basename
    for c in choices:
        base_clean = re.sub(r"[\W_]+", " ", os.path.splitext(os.path.basename(c))[0].lower())
        if all(w in base_clean for w in words):
            return c
    # 2. Overlap scoring for typos or partial names
    scored = []
    for c in choices:
        base_clean = re.sub(r"[\W_]+", " ", os.path.splitext(os.path.basename(c))[0].lower())
        c_tokens = set(base_clean.split())
        score = sum(2 if w in c_tokens else 1 if any(w in tok or tok in w for tok in c_tokens) else 0 for w in words)
        if score > 0:
            scored.append((score, c))
    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]
    return None


def play(text, session):
    """"Play my latest Obscura video": open the resolved video in the OS default player and say which one
    (checklist #171). Says so honestly when nothing matches instead of asking a folder question."""
    explicit = PATH.search(text)
    named = explicit.group(0) if explicit else (VIDEO_FILE.findall(text) or [""])[0].strip()
    src = find_video(named)
    if not src:
        vids = recent_videos(text)
        src = vids[0] if vids else None
    if not src:
        return {"reply": "I couldn't find that video in Downloads, Videos or Desktop. Give me its name or path.", "sources": []}
    from ishti import test_mode
    if test_mode.enabled():
        return {"reply": f"[test-mode] Would play {os.path.basename(src)} ({src}).", "sources": []}
    try:
        os.startfile(src)  # default player; no shell involved
    except OSError as exc:
        return {"reply": f"I found {src} but couldn't play it: {exc}", "sources": []}
    return {"reply": f"Playing {os.path.basename(src)} ({src}).", "sources": []}


def runtime(text, session):
    """"What's the total runtime of all videos in my Obscura folder?": a real sum over the actual files
    (checklist #175), with an honest "no video files" when there are none."""
    vids = recent_videos(text, n=200)
    if not vids:
        return {"reply": "No video files found in that folder, so there's nothing to add up.", "sources": []}
    from tools.video.video_editor import get_video_editor
    ed = get_video_editor()
    total, counted, skipped = 0.0, 0, 0
    for v in vids[:80]:
        d = (ed.get_media_info(v) or {}).get("duration_seconds")
        try:
            total += float(d)
            counted += 1
        except (TypeError, ValueError):
            skipped += 1
    h, rem = divmod(int(total), 3600)
    m, s = divmod(rem, 60)
    label = f"{h} h {m} min" if h else (f"{m} min {s} s" if m else f"{s} s")
    note = f" ({skipped} file(s) had no readable duration)" if skipped else ""
    return {"reply": f"{counted} video(s): total runtime {label} ({int(total)} s){note}.", "sources": []}


def run(text, session):
    pend = session.get("pending")
    if pend and pend.get("skill") == "video":
        if pend.get("choices"):  # answer to "which video?"
            src = pick(text, pend["choices"])
            if not src:
                # If they cancelled
                if re.search(r"\b(?:cancel|stop|nevermind|forget\s+it|no)\b", text, re.I):
                    session["pending"] = None
                    return {"reply": "Cancelled video edit.", "sources": []}
                # Check if it's an explicit path or a new file search on PC
                src = find_video(text)
                if not src:
                    # Keep choices pending and ask clearly instead of falling through to web search
                    return {"reply": f"I couldn't match '{text}' to a video. Please reply with a number (1-{len(pend['choices'])}) or video name, or say cancel.", "sources": []}
            session["pending"] = None
            return edit(pend["request"], session, src)
        session["pending"] = None
        if NEWEST.search(text) or OLDEST.search(text) or VIDEO_FILE.search(text) or PATH.search(text):
            return edit(text, session)  # a new video request, not the answer to "what edit?"
        if not (EDIT_VERB.search(text) or ANSWER_EDIT.search(text) or PRO.search(text)):
            return None  # moved on to something else
        return edit(f"{text} ({os.path.basename(pend['file'])})", session, pend["file"])  # answer to "what edit?"
    if PLAY.search(text):
        return play(text, session)
    if RUNTIME.search(text) and not EDIT_VERB.search(text):  # a total, not an edit
        return runtime(text, session)
    return edit(text, session)


def edit(text, session, src=None):
    from tools.video.video_editor import get_video_editor
    p, ed = plan(text), get_video_editor()
    explicit = PATH.search(text)  # a full path in the request wins over the model's reading of it
    named = explicit.group(0) if explicit else (VIDEO_FILE.findall(text) or [""])[0].strip()
    if not named and VIDEO_FILE.fullmatch(str(p.get("input_file") or "")):
        named = p["input_file"]  # only a real file name: the model's "videos" would start a whole-PC search
    # An explicit full path that isn't there is a real error, not something to paper over with a
    # search of Downloads/Videos/Desktop (checklist #172): say exactly which file is missing.
    if explicit and not os.path.isfile(named):
        return {"reply": f"I can't find that file: {named} - no such file (or the path is wrong). "
                         "Give me the correct path and I'll run the edit.", "sources": []}
    src = src or find_video(named)
    # If no source was found by name/path, check if we can reuse the previous video from this session
    if not src and not named and session.get("last_video") and os.path.isfile(session["last_video"]):
        src = session["last_video"]
        trace(session, "reuse", f"reusing previous video: {os.path.basename(src)}")
    if not src:
        vids = recent_videos(text)
        if vids and (NEWEST.search(text) or OLDEST.search(text)):
            src = vids[0] if NEWEST.search(text) else vids[-1]
            trace(session, "pick", f"{'newest' if NEWEST.search(text) else 'oldest'} video: {os.path.basename(src)}")
        elif vids:
            trace(session, "scan", f"{len(vids)} recent videos found")
            session["pending"] = {"skill": "video", "choices": vids, "request": text}
            return {"reply": "Which video?\n" + "\n".join(f"{i}. {os.path.basename(v)}" for i, v in enumerate(vids, 1))
                             + "\nReply with a number or name.", "sources": []}
        else:
            return {"reply": "I didn't find any videos in Downloads, Videos or Desktop. Give me the file's name or path.",
                    "sources": []}
    if src:
        session["last_video"] = src
    if PRO.search(text):
        from ishti.skills.video import pro
        trace(session, "plan", f"editor's pass on {os.path.basename(src)}: copy, grade, cards, captions, CapCut, checks")
        return {"reply": pro.make(src, text, session), "sources": []}
    trace(session, "plan", f"{p['operation'].replace('_', ' ')} on {os.path.basename(src)}")
    if p["operation"] == "info" and not INFO.search(text):  # "edit that" / a bare path: ask what to do
        session["pending"] = {"skill": "video", "file": src}
        info = ed.get_media_info(src)
        about = f" ({info.get('duration_seconds')} s, {info.get('width')}x{info.get('height')})" if "duration_seconds" in info else ""
        return {"reply": f"What should I do with {os.path.basename(src)}{about}? For example: {EDITS}.", "sources": []}
    op = p["operation"]
    res = {"trim": lambda: ed.trim_video(src, start_time=p.get("start_time") or "00:00:00", end_time=p.get("end_time")),
           "extract_audio": lambda: ed.extract_audio(src),
           "convert_vertical": lambda: ed.convert_to_vertical(src),
           "change_speed": lambda: ed.change_speed(src, speed_factor=float(p.get("speed") or 2.0)),
           "compress": lambda: ed.compress_video(src, target_size_mb=float(p.get("target_size_mb") or 25.0)),
           "thumbnail": lambda: ed.extract_thumbnail(src, timestamp=p.get("start_time") or "00:00:01"),
           "text_overlay": lambda: ed.add_text_overlay(src, str(p.get("overlay_text") or "ISHTI"),
                                                       position=p.get("position") or "bottom", font_size=48),
           "info": lambda: ed.get_media_info(src)}
    trace(session, "ffmpeg", f"{op.replace('_', ' ')} with FFmpeg{' (NVENC GPU)' if ed.has_nvenc else ''}", status="start")
    res = res[op]()
    trace(session, "ffmpeg", f"{op.replace('_', ' ')} finished", status="done" if res.get("status") == "success" or op == "info" else "error")
    if op == "info" and "duration_seconds" in res:
        return {"reply": f"{os.path.basename(src)}: {res.get('duration_seconds')} s, "
                         f"{res.get('width')}x{res.get('height')}, {res.get('video_codec', '')}", "sources": []}
    if res.get("status") != "success":
        return {"reply": f"{op} failed: {res.get('error', 'FFmpeg error')}", "sources": []}
    return {"reply": f"Done ({op}{', GPU' if ed.has_nvenc else ''}): {res.get('output_path')}", "sources": []}


if __name__ == "__main__":
    assert match("trim the video clip")
    assert match("cut the video in my downloads")
    assert not match("create a capcut project from my clips")
    print("[self-check] video OK - routing")
