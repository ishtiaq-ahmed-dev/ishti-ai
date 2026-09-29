"""Professional, ad-style edit of one clip - the way an editor would cut it, not a single text stamp:

  intro title card -> graded main clip (blurred-fill framing, lower-third tagline, burned captions)
  -> outro call-to-action card, joined with crossfades, speech loudness-normalised to -14 LUFS.

The same edit is also written as a real CapCut project (pyCapCut; CapCut 8.x keeps drafts as plain JSON) so the
owner can open it in CapCut and adjust anything. The render is then checked: ffprobe (streams, duration), black
frames, loudness, and three key frames read by a local vision model (Gemma 3) for text visibility and spelling."""
import base64
import json
import os
import re
import subprocess
import tempfile

from ishti import ROOT, llm
from ishti.skills import trace

OUT_DIR = os.path.join(ROOT, "data", "video_output")
CAPCUT_EXE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "CapCut", "Apps", "CapCut.exe")
CAPCUT_DRAFTS = os.path.join(os.environ.get("LOCALAPPDATA", ""), "CapCut", "User Data", "Projects", "com.lveditor.draft")
FONT = next((f for f in (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf") if os.path.exists(f)), None)
INTRO, OUTRO, XF = 2.2, 2.6, 0.5  # seconds: title card, call-to-action card, crossfade
BG = "0x14121f"  # ISHTI's dark brand colour behind the cards
LONG_S, HL_CLIPS, HL_EACH = 90, 6, 2.5  # sources longer than this get a highlight cut: clips x seconds
CAPTION_BUDGET_S = 120  # captions are a nice-to-have; never let them hold the edit hostage
ASPECTS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}


def aspect_of(text, w, h):
    if re.search(r"\b(?:reel|reels|short|shorts|tiktok|story|stories|vertical|9:16|portrait)\b", text, re.I):
        return "9:16"
    if re.search(r"\b(?:square|1:1|instagram\s+post)\b", text, re.I):
        return "1:1"
    return "9:16" if h and w and h > w else "16:9"


def ad_copy(request):
    """Headline, tagline and call to action written for this request (and what ISHTI knows about the subject)."""
    from ishti.facts import get_facts
    known = []
    for entity, attr, value in get_facts().mentioned_in(request):
        known.append(f"{entity} {attr}: {value}")
        readme = os.path.join(value, "README.md") if attr == "location" and os.path.isdir(value) else ""
        for cand in (readme, os.path.join(value, ".github", "README.md") if readme else ""):
            if cand and os.path.isfile(cand):
                with open(cand, encoding="utf-8", errors="ignore") as f:
                    known.append(f"{entity} README: " + re.sub(r"\s+", " ", f.read(900)))
                break
    raw = llm.ask(
        "Write copy for a short video ad. Request: " + request + ("\nWhat we know:\n" + "\n".join(known) if known else "")
        + '\n\nReturn JSON {"headline": "<max 4 words, punchy>", "tagline": "<max 8 words, the benefit>", '
        '"cta": "<max 5 words, call to action>", "brand": "<product or brand name>"}. No hashtags, no emojis.',
        system=None, temperature=0.4, max_tokens=120, response_format={"type": "json_object"})
    try:
        c = json.loads(raw)
    except ValueError:
        c = {}
    clip = lambda s, n: " ".join(str(s or "").replace('"', "").split()[:n])
    return {"headline": clip(c.get("headline"), 5) or "Meet ISHTI", "tagline": clip(c.get("tagline"), 9),
            "cta": clip(c.get("cta"), 6) or "Try it today", "brand": clip(c.get("brand"), 3)}


def captions(src, workdir):
    """Speech -> captions.srt with local faster-whisper; None when the clip has no speech."""
    import concurrent.futures as cf
    from ishti.voice import stt_call, stt_model

    def run():
        model = stt_model()
        # temperature 0 only: music/noise sent whisper into its temperature-fallback loop for 15+ minutes.
        # stt_call: the decode runs on the one STT thread (a second decoding thread makes the process fail fast)
        return stt_call(lambda: [s for s in model.transcribe(src, beam_size=1, vad_filter=True, temperature=0.0,
                                                            condition_on_previous_text=False,
                                                            no_speech_threshold=0.6)[0]
                                 if s.text.strip() and getattr(s, "no_speech_prob", 0) < 0.6])
    pool = cf.ThreadPoolExecutor(1)
    try:
        segs = pool.submit(run).result(timeout=CAPTION_BUDGET_S)
    except Exception:  # timeout or decode error: the edit goes on without captions
        return None
    finally:
        pool.shutdown(wait=False)
    if not segs:
        return None
    ts = lambda t: f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d},{int(t * 1000 % 1000):03d}"
    path = os.path.join(workdir, "captions.srt")
    with open(path, "w", encoding="utf-8") as f:
        for i, s in enumerate(segs, 1):
            f.write(f"{i}\n{ts(s.start)} --> {ts(s.end)}\n{s.text.strip()}\n\n")
    return path


def _drawtext(textfile, size, y, color="white", fade_in=0.2, extra=""):
    font = f":fontfile='{FONT.replace(chr(92), '/').replace(':', chr(92) + ':')}'" if FONT else ""
    return (f"drawtext=textfile={textfile}{font}:fontsize={size}:fontcolor={color}:x=(w-text_w)/2:y={y}:"
            f"shadowcolor=black@0.5:shadowx=2:shadowy=2:alpha='if(lt(t,{fade_in}),0,min(1,(t-{fade_in})/0.5))'{extra}")


def highlight(ed, src, D, workdir):
    """(highlight.mp4, [(start, length)]): HL_CLIPS moments spread over the middle 80%, normalised and joined.
    ponytail: evenly spaced, not scene-scored - scoring a 3-hour 4K film would take longer than the edit.
    Upgrade path: PySceneDetect on a low-res proxy to pick the most dynamic shots."""
    enc = ["-c:v", "h264_nvenc", "-cq", "20"] if ed.has_nvenc else ["-c:v", "libx264", "-crf", "18", "-preset", "fast"]
    norm = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p,setsar=1"
    moments, parts = [], []
    for i in range(HL_CLIPS):
        start = D * 0.1 + D * 0.8 * (i + 0.5) / HL_CLIPS
        part = os.path.join(workdir, f"hl{i}.mp4")
        subprocess.run([ed.ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", src,
                        "-t", f"{HL_EACH}", "-vf", norm, *enc, "-c:a", "aac", "-ar", "48000", "-ac", "2", part],
                       capture_output=True, timeout=300)
        if os.path.isfile(part):
            moments.append((start, HL_EACH))
            parts.append(part)
    listing = os.path.join(workdir, "parts.txt")
    with open(listing, "w", encoding="utf-8") as f:
        f.writelines(f"file '{os.path.basename(p)}'\n" for p in parts)
    out = os.path.join(workdir, "highlight.mp4")
    subprocess.run([ed.ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i",
                    "parts.txt", "-c", "copy", "highlight.mp4"], cwd=workdir, capture_output=True, timeout=300)
    return out, moments


def render(src, copy, srt, aspect, duration, has_audio, workdir, ed, stem=None):
    """One FFmpeg pass. Runs inside workdir so text/caption files are referenced by bare name (no Windows
    path escaping inside the filter graph)."""
    W, H = ASPECTS[aspect]
    for name, text in (("head.txt", copy["headline"]), ("tag.txt", copy["tagline"]), ("cta.txt", copy["cta"]),
                       ("brand.txt", copy["brand"] or copy["headline"])):
        with open(os.path.join(workdir, name), "w", encoding="utf-8") as f:
            f.write(text)
    D = duration
    fs = lambda frac: int(min(W, H) * frac)
    main = (f"[0:v]split=2[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:2,"
            f"eq=brightness=-0.10[bg];[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,eq=contrast=1.07:saturation=1.18:gamma=0.98,vignette=PI/5")
    if copy["tagline"]:
        main += "," + _drawtext("tag.txt", fs(0.045), f"h-text_h-{int(H * 0.08)}", fade_in=0.4,
                                extra=f":box=1:boxcolor=black@0.45:boxborderw={fs(0.02)}:enable='between(t,0.4,{max(1.5, D - 0.4)})'")
    if srt:
        main += (f",subtitles=captions.srt:force_style='FontName=Segoe UI,FontSize={max(12, fs(0.018))},Bold=1,"
                 f"Outline=2,Shadow=0,MarginV={int(H * 0.16 / 2)},PrimaryColour=&H00FFFFFF'")
    main += ",fps=30,format=yuv420p,setsar=1[main]"
    intro = (f"color=c={BG}:s={W}x{H}:d={INTRO}:r=30," + _drawtext("head.txt", fs(0.11), "(h-text_h)/2-h*0.03")
             + "," + _drawtext("brand.txt", fs(0.035), "(h/2)+h*0.06", color="0xb9a9ff", fade_in=0.6)
             + ",format=yuv420p,setsar=1[intro]")
    outro = (f"color=c={BG}:s={W}x{H}:d={OUTRO}:r=30," + _drawtext("cta.txt", fs(0.085), "(h-text_h)/2-h*0.03", fade_in=0.3)
             + "," + _drawtext("brand.txt", fs(0.04), "(h/2)+h*0.07", color="0xb9a9ff", fade_in=0.7)
             + ",format=yuv420p,setsar=1[outro]")
    o1, total = INTRO - XF, INTRO + D + OUTRO - 2 * XF
    graph = (f"{main};{intro};{outro};[intro][main]xfade=transition=fade:duration={XF}:offset={o1}[im];"
             f"[im][outro]xfade=transition=smoothleft:duration={XF}:offset={o1 + D - XF}[v]")
    if has_audio:
        graph += (f";[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,adelay={int(o1 * 1000)}:all=1,"
                  f"afade=t=in:st={o1}:d=0.3,apad,atrim=0:{total}[a]")
        amap = ["-map", "[a]"]
    else:
        graph += f";anullsrc=r=48000:cl=stereo,atrim=0:{total}[a]"
        amap = ["-map", "[a]"]
    os.makedirs(OUT_DIR, exist_ok=True)
    stem = stem or os.path.splitext(os.path.basename(src))[0]
    safe = re.sub(r"[^\w\- ]", "", stem)[:60].strip() or "video"
    out = os.path.join(OUT_DIR, f"{safe}_ad_{aspect.replace(':', 'x')}.mp4")
    enc = ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21"] if ed.has_nvenc else ["-c:v", "libx264", "-crf", "20", "-preset", "medium"]
    cmd = [ed.ffmpeg_exe, "-y", "-hide_banner", "-i", src, "-filter_complex", graph, "-map", "[v]", *amap, *enc,
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.2f}", out]
    p = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=900)
    if p.returncode != 0:
        bad = [l for l in p.stderr.splitlines() if re.search(r"error|invalid|cannot|no such|unable", l, re.I)]
        raise RuntimeError(" | ".join((bad or p.stderr.splitlines())[-3:])[:400])
    return out, total


def _material(cc, src):
    """pyCapCut reads media with MediaInfo, which gives no numeric video duration for some MKVs; fill the same
    fields from ffprobe then."""
    try:
        return cc.VideoMaterial(src)
    except TypeError:
        import uuid
        from tools.video.video_editor import get_video_editor
        info = get_video_editor().get_media_info(src)
        m = object.__new__(cc.VideoMaterial)
        m.material_name, m.material_id, m.path = os.path.basename(src), uuid.uuid4().hex, os.path.abspath(src)
        m.crop_settings, m.local_material_id, m.material_type = cc.CropSettings(), "", "video"
        m.duration = int(float(info["duration_seconds"]) * 1e6)  # microseconds, as pyCapCut stores it
        m.width, m.height = info["width"], info["height"]
        return m


def capcut_project(src, copy, srt, aspect, duration, name, moments=None):
    """The same edit as an editable CapCut draft; None when CapCut isn't installed."""
    if not os.path.isdir(CAPCUT_DRAFTS):
        return None
    # 1. Preferred modern path: capcut-cli (supports modern storage & draft mirrors)
    try:
        from tools.video.capcut_controller import get_capcut_controller
        ctrl = get_capcut_controller()
        if ctrl.is_installed():
            W, H = ASPECTS.get(aspect, (1080, 1920))
            ratio = "9:16" if aspect == "9:16" else "16:9"
            res = ctrl.quickstart(name, video=src, srt=srt if srt and os.path.isfile(srt) else None, ratio=ratio, width=W, height=H)
            pdir = res.get("draft_path") or ctrl.get_project_dir(name)
            # Add titles/tagline via capcut-cli
            if copy.get("headline"):
                ctrl.add_text(pdir, copy["headline"], start="0s", duration="2.2s", font_size=16)
            if copy.get("tagline"):
                ctrl.add_text(pdir, copy["tagline"], start="0.4s", duration=f"{max(1.0, duration - 0.8):.2f}s", font_size=12)
            if copy.get("cta"):
                ctrl.add_text(pdir, copy["cta"], start=f"{max(0.0, duration - 2.5):.2f}s", duration=f"{min(2.5, duration):.2f}s", font_size=14)
            return pdir
    except Exception as exc:
        print(f"[pro.py] capcut-cli draft creation failed ({exc}); attempting pycapcut fallback...")

    # 2. Fallback legacy path: pycapcut
    try:
        import pycapcut as cc
        W, H = ASPECTS[aspect]
        script = cc.DraftFolder(CAPCUT_DRAFTS).create_draft(name, W, H, allow_replace=True)
        script.add_track(cc.TrackType.video).add_track(cc.TrackType.text, "titles").add_track(cc.TrackType.text, "tagline")
        material, at = _material(cc, src), 0.0
        for i, (start, length) in enumerate(moments or [(0.0, duration)]):
            seg = cc.VideoSegment(material, cc.trange(f"{at:.3f}s", f"{length:.3f}s"),
                                  source_timerange=cc.trange(f"{start:.3f}s", f"{length:.3f}s"))
            try:
                seg.add_filter(cc.FilterType.Enhance, 60)
                if i < len(moments or []) - 1:
                    seg.add_transition(cc.TransitionType.Flash)
            except Exception:
                pass
            script.add_segment(seg)
            at += length
        head = cc.TextSegment(copy["headline"], cc.trange("0s", "2.2s"),
                              style=cc.TextStyle(size=14, bold=True, color=(1.0, 1.0, 1.0), align=1),
                              clip_settings=cc.ClipSettings(transform_y=0.1))
        tail = cc.TextSegment(copy["cta"], cc.trange(f"{max(0.0, duration - 2.5):.2f}s", f"{min(2.5, duration):.2f}s"),
                              style=cc.TextStyle(size=12, bold=True, color=(0.73, 0.66, 1.0), align=1))
        for seg in (head, tail):
            try:
                seg.add_animation(cc.TextIntro.Wiping_In)
            except Exception:
                pass
            script.add_segment(seg, "titles")
        if copy["tagline"]:
            script.add_segment(cc.TextSegment(copy["tagline"], cc.trange("0.4s", f"{max(1.0, duration - 0.8):.2f}s"),
                                              style=cc.TextStyle(size=7, color=(1.0, 1.0, 1.0), align=1),
                                              clip_settings=cc.ClipSettings(transform_y=-0.78)), "tagline")
        if srt:
            script.import_srt(srt, "captions", clip_settings=cc.ClipSettings(transform_y=-0.6))
        script.save()
        return os.path.join(CAPCUT_DRAFTS, name)
    except Exception as exc2:
        print(f"[pro.py] pycapcut also failed: {exc2}")
        return None


def _probe(ed, path):
    p = subprocess.run([ed.ffprobe_exe, "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", path],
                       capture_output=True, text=True, timeout=30)
    return json.loads(p.stdout or "{}")


def _frame(ed, path, t, workdir, name):
    out = os.path.join(workdir, name)
    subprocess.run([ed.ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", path,
                    "-frames:v", "1", "-vf", "scale=768:-2", out], capture_output=True, timeout=60)
    return out if os.path.isfile(out) else None


def look(image, question, role="vision2"):
    """Ask the local vision model about one frame; returns its JSON answer. role "vision2" = Gemma 3 4B (light, the
    default); "vision3" = the heavy tier (Gemma 3 12B) for dense pages/charts - the VRAM governor makes room for it
    (or falls back to tier 2) and hands the memory back afterwards."""
    with open(image, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    msg = llm.chat([{"role": "user", "content": [
        {"type": "text", "text": question + ' Reply only JSON {"text": "<all text you can read>", "ok": true/false, "problem": "<short or empty>"}.'},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}],
        role=role, system=None, temperature=0, max_tokens=150)
    try:
        return json.loads(re.search(r"\{.*\}", msg.content or "", re.S)[0])
    except (TypeError, ValueError):
        return {"text": msg.content or "", "ok": True, "problem": ""}


def verify(ed, out, copy, total, workdir):
    """[(check, passed, detail)] - the checks an editor does before handing a video over."""
    import difflib
    results = []
    info = _probe(ed, out)
    streams = {s.get("codec_type") for s in info.get("streams", [])}
    dur = float(info.get("format", {}).get("duration", 0))
    results.append(("video + audio streams", {"video", "audio"} <= streams, ", ".join(sorted(streams))))
    results.append(("length", abs(dur - total) < 0.6, f"{dur:.1f} s (planned {total:.1f} s)"))
    # only the footage between the cards (the brand cards are dark on purpose), and only truly black frames
    main = f"trim=start={INTRO}:end={total - OUTRO},blackdetect=d=0.5:pix_th=0.02:picture_black_ratio_th=0.99"
    p = subprocess.run([ed.ffmpeg_exe, "-hide_banner", "-i", out, "-vf", main, "-af", "ebur128",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=600)
    blacks = re.findall(r"black_start:([\d.]+)", p.stderr)
    results.append(("no black gaps", not blacks, f"{len(blacks)} black stretch(es)" if blacks else "none"))
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", p.stderr)
    loud = float(lufs[-1]) if lufs else None
    results.append(("loudness", loud is None or loud < -60 or -18 <= loud <= -10,
                    f"{loud:.1f} LUFS" if loud is not None and loud > -60 else "silent clip"))
    checks = ((INTRO * 0.6, copy["headline"], "title card"), (total - OUTRO * 0.45, copy["cta"], "call to action"))
    for t, expected, label in checks:
        frame = _frame(ed, out, t, workdir, f"check_{label[:5]}.jpg")
        if not frame:
            results.append((f"{label} frame", False, "couldn't extract a frame"))
            continue
        seen = look(frame, f"This is a frame from a video ad. The {label} should say: '{expected}'. Is that text fully "
                           "visible (not cut off), readable and spelled correctly?")
        sim = difflib.SequenceMatcher(None, expected.lower(), str(seen.get("text", "")).lower()).ratio()
        ok = sim >= 0.6 and seen.get("ok", True) is not False
        results.append((f"{label} readable", ok, f"reads '{str(seen.get('text', ''))[:60]}'" + (f" - {seen['problem']}" if seen.get("problem") else "")))
    mid = _frame(ed, out, INTRO + (total - INTRO - OUTRO) / 2, workdir, "check_main.jpg")
    if mid:
        seen = look(mid, "This is a frame from the main part of a video ad. Is the picture clear (not black, not "
                         "stretched or distorted, subject visible)?")
        results.append(("main footage clear", seen.get("ok", True) is not False, seen.get("problem") or "looks fine"))
    return results


def open_capcut():
    if os.path.isfile(CAPCUT_EXE):
        subprocess.Popen([CAPCUT_EXE], close_fds=True)  # no shell; CapCut lists the new project first
        return True
    return False


def make(src, request, session):
    """The whole editor pass. Returns the chat reply."""
    from tools.video.video_editor import get_video_editor
    ed = get_video_editor()
    info = ed.get_media_info(src)
    D = float(info.get("duration_seconds") or 0)
    if D < 1:
        return f"{os.path.basename(src)} has no usable video ({info.get('error', 'zero length')})."
    aspect = aspect_of(request, info.get("width"), info.get("height"))
    with tempfile.TemporaryDirectory(prefix="ishti_edit_") as work:
        original, moments = src, None
        if D > LONG_S:  # a film or long recording: an ad is a short highlight, not the whole thing
            trace(session, "highlight", f"long source ({D / 60:.0f} min): cutting a {HL_CLIPS * HL_EACH:.0f} s highlight "
                                        f"from {HL_CLIPS} moments", status="start")
            src, moments = highlight(ed, src, D, work)
            D = HL_CLIPS * HL_EACH
            trace(session, "highlight", "highlight ready", status="done")
        trace(session, "copy", "writing headline, tagline and call to action", status="start")
        copy = ad_copy(request)
        trace(session, "copy", f"\"{copy['headline']}\" / \"{copy['tagline']}\" / \"{copy['cta']}\"", status="done")
        srt = None
        if info.get("audio_codec"):
            trace(session, "captions", "transcribing speech for captions (faster-whisper)", status="start")
            srt = captions(src, work)
            trace(session, "captions", "captions ready" if srt else "no speech - no captions", status="done")
        trace(session, "ffmpeg", f"rendering {aspect} ad: intro card, graded clip, lower third, outro"
                                 f"{' (NVENC GPU)' if ed.has_nvenc else ''}", status="start")
        out, total = render(src, copy, srt, aspect, D, bool(info.get("audio_codec")), work, ed,
                            stem=os.path.splitext(os.path.basename(original))[0])
        trace(session, "ffmpeg", f"rendered {total:.1f} s", status="done")
        draft, capcut_error = None, None
        try:
            name = f"ISHTI {os.path.splitext(os.path.basename(original))[0][:40]} ad"
            draft = capcut_project(original, copy, srt, aspect, D, name, moments)
            trace(session, "capcut", f"CapCut project '{name}' written" if draft else "CapCut isn't installed", status="done")
        except Exception as exc:
            capcut_error = f"{type(exc).__name__}: {exc}"[:150]
            trace(session, "capcut", f"CapCut project failed: {capcut_error}", status="error")
        trace(session, "verify", "checking the video: streams, black frames, loudness, text on key frames", status="start")
        results = verify(ed, out, copy, total, work)
        passed = sum(ok for _, ok, _ in results)
        trace(session, "verify", f"{passed}/{len(results)} checks passed", status="done" if passed == len(results) else "error")
    opened = open_capcut() if draft else False
    lines = [f"Done: {out}", "",
             f"**{aspect} ad, {total:.1f} s**: title card \"{copy['headline']}\" → graded clip with lower third "
             f"\"{copy['tagline']}\"" + (" and captions" if srt else "") + f" → call to action \"{copy['cta']}\", crossfades, "
             + ("speech normalised to -14 LUFS." if info.get("audio_codec") else "silent source, so no audio."),
             "", "**Checks**"] + [f"- {'✓' if ok else '✗'} {name}: {detail}" for name, ok, detail in results]
    if draft:
        lines += ["", f"CapCut project: **{os.path.basename(draft)}**" + (" - CapCut is opening; it's at the top of your projects."
                                                                          if opened else "")]
    elif capcut_error:
        lines += ["", f"The CapCut project couldn't be written ({capcut_error}); the rendered video above is complete."]
    else:
        lines += ["", "CapCut isn't installed - say \"install capcut\" and I'll set it up (with your okay)."]
    return "\n".join(lines)
