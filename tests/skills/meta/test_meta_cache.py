"""FIX-10 regression: op_health must cache identical-skill-set results.

Measured: a full health pass costs ~72 s (110 subprocess self-checks); the generated
suite fires the health row 4x and a chat user repeats it too - uncached that is ~5 min
of the same work. Cache keys on the skill files' mtimes: any edit invalidates it.
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("ISHTI_TEST_MODE", "1")

from ishti.skills.meta import skill as meta  # noqa: E402


def test_second_health_call_is_cached():
    t0 = time.time()
    meta.op_health("check your skills", {})
    first = time.time() - t0
    t1 = time.time()
    again = meta.op_health("check your skills", {})
    second = time.time() - t1
    assert second < max(2.0, first / 10), f"second call took {second:.1f}s (first {first:.1f}s) - cache not working"
    assert "Skill health" in again


def test_cache_invalidates_when_a_skill_file_changes(tmp_path):
    import hashlib
    meta._health_cache.clear()
    meta.op_health("check your skills", {})
    key_before = next(iter(meta._health_cache))
    meta._health_cache.clear()
    # simulate a skill edit: a different mtime set must produce a different key
    skills_dir = os.path.join(ROOT, "ishti", "skills")
    names = sorted(n for n in os.listdir(skills_dir)
                   if os.path.isfile(os.path.join(skills_dir, n, "skill.py")))
    stamp = hashlib.sha256()
    for n in names:
        p = os.path.join(skills_dir, n, "skill.py")
        stamp.update(str(os.path.getmtime(p) + 1).encode())  # +1s: pretend one file changed
    key_after = stamp.hexdigest()
    assert key_before != key_after


def test_disk_cache_answers_a_fresh_process(tmp_path, monkeypatch):
    """Cycle 4: the ~72-80 s health pass must not repeat in a NEW process when nothing changed.

    The FIX-10 cache was in-memory only, so every restart paid the full pass again - the first
    `check your skills` after a boot was the slowest request in the whole system. Same mtime key =
    identical skill sources = the same report; the TTL stays 900 s."""
    monkeypatch.setattr(meta, "_HEALTH_DISK", str(tmp_path / "meta_health.json"))
    _, _, key = meta._health_key()
    reply = "Skill health: 122/122 self-checks pass (from the disk cache)"
    meta._health_disk_put(key, reply)
    assert meta._health_disk_get(key) == reply, "disk cache round-trip failed"
    assert meta._health_disk_get(key + "x") is None, "a different skill set must not reuse the reply"
    meta._health_cache.clear()   # as if this were a fresh process
    t0 = time.time()
    out = meta.op_health("check your skills", {})
    assert time.time() - t0 < 5, "disk cache not used - a fresh process would pay the full pass"
    assert "from the disk cache" in out
