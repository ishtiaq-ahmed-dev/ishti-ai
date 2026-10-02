"""WP-JEV acceptance: push-to-talk + dictation mode (matrix rows JEV-02, JEV-03).

T-JEV-02 / T-JEV-03, fully headless (injected transcribe/paste/clipboard/binder, scratch log):
  - with NO session active, push_chunk() refuses: no transcription, no paste, no history -
    the always-listening mic stays OFF by construction
  - PTT: a bound key's press starts a session, release ends it; a second release is False
  - dictation: start/stop + the "start/stop dictation" phrase family
  - success: text is clipped, pasted, and one JSONL row is written (index, chars, pasted)
  - failure: a chunk that raises or transcribes empty is NEVER pasted; its audio path and
    error are preserved in the history and state() counts the hole; the session continues
  - history is bounded (max_chunks) JSONL; a missing backend fails honestly (bind_ptt False)
  - junk inputs never raise; double start is refused
  - the module's own __main__ self-check stays green

Run: venv\\Scripts\\python -m pytest tests/test_dictation.py -q
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti import dictation  # noqa: E402


class Rig:
    """An engine with every side effect captured."""

    def __init__(self, tmp_path, texts=(), max_chunks=dictation.MAX_CHUNKS):
        self.pasted, self.clipped, self.bound = [], [], {}
        self.texts = dict(texts)
        self.engine = dictation.Dictation(
            transcribe=self._transcribe, log_path=str(tmp_path / "history.jsonl"),
            paste=lambda t: self.pasted.append(t) or True,
            clipboard=lambda t: self.clipped.append(t) or True,
            binder=self._binder, max_chunks=max_chunks)

    def _transcribe(self, path):
        key = str(path)
        if "boom" in key:
            raise RuntimeError("stt down")
        return self.texts.get(key, "")

    def _binder(self, key, on_press, on_release):
        self.bound = {"key": key, "press": on_press, "release": on_release}
        return lambda: self.bound.update(unbound=True)

    def rows(self):
        return self.engine.history(limit=99)


def test_nothing_records_without_an_explicit_session(tmp_path):
    rig = Rig(tmp_path, {"a.wav": "hello"})
    d = rig.engine
    assert d.active() is False
    r = d.push_chunk("a.wav")
    assert r["ok"] is False and "no active session" in r["error"]
    assert rig.pasted == [] and rig.clipped == [] and rig.rows() == []
    assert not os.path.exists(d.log_path), "no history file is even created"


def test_ptt_press_and_release_cycle(tmp_path):
    rig = Rig(tmp_path, {"a.wav": "hello world"})
    d = rig.engine
    assert d.bind_ptt("right alt") is True and d.state()["ptt_bound"]
    assert rig.bound["key"] == "right alt"
    rig.bound["press"]()
    assert d.active() and d.state()["kind"] == "ptt"
    assert d.push_chunk("a.wav")["ok"] is True and rig.pasted == ["hello world"]
    assert rig.bound["release"]() is True and d.active() is False
    assert rig.bound["release"]() is False, "a second release does nothing"
    assert d.unbind_ptt() is True and rig.bound.get("unbound") is True
    assert d.state()["ptt_bound"] is False


def test_dictation_phrase_family(tmp_path):
    rig = Rig(tmp_path)
    d = rig.engine
    for phrase in ("start dictation", "Begin dictating.", "open typing", "dictation on"):
        d.stop()
        assert d.apply_phrase(phrase) == "start" and d.active(), phrase
    for phrase in ("stop dictation", "end dictating!", "finish typing", "dictation off"):
        d.start()
        assert d.apply_phrase(phrase) == "stop" and not d.active(), phrase
    assert d.apply_phrase("what is the weather") is None
    assert d.apply_phrase(None) is None and d.apply_phrase(42) is None


def test_success_writes_one_bounded_jsonl_row(tmp_path):
    rig = Rig(tmp_path, {"a.wav": "  hello world  "})
    d = rig.engine
    assert d.start()
    r = d.push_chunk("a.wav", label="memo")
    assert r["ok"] and r["text"] == "hello world" and r["index"] == 1 and r["pasted"] is True
    assert rig.clipped == ["hello world"] and rig.pasted == ["hello world"]
    (row,) = rig.rows()
    assert row["ok"] is True and row["chars"] == 11 and row["pasted"] is True and row["label"] == "memo"
    assert json.loads(open(d.log_path, encoding="utf-8").read().strip()) == row
    assert d.state() == {"active": True, "kind": "dictation", "chunks": 1, "holes": 0,
                         "since": d.state()["since"], "ptt_bound": False, "ptt_key": None,
                         "log_path": d.log_path}


def test_failed_chunk_is_never_pasted_and_keeps_its_audio(tmp_path):
    rig = Rig(tmp_path, {"ok.wav": "good text", "empty.wav": "  "})
    d = rig.engine
    assert d.start()
    bad = d.push_chunk("boom.wav")
    assert bad["ok"] is False and "not pasted" in bad["error"] and "RuntimeError" in bad["error"]
    empty = d.push_chunk("empty.wav")
    assert empty["ok"] is False and "empty transcript" in empty["error"]
    assert rig.pasted == [] and rig.clipped == [], "a hole is never pasted"
    assert d.state()["holes"] == 2 and d.state()["chunks"] == 0
    good = d.push_chunk("ok.wav")
    assert good["ok"] is True and good["index"] == 3, "indexes account for the holes"
    rows = rig.rows()
    assert [r["ok"] for r in rows] == [False, False, True]
    assert rows[0]["audio"] == "boom.wav" and "RuntimeError" in rows[0]["error"], rows[0]
    assert rows[1]["audio"] == "empty.wav" and rows[1]["error"] == "empty transcript"


def test_history_is_bounded(tmp_path):
    rig = Rig(tmp_path, {f"c{i}.wav": f"chunk {i}" for i in range(6)}, max_chunks=3)
    d = rig.engine
    assert d.start()
    for i in range(6):
        assert d.push_chunk(f"c{i}.wav")["ok"] is True
    d._trim()
    rows = rig.rows()
    assert len(rows) == 3 and [r["text"] for r in rows] == ["chunk 3", "chunk 4", "chunk 5"]
    assert rig.pasted == [f"chunk {i}" for i in range(6)], "trimming the log never drops a paste"


def test_missing_hotkey_backend_fails_honestly(tmp_path):
    d = dictation.Dictation(transcribe=lambda p: "x", log_path=str(tmp_path / "h.jsonl"),
                            binder=lambda *a: (_ for _ in ()).throw(ImportError("no pynput")))
    assert d.bind_ptt("right alt") is False
    assert d.state()["ptt_bound"] is False and d.active() is False
    assert d.unbind_ptt() is False


def test_junk_is_total_and_double_start_refused(tmp_path):
    rig = Rig(tmp_path)
    d = rig.engine
    for junk in (None, [], {"a": "b"}, b""):
        assert d.push_chunk(junk)["ok"] is False
    assert d.stop() is False and d.release() is False
    assert d.start() and d.start() is False
    assert d.state()["kind"] == "dictation"
    assert d.history(limit="junk") == [] or isinstance(d.history(limit="junk"), list)
    assert d._transcribe.__name__ == "_transcribe"


def test_engine_singleton_is_shared():
    assert dictation.engine() is dictation.engine()


def test_selfcheck_still_passes():
    mod = os.path.join(ROOT, "ishti", "dictation.py")
    env = {**os.environ,
           "PYTHONPATH": ROOT + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")}
    r = subprocess.run([sys.executable, mod], capture_output=True, text=True, timeout=120, cwd=ROOT, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[self-check] dictation OK" in r.stdout
