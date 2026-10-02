"""Speaker diarization ("who said what"): Whisper segments -> SpeechBrain ECAPA speaker embeddings -> clustering.
pyannote's models are gated behind a Hugging Face login; SpeechBrain's ECAPA (speechbrain/spkrec-ecapa-voxceleb,
~80 MB, downloaded once into data/models) is open and runs on the CPU in seconds.

diarize(path, speakers=None) -> [{"speaker": 1, "start", "end", "text"}]; speakers = known count, else found by
a cosine-distance threshold. split(path, turns) writes one WAV per speaker."""
import os
import subprocess
import wave

import numpy as np

from ishti import CONFIG, ROOT

MODEL_DIR = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "models", "ecapa")
_enc = None


def _pcm16k(path):
    from ishti.skills.media.skill import ed
    raw = subprocess.run([ed().ffmpeg_exe, "-loglevel", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=900).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768


def encoder():
    global _enc
    if _enc is None:
        from speechbrain.inference.speaker import EncoderClassifier
        from speechbrain.utils.fetching import LocalStrategy
        _enc = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb", savedir=MODEL_DIR,
                                              run_opts={"device": "cpu"}, local_strategy=LocalStrategy.COPY)
    return _enc


def segments(pcm):
    from ishti.voice import pick_language, stt_call, stt_model
    model = stt_model()
    lang = pick_language(model, pcm)
    # stt_call: the decode (and the lazy iteration over its segments) happens on the one STT thread, never here
    return stt_call(lambda: [{"start": s.start, "end": s.end, "text": s.text.strip()}
                             for s in model.transcribe(pcm, beam_size=1, vad_filter=True, language=lang,
                                                       condition_on_previous_text=False)[0] if s.text.strip()])


def diarize(path, speakers=None, threshold=0.6):
    import torch
    from scipy.cluster.hierarchy import fcluster, linkage
    pcm = _pcm16k(path)
    segs = segments(pcm)
    if not segs:
        return []
    embs = []
    for s in segs:
        clip = pcm[int(s["start"] * 16000):int(s["end"] * 16000)]
        if len(clip) < 8000:  # under 0.5 s: pad so the encoder has something to hold on to
            clip = np.pad(clip, (0, 8000 - len(clip)))
        e = encoder().encode_batch(torch.from_numpy(clip).unsqueeze(0)).squeeze().detach().numpy()
        embs.append(e / (np.linalg.norm(e) + 1e-9))
    if len(segs) == 1:
        labels = [1]
    else:
        tree = linkage(np.array(embs), method="average", metric="cosine")
        labels = fcluster(tree, t=speakers, criterion="maxclust") if speakers else fcluster(tree, t=threshold, criterion="distance")
    order = {}  # speakers numbered by who speaks first
    for lab in labels:
        order.setdefault(lab, len(order) + 1)
    return [{**s, "speaker": order[lab]} for s, lab in zip(segs, labels)]


def split(path, turns, out_dir):
    """One WAV per speaker (their parts joined, 0.3 s gaps) -> {speaker: path}."""
    pcm = _pcm16k(path)
    gap = np.zeros(4800, np.float32)
    parts = {}
    for t in turns:
        parts.setdefault(t["speaker"], []).extend([pcm[int(t["start"] * 16000):int(t["end"] * 16000)], gap])
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    files = {}
    for spk, chunks in parts.items():
        dst = os.path.join(out_dir, f"{stem}_speaker{spk}.wav")
        with wave.open(dst, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes((np.clip(np.concatenate(chunks), -1, 1) * 32767).astype(np.int16).tobytes())
        files[spk] = dst
    return files


def transcript(turns):
    lines, last = [], None
    for t in turns:
        stamp = f"{int(t['start'] // 60):02d}:{int(t['start'] % 60):02d}"
        if t["speaker"] == last:
            lines[-1] += " " + t["text"]
        else:
            lines.append(f"**Speaker {t['speaker']}** [{stamp}]: {t['text']}")
        last = t["speaker"]
    return "\n".join(lines)


if __name__ == "__main__":
    import io
    import tempfile
    from ishti.voice import roster, synthesize
    voices = {v["name"]: v for v in roster()}
    a, b = voices["Male 1 - Deep/Calm"], voices["Female 1 - Neutral"]
    lines = [(a, "Good morning, did you finish the quarterly report?"), (b, "Almost, I still need the sales figures from March."),
             (a, "I can send them to you before lunch today."), (b, "Perfect, then I will share the final version this afternoon.")]
    folder = tempfile.mkdtemp()
    pcm = []
    for voice, line in lines:
        with wave.open(io.BytesIO(synthesize(line, voice))) as wf:
            x = np.frombuffer(wf.readframes(wf.getnframes()), np.int16).astype(np.float32)
            rate = wf.getframerate()
        t = np.arange(0, len(x) / rate, 1 / 16000)
        pcm += [np.interp(t, np.arange(len(x)) / rate, x), np.zeros(8000)]
    path = os.path.join(folder, "meeting.wav")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(np.concatenate(pcm).astype(np.int16).tobytes())
    turns = diarize(path)
    who = [t["speaker"] for t in turns]
    print(transcript(turns))
    assert len(set(who)) == 2, who
    first = {t["speaker"] for t in turns if "report" in t["text"].lower() or "lunch" in t["text"].lower()}
    second = {t["speaker"] for t in turns if "march" in t["text"].lower() or "afternoon" in t["text"].lower()}
    assert len(first) == 1 and len(second) == 1 and first != second, (first, second)
    files = split(path, turns, folder)
    assert len(files) == 2 and all(os.path.getsize(f) > 10000 for f in files.values())
    print("[self-check] diarize OK - two voices found, every line attributed to the right one, one file per speaker")
