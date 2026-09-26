"""Video skill: the local LLM turns the request into one FFmpeg operation (JSON), then
tools/video/video_editor.py runs it (NVENC when available). Outputs are new files; nothing is overwritten."""
import json
import os
import re

from ishti import llm
from orchestrator.intent_detector import IntentDetector

OPS = ("trim", "extract_audio", "convert_vertical", "change_speed", "compress", "thumbnail", "info")


def match(text):
    return IntentDetector.classify(text)["mode"] == "VIDEO_EDIT"


def plan(text):
    out = llm.ask(
        "Turn this video request into JSON with keys: operation (one of " + ", ".join(OPS) + "), "
        "input_file (path or file name as written, or \"\"), start_time and end_time (HH:MM:SS or null), "
        f"speed (number or null), target_size_mb (number or null).\nRequest: {text}",
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


def run(text, session):
    from tools.video.video_editor import get_video_editor
    p, ed = plan(text), get_video_editor()
    src = find_video(p.get("input_file") or (re.findall(r"[\w\-. \\/:]+\.(?:mp4|mov|mkv|avi|webm)", text, re.I) or [""])[0])
    if not src:
        return {"reply": f"Which video? I couldn't find '{p.get('input_file') or 'the file'}'. Give me its name or path.",
                "sources": []}
    op = p["operation"]
    res = {"trim": lambda: ed.trim_video(src, start_time=p.get("start_time") or "00:00:00", end_time=p.get("end_time")),
           "extract_audio": lambda: ed.extract_audio(src),
           "convert_vertical": lambda: ed.convert_to_vertical(src),
           "change_speed": lambda: ed.change_speed(src, speed_factor=float(p.get("speed") or 2.0)),
           "compress": lambda: ed.compress_video(src, target_size_mb=float(p.get("target_size_mb") or 25.0)),
           "thumbnail": lambda: ed.extract_thumbnail(src, timestamp=p.get("start_time") or "00:00:01"),
           "info": lambda: ed.get_media_info(src)}[op]()
    if op == "info" and "duration_seconds" in res:
        return {"reply": f"{os.path.basename(src)}: {res.get('duration_seconds')} s, "
                         f"{res.get('width')}x{res.get('height')}, {res.get('video_codec', '')}", "sources": []}
    if res.get("status") != "success":
        return {"reply": f"{op} failed: {res.get('error', 'FFmpeg error')}", "sources": []}
    return {"reply": f"Done ({op}{', GPU' if ed.has_nvenc else ''}): {res.get('output_path')}", "sources": []}
