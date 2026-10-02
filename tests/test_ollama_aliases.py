"""ishti/ollama_aliases.py: only provably-ISHTI, unneeded, old, unloaded context aliases are ever removed.
Ollama is faked (get/post/delete injected): offline, no GPU, nothing real is touched."""
import datetime as dt

from ishti import ollama_aliases as oa

NOW = dt.datetime(2026, 10, 2, 12, 0, tzinfo=dt.timezone.utc)


def tag(name, parent, hours_old=100):
    return {"name": name, "details": {"parent_model": parent},
            "modified_at": (NOW - dt.timedelta(hours=hours_old)).isoformat()}


class FakeOllama:
    def __init__(self, tags, loaded=(), num_ctx=None):
        self.tags, self.loaded, self.num_ctx, self.deleted = tags, set(loaded), num_ctx or {}, []

    def get(self, path):
        return {"models": self.tags} if path == "/api/tags" else {"models": [{"name": n} for n in self.loaded]}

    def post(self, path, body):
        name = body["model"]
        ctx = self.num_ctx.get(name)
        if ctx is None:
            m = oa.ALIAS.match(name)
            ctx = int(m["k"]) * 1024 if m else 4096
        return {"parameters": f"num_ctx                        {ctx}\nstop \"<end>\""}

    def delete(self, name):
        self.deleted.append(name)
        return 200


def run(fake, needed, apply=True):
    return oa.cleanup(apply=apply, get=fake.get, post=fake.post, delete=fake.delete, needed=needed, now=NOW)


def test_removes_only_the_stale_governor_aliases():
    fake = FakeOllama([
        tag("gemma3-c2k:12b", "gemma3:12b"),                   # stale: not a rung, old, not loaded
        tag("qwen2.5-c8k:7b", "qwen2.5:7b"),                   # a rung -> kept
        tag("qwen2.5-c8k:3b", "qwen2.5:3b"),                   # loaded right now -> kept
        tag("gemma3-c4k:4b", "gemma3:4b", hours_old=2),        # too young: a concurrent job may be about to load it
        tag("gemma3:12b", ""),                                 # not alias-shaped
        tag("ishti-qwen2.5:14b", ""),                          # the owner's custom model: never touched
    ], loaded={"qwen2.5-c8k:3b"})
    out = run(fake, needed={"qwen2.5-c8k:7b"})
    assert fake.deleted == ["gemma3-c2k:12b"] and out["removed"] == ["gemma3-c2k:12b"]
    assert set(out["kept"]) == {"qwen2.5-c8k:7b", "qwen2.5-c8k:3b", "gemma3-c4k:4b"}


def test_dry_run_deletes_nothing():
    fake = FakeOllama([tag("gemma3-c2k:12b", "gemma3:12b")])
    out = run(fake, needed=set(), apply=False)
    assert out["stale"] == ["gemma3-c2k:12b"] and out["removed"] == [] and fake.deleted == []


def test_a_look_alike_the_owner_made_by_hand_is_never_removed():
    fake = FakeOllama([
        tag("myproj-c8k:7b", "something-else:7b"),             # parent does not match the name -> not ours
        tag("tuned-c4k:14b", "tuned:14b"),                     # right parent, but num_ctx is not the one in the name
    ], num_ctx={"tuned-c4k:14b": 16384})
    out = run(fake, needed=set())
    assert fake.deleted == [] and out["stale"] == [] and out["kept"] == []


def test_a_failed_delete_is_reported_not_hidden():
    fake = FakeOllama([tag("gemma3-c2k:12b", "gemma3:12b")])
    fake.delete = lambda name: 500
    out = run(fake, needed=set())
    assert out["failed"] == ["gemma3-c2k:12b"] and out["removed"] == []


def test_needed_names_come_from_the_ladder(monkeypatch):
    from ishti import vram
    spec = {"roles": {"coder": {"rungs": [{"model": "qwen2.5-coder:7b", "num_ctx": 8192}, {"model": "laya", "cpu": True}]}}}
    monkeypatch.setattr(vram, "cfg", lambda: spec)
    monkeypatch.setattr(vram, "alias_name", lambda model, ctx: f"{model.split(':')[0]}-c{ctx // 1024}k:{model.split(':')[1]}" if ctx else model)
    monkeypatch.setattr(vram, "rung_num_ctx", lambda r: r.get("num_ctx"))
    monkeypatch.setattr(vram, "rungs", lambda role: [])
    names = oa.needed_names()
    assert "qwen2.5-coder-c8k:7b" in names and "laya" in names
