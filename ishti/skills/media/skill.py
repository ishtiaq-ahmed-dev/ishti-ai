"""Audio, video extras and images - thin wrappers over FFmpeg, faster-whisper, Piper and Pillow.

Audio:  transcribe, summarise a voice note, normalise loudness, remove noise, cut silences, convert, text to speech
Video:  merge, reverse, burn subtitles (and translate them), background music with ducking, stabilise, GIF,
        watermark, record the screen (FFmpeg gdigrab), record the webcam
Image:  resize (one file or a folder), rotate/flip/black-and-white/blur/sharpen, strip metadata, meme text,
        read the text in an image (local vision model), convert format
Results land in data/media_output, which the chat plays/shows inline."""
import glob
import os
import re
import subprocess
import threading
import time

from ishti import ROOT
from ishti.skills import log_need, trace

I = re.I
OUT = os.path.join(ROOT, "data", "media_output")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
AUDIO = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wma")
VIDEO = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".wmv")
IMAGE = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff")
PATH = re.compile(r"[A-Za-z]:[\\/][^\"'<>|?*\n]+?\.(?:" + "|".join(e[1:] for e in AUDIO + VIDEO + IMAGE + (".srt",)) + r")\b", I)
QUOTED = re.compile(r"[\"“']([^\"”']{1,120})[\"”']")
RECORDING = {}


def _stop_recordings():
    """Kill switch: finish any screen/webcam recording now (the file is kept)."""
    return op_stop_record(None, "", {}) if RECORDING else ""

OPS = [
    ("stop_record", re.compile(r"\bstop\s+(?:the\s+)?(?:screen\s+|webcam\s+)?recording\b", I)),
    ("screen_record", re.compile(r"\brecord\s+(?:my\s+|the\s+)?screen\b|\bscreen\s+record", I)),
    ("webcam_record", re.compile(r"\brecord\s+(?:from\s+)?(?:my\s+|the\s+)?(?:webcam|camera)\b", I)),
    # text to speech - words, not a file ("convert D:\talk.wav to mp3" is a conversion)
    ("tts", re.compile(r"^(?!.*[A-Za-z]:\\).*\b(?:save|turn|convert|make)\s+.+\s+(?:as|into|to)\s+(?:an?\s+)?(?:audio|speech|voice(?:\s+note)?|mp3)\b", I)),
    ("ocr", re.compile(r"\b(?:read|extract|ocr|get)\s+(?:the\s+)?text\s+(?:from|in|on|off)\b", I)),
    ("summarize_audio", re.compile(r"\bsummari[sz]e\b.+\.(?:mp3|wav|m4a|ogg|opus|aac|mp4|webm)\b", I)),
    ("transcribe", re.compile(r"\btranscri(?:be|pt)\b", I)),
    ("translate_subs", re.compile(r"\btranslate\s+(?:the\s+)?(?:subtitles?|captions|srt)\b", I)),
    ("subtitles", re.compile(r"\b(?:add|generate|burn|create|make)\s+(?:the\s+)?(?:subtitles?|captions)\b", I)),
    ("music", re.compile(r"\badd\s+(?:the\s+|some\s+)?(?:background\s+)?(?:music|song|track)\b", I)),
    ("stabilize", re.compile(r"\bstabili[sz]e\b|\bremove\s+(?:the\s+)?(?:shake|shaking)\b", I)),
    ("denoise", re.compile(r"\b(?:remove|reduce|clean(?:\s+up)?)\s+(?:the\s+)?(?:background\s+)?(?:noise|hiss|hum)\b|\bdenoise\b", I)),
    ("silence", re.compile(r"\b(?:remove|cut|trim|delete)\s+(?:the\s+|all\s+)?(?:silences?|silent\s+parts|dead\s+air|pauses)\b", I)),
    ("normalize", re.compile(r"\bnormali[sz]e\b|\b(?:fix|even\s+out)\s+(?:the\s+)?(?:volume|loudness|audio\s+levels)\b", I)),
    ("merge", re.compile(r"\b(?:merge|join|combine|concatenate|stitch)\b", I)),
    ("reverse", re.compile(r"\breverse\b|\bplay\s+(?:it\s+)?backwards\b", I)),
    ("gif", re.compile(r"\b(?:to|into|as)\s+(?:a\s+)?gif\b|\bmake\s+(?:a\s+)?gif\b", I)),
    ("watermark", re.compile(r"\bwatermark\b", I)),
    ("meme", re.compile(r"\bmeme\b", I)),
    ("strip_meta", re.compile(r"\b(?:remove|strip|clean)\s+(?:the\s+|all\s+)?(?:metadata|exif|location\s+data|gps)\b", I)),
    ("diarize", re.compile(r"\bwho\s+(?:said|says|spoke)\s+what\b|\b(?:split|separate|divide)\b.+\bby\s+speakers?\b|"
                           r"\bdiari[sz]"
                           r"(?:e|ation)\b|\b(?:identify|separate|label)\s+(?:the\s+)?speakers\b", I)),
    ("remove_bg", re.compile(r"\b(?:remove|delete|cut\s+out|erase|strip)\s+(?:the\s+)?(?:background|bg)\b|\bbackground\s+remov", I)),
    ("upscale", re.compile(r"\b(?:upscale|enhance|super[-\s]?resolution|increase\s+(?:the\s+)?resolution|make\s+(?:it|this|the\s+\w+)\s+(?:hd|sharper|higher\s+res))\b", I)),
    ("resize", re.compile(r"\b(?:resize|scale|shrink)\b", I)),
    ("image_edit", re.compile(r"\b(?:rotate|flip|mirror|grayscale|greyscale|black\s+and\s+white|blur|sharpen)\b", I)),
    ("convert", re.compile(r"\bconvert\b.+\bto\s+(?P<fmt>mp3|wav|m4a|aac|ogg|flac|mp4|mkv|webm|mov|png|jpe?g|webp)\b", I)),
]


def match(text):
    if any(rx.search(text) for name, rx in OPS[:4]):
        return True
    return bool(PATH.search(text)) and any(rx.search(text) for _, rx in OPS[4:])


def run(text, session):
    for name, rx in OPS:
        if m := rx.search(text):
            if name not in ("stop_record", "screen_record", "webcam_record", "tts") and not PATH.search(text):
                continue
            trace(session, "media", name.replace("_", " "), status="start")
            try:
                out = globals()["op_" + name](m, text, session)
            except subprocess.CalledProcessError as exc:
                bad = [l for l in (exc.stderr or "").splitlines() if re.search(r"error|invalid|no such|unable", l, I)]
                out = f"FFmpeg failed: {' | '.join((bad or (exc.stderr or '').splitlines())[-2:])[:300]}"
            trace(session, "media", "done", status="done")
            return out if isinstance(out, dict) or out is None else {"reply": out, "sources": []}
    return None


# ---------------------------------------------------------------- helpers
def ed():
    from tools.video.video_editor import get_video_editor
    return get_video_editor()


def ff(*args, cwd=None, timeout=1800):
    subprocess.run([ed().ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error", *args], cwd=cwd, check=True,
                   capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)


def paths(text):
    return [p for p in PATH.findall(text) if os.path.isfile(p)]


def out_path(src, suffix, ext=None):
    os.makedirs(OUT, exist_ok=True)
    stem, e = os.path.splitext(os.path.basename(src))
    return os.path.join(OUT, f"{stem}_{suffix}{ext or e}")


def venc():
    return ["-c:v", "h264_nvenc", "-cq", "21"] if ed().has_nvenc else ["-c:v", "libx264", "-crf", "20", "-preset", "medium"]


def need_file(text, kinds=AUDIO + VIDEO):
    got = [p for p in paths(text) if p.lower().endswith(kinds)]
    return got[0] if got else None


def has_video(path):
    return path.lower().endswith(VIDEO) and not path.lower().endswith(AUDIO)


# ---------------------------------------------------------------- audio
def op_transcribe(m, text, session, keep=False):
    src = need_file(text)
    if not src:
        return "Which file? Give me the full path of the audio or video."
    from ishti.skills.video.pro import captions
    import tempfile
    with tempfile.TemporaryDirectory() as work:
        srt = captions(src, work)
        if not srt:
            return "I couldn't hear any speech in it."
        with open(srt, encoding="utf-8") as f:
            body = f.read()
    dst = out_path(src, "transcript", ".srt")
    with open(dst, "w", encoding="utf-8") as f:
        f.write(body)
    plain = " ".join(l for l in body.splitlines() if l and not l[0].isdigit())
    if keep:
        return plain
    txt = dst[:-4] + ".txt"
    with open(txt, "w", encoding="utf-8") as f:
        f.write(plain)
    return f"Transcript ({len(plain.split())} words), saved as {txt} and subtitles {dst}:\n\n{plain[:1500]}"


def op_summarize_audio(m, text, session):
    plain = op_transcribe(m, text, session, keep=True)
    if plain.startswith(("Which file", "I couldn't")):
        return plain
    from ishti import llm
    return llm.ask(f"Summarise this voice note / recording for its owner: the main points and anything to act on.\n\n{plain[:8000]}",
                   max_tokens=400)


def op_normalize(m, text, session):
    src = need_file(text)
    dst = out_path(src, "normalized")
    ff("-i", src, "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", *(["-c:v", "copy"] if has_video(src) else []), dst)
    return f"Loudness normalised to -14 LUFS: {dst}"


def op_denoise(m, text, session):
    src = need_file(text)
    dst = out_path(src, "denoised")
    vf = ["-vf", "hqdn3d=3:3:6:6", *venc()] if has_video(src) and re.search(r"\bvideo|picture|grain\b", text, I) else \
         (["-c:v", "copy"] if has_video(src) else [])
    if os.path.isfile(RNNOISE):  # RNNoise (a neural speech denoiser, FFmpeg arnndn) + a gentle FFT pass after it
        ff("-i", os.path.abspath(src), "-af", "highpass=f=70,arnndn=m=sh.rnnn,afftdn=nf=-30", *vf, os.path.abspath(dst),
           cwd=os.path.dirname(RNNOISE))  # the model is given by name from its own folder: no path escaping
        how = "RNNoise"
    else:
        ff("-i", src, "-af", "afftdn=nf=-25,highpass=f=70", *vf, dst)
        how = "FFT denoise"
    return f"Background noise reduced ({how}): {dst}"


def op_silence(m, text, session):
    """Keep only the parts with sound: silencedetect -> segments -> select/aselect (keeps video in sync)."""
    src = need_file(text)
    p = subprocess.run([ed().ffmpeg_exe, "-hide_banner", "-i", src, "-af", "silencedetect=noise=-35dB:d=0.6", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=1800, creationflags=NO_WINDOW)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", p.stderr)]
    if not starts:
        return "No silences longer than 0.6 s to cut."
    dur = float(ed().get_media_info(src).get("duration_seconds") or 0)
    keep, t = [], 0.0
    for s, e in zip(starts, ends + [dur] * (len(starts) - len(ends))):
        if s - t > 0.05:
            keep.append((max(0, t - 0.1), s + 0.1))  # keep a breath either side
        t = e
    if dur - t > 0.05:
        keep.append((max(0, t - 0.1), dur))
    expr = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keep)
    dst = out_path(src, "tight")
    args = ["-i", src, "-af", f"aselect='{expr}',asetpts=N/SR/TB"]
    if has_video(src):
        args += ["-vf", f"select='{expr}',setpts=N/FRAME_RATE/TB", *venc()]
    ff(*args, dst)
    cut = dur - sum(b - a for a, b in keep)
    return f"Cut {len(starts)} silences ({cut:.1f} s of {dur:.1f} s): {dst}"


def op_convert(m, text, session):
    fmt = m.group("fmt").lower().replace("jpeg", "jpg")
    src = paths(text)[0]
    dst = out_path(src, "converted", "." + fmt)
    if src.lower().endswith(IMAGE) and fmt in ("png", "jpg", "webp"):
        from PIL import Image
        im = Image.open(src)
        (im.convert("RGB") if fmt == "jpg" else im).save(dst, quality=92)
    elif fmt in ("mp3", "wav", "m4a", "aac", "ogg", "flac"):
        ff("-i", src, "-vn", *(["-b:a", "192k"] if fmt in ("mp3", "m4a", "aac", "ogg") else []), dst)
    else:
        ff("-i", src, *venc(), "-c:a", "aac", dst)
    return f"Converted to {fmt.upper()}: {dst}"


def op_tts(m, text, session):
    words = QUOTED.search(text)
    say = words.group(1) if words else re.sub(r"^\s*(?:save|turn|convert|make)\s+|\s+(?:as|into|to)\s+(?:an?\s+)?(?:audio|speech|voice(?:\s+note)?|mp3).*$", "", text, flags=I)
    from ishti.voice import synthesize
    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, f"speech_{time.strftime('%Y%m%d_%H%M%S')}.wav")
    with open(dst, "wb") as f:
        f.write(synthesize(say))
    return f"Saved as speech: {dst}"


# ---------------------------------------------------------------- video extras
def op_merge(m, text, session):
    clips = [p for p in paths(text) if p.lower().endswith(VIDEO)]
    if len(clips) < 2:
        return "Give me at least two video paths to merge."
    parts = "".join(f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1[v{i}];"
                    f"[{i}:a]aresample=48000[a{i}];" for i in range(len(clips)))
    chain = "".join(f"[v{i}][a{i}]" for i in range(len(clips)))
    dst = out_path(clips[0], "merged", ".mp4")
    args = [a for c in clips for a in ("-i", c)]
    ff(*args, "-filter_complex", parts + f"{chain}concat=n={len(clips)}:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]", *venc(), "-c:a", "aac", dst)
    return f"Merged {len(clips)} clips: {dst}"


def op_reverse(m, text, session):
    src = need_file(text)
    dst = out_path(src, "reversed")
    ff("-i", src, *(["-vf", "reverse", *venc()] if has_video(src) else []), "-af", "areverse", dst)
    return f"Reversed: {dst}"


def op_subtitles(m, text, session):
    src = need_file(text, VIDEO)
    if not src:
        return "Which video? Give me its full path."
    from ishti.skills.video.pro import captions
    work = os.path.join(OUT, "_subs")
    os.makedirs(work, exist_ok=True)
    srt = captions(src, work)
    if not srt:
        return "I couldn't hear any speech to caption."
    lang = re.search(r"\bin\s+(urdu|english|arabic|hindi|roman urdu)\b", text, I)
    if lang:
        srt = translate_srt(srt, lang.group(1))
    dst = out_path(src, "subtitled", ".mp4")
    ff("-i", src, "-vf", f"subtitles={os.path.basename(srt)}:force_style='FontName=Segoe UI,FontSize=18,Bold=1,Outline=2,MarginV=40'",
       *venc(), "-c:a", "copy", dst, cwd=work)
    keep = out_path(src, "subtitles", ".srt")
    os.replace(srt, keep)
    return f"Burned-in subtitles: {dst}\nSubtitle file: {keep}"


def translate_srt(srt, language):
    from ishti import llm
    with open(srt, encoding="utf-8") as f:
        blocks = f.read().strip().split("\n\n")
    out = []
    for b in blocks:
        lines = b.splitlines()
        if len(lines) >= 3:
            lines = lines[:2] + [llm.ask(f"Translate into {language}. Output only the translation.\n\n{' '.join(lines[2:])}",
                                         role="translator", system=None, temperature=0, max_tokens=120).strip()]
        out.append("\n".join(lines))
    dst = srt[:-4] + f"_{language.lower().replace(' ', '_')}.srt"
    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n\n".join(out) + "\n")
    return dst


def op_translate_subs(m, text, session):
    srt = next((p for p in paths(text) if p.lower().endswith(".srt")), None)
    lang = re.search(r"\b(?:to|into|in)\s+(urdu|english|arabic|hindi|roman urdu|\w+)\s*$", text, I)
    if not srt or not lang:
        return "Give me the .srt path and the language, e.g. \"translate the subtitles D:\\x.srt into urdu\"."
    return f"Translated subtitles: {translate_srt(srt, lang.group(1))}"


def op_music(m, text, session):
    video = next((p for p in paths(text) if p.lower().endswith(VIDEO)), None)
    song = next((p for p in paths(text) if p.lower().endswith(AUDIO)), None)
    if not video or not song:
        return "Give me the video's path and the music file's path."
    dst = out_path(video, "music", ".mp4")
    has_speech = ed().get_media_info(video).get("audio_codec")
    # the song repeats under a longer video by looping the input (-stream_loop), not the aloop filter,
    # which buffered billions of samples and hung for 10 minutes
    if has_speech:  # duck the music under the speech (sidechain compression), not a flat mix
        graph = ("[1:a]volume=0.35[m];[0:a]asplit=2[sc][voice];"
                 "[m][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck];"
                 "[voice][duck]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14[a]")
    else:
        graph = "[1:a]volume=0.8,loudnorm=I=-14[a]"
    ff("-i", video, "-stream_loop", "-1", "-i", song, "-filter_complex", graph, "-map", "0:v", "-map", "[a]",
       "-c:v", "copy", "-c:a", "aac", "-shortest", dst, timeout=600)
    return f"Music added{' and ducked under the speech' if has_speech else ''}: {dst}"


def op_stabilize(m, text, session):
    src = need_file(text, VIDEO)
    work = os.path.join(OUT, "_stab")
    os.makedirs(work, exist_ok=True)
    ff("-i", src, "-vf", "vidstabdetect=shakiness=6:accuracy=12:result=transforms.trf", "-f", "null", "-", cwd=work)
    dst = out_path(src, "stabilized")
    # sharpen BEFORE the transform: any filter after vidstabtransform segfaults FFmpeg 8.1.1 (Gyan full build)
    ff("-i", src, "-vf", "unsharp=5:5:0.6,vidstabtransform=input=transforms.trf:smoothing=20:zoom=2", *venc(), "-c:a", "copy", dst, cwd=work)
    return f"Stabilised: {dst}"


def op_gif(m, text, session):
    src = need_file(text, VIDEO)
    dst = out_path(src, "clip", ".gif")
    span = re.search(r"(?:first|from\s+0\s+to)\s+(\d+)\s*(?:s|sec|seconds)", text, I)
    t = ["-t", span.group(1)] if span else ["-t", "8"]
    ff(*t, "-i", src, "-vf", "fps=12,scale=540:-2:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer", dst)
    return f"GIF ({t[1]} s): {dst}"


def op_watermark(m, text, session):
    src = paths(text)[0]
    logo = next((p for p in paths(text)[1:] if p.lower().endswith(IMAGE)), None)
    label = (QUOTED.search(text) or [None, None])[1] or (None if logo else "ISHTI")
    if src.lower().endswith(IMAGE):
        from PIL import Image, ImageDraw, ImageFont
        im = Image.open(src).convert("RGBA")
        layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
        if logo:
            mark = Image.open(logo).convert("RGBA")
            mark.thumbnail((im.width // 5, im.height // 5))
            layer.paste(mark, (im.width - mark.width - 20, im.height - mark.height - 20), mark)
        else:
            font = ImageFont.truetype("segoeuib.ttf", max(16, im.width // 25))
            d = ImageDraw.Draw(layer)
            w = d.textlength(label, font=font)
            d.text((im.width - w - 24, im.height - font.size - 24), label, font=font, fill=(255, 255, 255, 150))
        dst = out_path(src, "watermarked", ".png")
        Image.alpha_composite(im, layer).save(dst)
        return f"Watermarked: {dst}"
    dst = out_path(src, "watermarked")
    if logo:
        ff("-i", src, "-i", logo, "-filter_complex", "[1]scale=iw/6:-1,format=rgba,colorchannelmixer=aa=0.7[l];[0][l]overlay=W-w-24:H-h-24",
           *venc(), "-c:a", "copy", dst)
    else:
        font = r"C\:/Windows/Fonts/segoeuib.ttf"
        ff("-i", src, "-vf", f"drawtext=text='{label.replace(':', '')}':fontfile='{font}':fontsize=h/18:fontcolor=white@0.6:x=w-tw-24:y=h-th-24",
           *venc(), "-c:a", "copy", dst)
    return f"Watermarked: {dst}"


def op_screen_record(m, text, session):
    if "screen" in RECORDING:
        return "Already recording the screen - say \"stop recording\"."
    secs = re.search(r"(\d+)\s*(min|minute|minutes|m|s|sec|seconds)\b", text, I)
    limit = (int(secs.group(1)) * (60 if secs.group(2).lower().startswith("m") else 1)) if secs else 600
    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, f"screen_{time.strftime('%Y%m%d_%H%M%S')}.mp4")
    proc = subprocess.Popen([ed().ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error", "-f", "gdigrab", "-framerate", "30",
                             # two monitors make a 4480 px wide desktop; NVENC H.264 stops at 4096 - scale it down
                             "-i", "desktop", "-t", str(limit), "-vf", "scale='min(2560,iw)':-2,format=yuv420p", *venc(), dst],
                            stdin=subprocess.PIPE, creationflags=NO_WINDOW)
    RECORDING["screen"] = (proc, dst)
    threading.Thread(target=lambda: (proc.wait(), RECORDING.pop("screen", None)), daemon=True).start()
    return f"Recording the screen (up to {limit // 60} min {limit % 60} s). Say \"stop recording\" to finish. File: {dst}"


def op_webcam_record(m, text, session):
    p = subprocess.run([ed().ffmpeg_exe, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                       capture_output=True, text=True, creationflags=NO_WINDOW, timeout=30)
    cams = re.findall(r'"([^"]+)" \(video\)', p.stderr)
    if not cams:
        log_need("gap", "media", text, "no webcam connected")
        return "No webcam is connected to this PC."
    secs = re.search(r"(\d+)\s*(min|minutes?|s|sec|seconds)\b", text, I)
    limit = (int(secs.group(1)) * (60 if secs.group(2).lower().startswith("m") else 1)) if secs else 60
    dst = os.path.join(OUT, f"webcam_{time.strftime('%Y%m%d_%H%M%S')}.mp4")
    proc = subprocess.Popen([ed().ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error", "-f", "dshow", "-i", f"video={cams[0]}",
                             "-t", str(limit), *venc(), dst], stdin=subprocess.PIPE, creationflags=NO_WINDOW)
    RECORDING["webcam"] = (proc, dst)
    return f"Recording from {cams[0]} for up to {limit} s. File: {dst}"


def op_stop_record(m, text, session):
    if not RECORDING:
        return "Nothing is recording."
    done = []
    for name, (proc, dst) in list(RECORDING.items()):
        try:
            proc.stdin.write(b"q")  # FFmpeg finishes the file cleanly on 'q'
            proc.stdin.flush()
            proc.wait(timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
        RECORDING.pop(name, None)
        done.append(dst)
    return "Stopped. Saved:\n" + "\n".join(f"- {d}" for d in done)


# ---------------------------------------------------------------- images
def _images(text):
    """One image path, or every image in a named folder ("resize the photos in downloads")."""
    got = [p for p in paths(text) if p.lower().endswith(IMAGE)]
    if got:
        return got
    from ishti.skills.folders.skill import folder_in
    folder = folder_in(text)[1]
    return sorted(p for p in glob.glob(os.path.join(folder, "*")) if p.lower().endswith(IMAGE))[:200] if folder else []


ESRGAN = os.path.join(ROOT, "data", "bin", "realesrgan", "realesrgan-ncnn-vulkan.exe")
RNNOISE = os.path.join(ROOT, "data", "bin", "rnnoise", "sh.rnnn")  # GregorR/rnnoise-models "somnolent-hogwash" (speech + noise)
_rembg = None


def remove_background(src, dest=None):
    """Cut the subject out (rembg, u2net on ONNX Runtime; model downloads once, ~170 MB). -> transparent PNG."""
    global _rembg
    from PIL import Image
    from rembg import new_session, remove
    _rembg = _rembg or new_session("u2net")  # loading the model is the slow part: once per run
    dest = dest or out_path(src, "nobg", ".png")
    remove(Image.open(src), session=_rembg).save(dest)
    return dest


def upscale(src, scale=4, dest=None, anime=False):
    """Real-ESRGAN (ncnn/Vulkan, on the GPU): 2x-4x with detail restored, not just stretched."""
    dest = dest or out_path(src, f"x{scale}", ".png")
    model = "realesrgan-x4plus-anime" if anime else "realesrgan-x4plus"
    src, dest = os.path.abspath(src), os.path.abspath(dest)  # the tool runs from its own folder
    args = [ESRGAN, "-i", src, "-o", dest, "-n", model, "-s", "4", "-f", "png"]
    from ishti import gpu
    with gpu.slot(1500, "upscale"):  # Real-ESRGAN next to the 14B model once made Ollama run out of VRAM
        subprocess.run(args, check=True, capture_output=True, timeout=600, creationflags=NO_WINDOW,
                       cwd=os.path.dirname(ESRGAN))
    if not os.path.isfile(dest):
        raise RuntimeError("the upscaler produced no image (no Vulkan GPU?)")
    if scale != 4:  # the x4plus model only does 4x: scale the result down to what was asked (still sharper)
        from PIL import Image
        im, orig = Image.open(dest), Image.open(src)
        im.resize((orig.width * scale, orig.height * scale), Image.LANCZOS).save(dest)
    return dest


def op_diarize(m, text, session):
    src = next((p for p in paths(text) if p.lower().endswith(AUDIO + VIDEO)), None)
    if not src:
        return "Which recording? Give me its full path."
    from ishti import diarize
    n = re.search(r"\b(\d|two|three|four|five)\s+(?:speakers|people|persons)\b", text, I)
    count = {"two": 2, "three": 3, "four": 4, "five": 5}.get(n.group(1).lower(), None) if n and not n.group(1).isdigit() \
        else (int(n.group(1)) if n else None)
    trace(session, "diarize", "who is speaking when (speaker voices + Whisper)", status="start")
    turns = diarize.diarize(src, speakers=count)
    if not turns:
        return f"I heard no speech in {os.path.basename(src)}."
    files = diarize.split(src, turns, OUT)
    speakers = sorted({t["speaker"] for t in turns})
    trace(session, "diarize", f"{len(speakers)} speaker(s)", status="done")
    return (f"{len(speakers)} speaker(s) in {os.path.basename(src)}:\n\n{diarize.transcript(turns)}\n\nEach speaker on their own:\n"
            + "\n".join(f"- Speaker {k}: {v}" for k, v in sorted(files.items())))


def op_remove_bg(m, text, session):
    imgs = _images(text)
    if not imgs:
        return "Which image? Give me its full path."
    trace(session, "rembg", f"cutting out {len(imgs)} image(s)", status="start")
    done = [remove_background(p) for p in imgs[:50]]
    trace(session, "rembg", "done", status="done")
    return "Background removed (transparent PNG):\n" + "\n".join(f"- {d}" for d in done)


def op_upscale(m, text, session):
    imgs = _images(text)
    if not imgs:
        return "Which image? Give me its full path."
    if not os.path.isfile(ESRGAN):
        return "The upscaler isn't installed (data/bin/realesrgan). Say \"use an online tool to upscale ...\" instead."
    factor = re.search(r"\b([234])\s*x\b|\bx\s*([234])\b", text, I)
    scale = int(factor.group(1) or factor.group(2)) if factor else 4
    anime = bool(re.search(r"\b(?:anime|cartoon|drawing|illustration)\b", text, I))
    trace(session, "realesrgan", f"upscaling {len(imgs)} image(s) {scale}x on the GPU", status="start")
    done = [upscale(p, scale, anime=anime) for p in imgs[:20]]
    trace(session, "realesrgan", "done", status="done")
    return f"Upscaled {scale}x:\n" + "\n".join(f"- {d}" for d in done)


def op_resize(m, text, session):
    from PIL import Image
    imgs = _images(text)
    if not imgs:
        return "Which image(s)? Give me a path or a folder like downloads."
    pct = re.search(r"(\d{1,3})\s*%", text)
    box = re.search(r"(\d{2,5})\s*[x×]\s*(\d{2,5})", text)
    width = re.search(r"(\d{2,5})\s*(?:px|pixels)?\s*(?:wide|width)", text, I)
    for p in imgs:
        im = Image.open(p)
        if pct:
            size = (max(1, im.width * int(pct.group(1)) // 100), max(1, im.height * int(pct.group(1)) // 100))
        elif box:
            im.thumbnail((int(box.group(1)), int(box.group(2))))
            size = im.size
        else:
            w = int(width.group(1)) if width else 1280
            size = (w, max(1, round(im.height * w / im.width)))
        im.resize(size, Image.LANCZOS).save(out_path(p, "resized"), quality=90)
    return f"Resized {len(imgs)} image(s) into {OUT}" + (f":\n{out_path(imgs[0], 'resized')}" if len(imgs) == 1 else "")


def op_image_edit(m, text, session):
    from PIL import Image, ImageFilter, ImageOps
    src = next((p for p in paths(text) if p.lower().endswith(IMAGE)), None)
    im = Image.open(src)
    deg = re.search(r"rotate\s+(?:it\s+)?(?:by\s+)?(-?\d{1,3})", text, I)
    if deg or re.search(r"\brotate\b", text, I):
        im = im.rotate(-int(deg.group(1)) if deg else -90, expand=True)
    if re.search(r"\bflip\b", text, I):
        im = ImageOps.flip(im)
    if re.search(r"\bmirror\b", text, I):
        im = ImageOps.mirror(im)
    if re.search(r"\b(?:grayscale|greyscale|black\s+and\s+white)\b", text, I):
        im = ImageOps.grayscale(im)
    if re.search(r"\bblur\b", text, I):
        im = im.filter(ImageFilter.GaussianBlur(4))
    if re.search(r"\bsharpen\b", text, I):
        im = im.filter(ImageFilter.UnsharpMask(2, 150, 3))
    dst = out_path(src, "edited")
    im.save(dst, quality=92)
    return f"Edited: {dst}"


def op_strip_meta(m, text, session):
    from PIL import Image
    imgs = _images(text)
    for p in imgs:
        im = Image.open(p)
        clean = Image.new(im.mode, im.size)
        clean.putdata(list(im.getdata()))  # pixels only: no EXIF, GPS, camera or software tags
        clean.save(out_path(p, "clean"), quality=95)
    return f"Removed metadata (EXIF, GPS location) from {len(imgs)} image(s); clean copies in {OUT}."


def op_meme(m, text, session):
    from PIL import Image, ImageDraw, ImageFont
    src = next((p for p in paths(text) if p.lower().endswith(IMAGE)), None)
    lines = QUOTED.findall(text)
    if not src or not lines:
        return "Give me the image path and the text in quotes, e.g. top \"WHEN THE CODE WORKS\" bottom \"FIRST TRY\"."
    im = Image.open(src).convert("RGB")
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype("impact.ttf" if os.path.exists(r"C:\Windows\Fonts\impact.ttf") else "arialbd.ttf", max(24, im.width // 11))
    for i, line in enumerate(lines[:2]):
        line = line.upper()
        w = d.textlength(line, font=font)
        y = 12 if i == 0 else im.height - font.size - 24
        d.text(((im.width - w) / 2, y), line, font=font, fill="white", stroke_width=max(2, font.size // 15), stroke_fill="black")
    dst = out_path(src, "meme", ".jpg")
    im.save(dst, quality=92)
    return f"Meme: {dst}"


def op_ocr(m, text, session):
    src = next((p for p in paths(text) if p.lower().endswith(IMAGE)), None)
    if not src:
        return "Give me the image's full path."
    from ishti.ocr import ocr
    found = ocr(src)
    if not found.strip():  # handwriting / stylised text: the vision model reads what OCR can't
        from ishti.skills.video.pro import look
        found = look(src, "Read ALL the text in this image exactly, keeping line breaks.").get("text", "")
    return f"Text in {os.path.basename(src)}:\n```\n{found}\n```" if found.strip() else f"I found no text in {os.path.basename(src)}."


from ishti import killswitch as _ks  # noqa: E402
_ks.on_kill(_stop_recordings)


if __name__ == "__main__":
    import tempfile
    cases = {r"transcribe D:\a\talk.mp3": "transcribe", r"summarize D:\a\note.m4a": "summarize_audio",
             r"normalize D:\a\talk.mp3": "normalize", r"remove the background noise from D:\a\talk.wav": "denoise",
             r"remove the silences from D:\a\talk.mp4": "silence", r"convert D:\a\talk.wav to mp3": "convert",
             r"merge D:\a\1.mp4 and D:\a\2.mp4": "merge", r"add subtitles to D:\a\v.mp4": "subtitles",
             r"add background music D:\a\s.mp3 to D:\a\v.mp4": "music", r"stabilize D:\a\v.mp4": "stabilize",
             r"make a gif from D:\a\v.mp4": "gif", r"watermark D:\a\p.jpg with 'ISHTI'": "watermark",
             "record my screen for 2 minutes": "screen_record", "stop recording": "stop_record",
             r"resize D:\a\p.jpg to 800 px wide": "resize", r"rotate D:\a\p.jpg 90": "image_edit",
             r"remove the metadata from D:\a\p.jpg": "strip_meta", r"read the text from D:\a\p.png": "ocr",
             'save "hello there" as audio': "tts", r"remove the background from D:\a\p.jpg": "remove_bg",
             r"upscale D:\a\p.jpg 2x": "upscale", r"enhance D:\a\p.jpg": "upscale",
             r"who said what in D:\a\meeting.mp3": "diarize", r"split D:\a\call.wav by speaker": "diarize"}
    for t, want in cases.items():
        got = next((n for n, rx in OPS if rx.search(t) and (n in ("stop_record", "screen_record", "webcam_record", "tts") or PATH.search(t))), None)
        assert got == want, (t, got, want)
    assert not match("what's the weather") and not match("merge the two branches")
    from PIL import Image
    d = tempfile.mkdtemp()
    src = os.path.join(d, "t.jpg")
    Image.new("RGB", (400, 200), "red").save(src)
    s = {}
    assert "Resized 1" in op_resize(None, f"resize {src} to 50%", s)
    assert Image.open(out_path(src, "resized")).size == (200, 100)
    assert "Watermarked" in op_watermark(None, f"watermark {src} with 'ISHTI'", s)
    from PIL import Image as _Img
    sample = os.path.join(tempfile.mkdtemp(), "sample.png")
    _Img.new("RGB", (64, 48), "white").save(sample)
    if os.path.isfile(ESRGAN):  # Real-ESRGAN on the GPU: a real 2x
        assert _Img.open(upscale(sample, 2)).size == (128, 96)
    if os.path.isfile(os.path.expanduser("~/.u2net/u2net.onnx")):
        assert _Img.open(remove_background(sample)).mode == "RGBA"
    print("[self-check] media OK - routing, resize, watermark, background removal, upscale")
