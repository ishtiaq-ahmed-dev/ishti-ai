def test_probe_separates_system_process_and_python_memory(tmp_path):
    from ishti.core.memory_probe import Probe
    import tracemalloc
    probe = Probe(tmp_path / "growth.json")
    row = probe.sample()
    assert row["rss_bytes"] > 0 and row["system_used_bytes"] > row["rss_bytes"]
    assert row["python_bytes"] >= 0 and "children" in row
    probe.sample()
    probe.save()
    import json
    report = json.loads(probe.path.read_text())
    assert len(report["samples"]) == 2 and len(report["growth"]) == 1
    assert all(set(a) == {"file", "line", "bytes", "count"} for a in report["growth"][0]["allocators"])
    tracemalloc.stop()
