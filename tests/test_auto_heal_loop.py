from ishti import self_heal


def test_auto_loop_repairs_only_when_enabled_and_unhealthy(monkeypatch):
    calls = []
    monkeypatch.setattr(self_heal, "trigger_heal", lambda trigger="manual", issues=None: calls.append((trigger, issues)))
    monkeypatch.setattr(self_heal, "diagnose", lambda: ["Ollama unreachable"])
    monkeypatch.setattr(self_heal, "AUTO_INTERVAL_S", 0)
    monkeypatch.setattr(self_heal, "AUTO_COOLDOWN_S", 3600)
    monkeypatch.setattr(self_heal, "_save_state", lambda: None)
    import time
    self_heal._state["enabled"] = False
    self_heal.ensure_loop()
    time.sleep(0.3)
    assert calls == []                      # OFF: loop idles
    self_heal.set_enabled(True)
    time.sleep(0.5)
    self_heal._state["enabled"] = False
    assert len(calls) == 1 and calls[0][0] == "auto"   # ON: one repair, then the cooldown holds
