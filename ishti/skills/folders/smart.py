"""Organize by what files ARE, not just their extension.

1. Audio-only "videos" (.mp4/.webm with no video stream, e.g. voice notes) belong in Audio.
2. Inside document folders (PDFs, Documents, Spreadsheets, Presentations), group files by topic:
   each file is described by its cleaned name - plus the first lines of its text when the name says
   nothing ("1226225610.pdf", scanner names) - embedded locally with nomic-embed, clustered with
   k-means, and each cluster named by the local model from a dozen examples.
~1 minute for ~1,500 files instead of one model call per file."""
import os
import random
import re
import subprocess

DOC_FOLDERS = ("PDFs", "Documents", "Spreadsheets", "Presentations")
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi"}


def audio_only(path):
    """True when a 'video' file has no video stream (ffprobe)."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=codec_type",
                              "-of", "csv=p=0", path], capture_output=True, text=True, timeout=15).stdout
        return "video" not in out
    except Exception:
        return False


def opaque(name):
    """A name that says nothing about the content: numbers, hashes, scanner ids."""
    words = re.findall(r"[A-Za-z]{3,}", os.path.splitext(name)[0])
    return len(words) < 2 or bool(re.fullmatch(r"(?:scan|img|doc|skm|file|document)?[\W_\dA-Fa-f]*", os.path.splitext(name)[0], re.I))


def first_text(path, limit=300):
    """First lines of a PDF (pypdfium2, ~20 ms). Empty for other types or scanned images."""
    if not path.lower().endswith(".pdf"):
        return ""
    try:
        import pypdfium2 as pdfium
        from ishti import PDFIUM_LOCK
        with PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(path)
            text = pdf[0].get_textpage().get_text_range() if len(pdf) else ""
            pdf.close()
        return re.sub(r"\s+", " ", text)[:limit]
    except Exception:
        return ""


def describe(path):
    stem = re.sub(r"[_\-]+", " ", os.path.splitext(os.path.basename(path))[0])
    stem = re.sub(r"\(\d+\)", "", stem).strip()
    return f"{stem} | {first_text(path)}" if opaque(os.path.basename(path)) else stem


def topic_groups(paths, trace=lambda *a, **k: None):
    """{topic name: [paths]} for one folder's files."""
    from sklearn.cluster import KMeans
    import numpy as np
    from ishti import llm
    from ishti.semantic import embed

    n_opaque = sum(opaque(os.path.basename(p)) for p in paths)
    trace("read", f"read the first page of {n_opaque} files with unclear names", status="start")
    texts = [describe(p) for p in paths]
    trace("read", f"read {n_opaque} files", status="done")
    trace("embed", f"understand {len(paths)} files (local embeddings)", status="start")
    vecs = []
    for i in range(0, len(texts), 64):
        vecs += embed([f"clustering: {t}" for t in texts[i:i + 64]])
    trace("embed", f"embedded {len(paths)} files", status="done")
    k = max(2, min(10, round((len(paths) / 8) ** 0.5)))
    labels = KMeans(n_clusters=k, n_init=4, random_state=0).fit_predict(np.array(vecs))
    groups = {}
    trace("name", f"name {k} groups", status="start")
    for c in range(k):
        members = [i for i, l in enumerate(labels) if l == c]
        sample = [texts[i][:120] for i in random.Random(c).sample(members, min(12, len(members)))]
        name = llm.ask("These files belong in one folder. Give that folder a short, specific name (1-3 words, "
                       "Title Case, no punctuation, e.g. 'Tax Returns', 'LLC Certificates', 'Bank Statements'). "
                       "Output only the name.\n\n" + "\n".join(f"- {s}" for s in sample),
                       system=None, temperature=0, max_tokens=12)
        name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)  # "InvoiceNumbers" -> "Invoice Numbers"
        name = re.sub(r"[^\w &-]", "", name).strip()[:40] or f"Group {c + 1}"
        name = " ".join(w if w.isupper() and len(w) <= 4 else w.capitalize() for w in name.split())  # LLC stays LLC
        groups.setdefault(name, []).extend(paths[i] for i in members)  # same name twice -> one folder
    trace("name", f"groups: {', '.join(f'{g} ({len(v)})' for g, v in groups.items())}", status="done")
    return groups


def smart_plan(root, trace=lambda *a, **k: None):
    """Moves [(src, dst)] and a {folder label: count} summary for a content-aware reorganize of root."""
    moves, summary = [], {}
    # audio-only files sitting in video folders / named as video
    for sub in ("Videos", "Video", ""):
        d = os.path.join(root, sub)
        if not os.path.isdir(d):
            continue
        for e in os.scandir(d):
            if e.is_file() and os.path.splitext(e.name)[1].lower() in VIDEO_EXT and audio_only(e.path):
                moves.append((e.path, os.path.join(root, "Audio", e.name)))
    if moves:
        summary["Audio (were in Videos)"] = len(moves)
        trace("probe", f"{len(moves)} 'videos' are audio only - moving them to Audio")
    for folder in DOC_FOLDERS:
        d = os.path.join(root, folder)
        if not os.path.isdir(d):
            continue
        files = [e.path for e in os.scandir(d) if e.is_file() and not e.name.startswith("~$")]
        if len(files) < 12:  # too few to be worth sub-folders
            continue
        for topic, members in topic_groups(files, trace).items():
            summary[f"{folder}/{topic}"] = len(members)
            moves += [(p, os.path.join(d, topic, os.path.basename(p))) for p in members]
    return moves, summary
