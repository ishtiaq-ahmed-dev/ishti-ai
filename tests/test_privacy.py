"""ishti/privacy.py: the egress guard, the master switch, the ledger, hardening. Offline and fast: the socket's real connect
is replaced by a stub, so no packet is ever sent (an allowed connection just records the address it would have dialled)."""
import json
import os
import socket

import pytest

from ishti import audit, privacy, scope

EXT = "93.184.216.34"  # a public address (example.com); never actually dialled here


@pytest.fixture
def guard(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy, "SETTINGS_PATH", str(tmp_path / "privacy.json"))
    monkeypatch.setattr(privacy, "LEDGER_PATH", str(tmp_path / "outbound.log"))
    monkeypatch.setattr(audit, "DB", str(tmp_path / "audit.db"))  # scope._deny writes an audit row: not the owner's log
    audit._reset_cache()
    privacy._sw.update(mtime=None, checked=0.0, internet=True)
    privacy._totals.update(allowed=None, blocked=None)
    privacy._last.clear()
    privacy._resolved.clear()
    dialled = []
    monkeypatch.setattr(socket.socket, "connect", lambda self, a, *r: dialled.append(a))
    monkeypatch.setattr(socket.socket, "connect_ex", lambda self, a, *r: dialled.append(a) or 0)
    privacy.install()
    yield dialled
    privacy.uninstall()
    audit._reset_cache()


def dial(host, port=443):
    s = socket.socket()
    try:
        s.connect((host, port))
    finally:
        s.close()


def ledger():
    with open(privacy.LEDGER_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_external_connect_without_context_is_blocked_and_logged(guard):
    with pytest.raises(privacy.EgressBlocked) as e:
        dial(EXT, 443)
    assert EXT in str(e.value) and "nothing was sent" in str(e.value)
    assert guard == []  # the real connect was never reached
    row = ledger()[-1]
    assert (row["host"], row["port"], row["allowed"]) == (EXT, 443, False) and row["who"].startswith("library:")


def test_loopback_and_lan_are_allowed_and_not_logged(guard):
    for host in ("127.0.0.1", "localhost", "::1", "192.168.1.20", "10.0.0.5", "169.254.1.1"):
        dial(host, 11434)
    assert len(guard) == 6 and not os.path.exists(privacy.LEDGER_PATH)


def test_allow_context_lets_an_owner_download_through(guard):
    with privacy.allow("download voice"):
        dial(EXT)
    assert guard == [(EXT, 443)]
    row = ledger()[-1]
    assert row["allowed"] is True and row["who"] == "owner:download voice"
    with pytest.raises(privacy.EgressBlocked):  # and it is over when the block ends
        dial(EXT)


def test_skill_scope_net_is_respected(guard):
    privacy._resolved[EXT] = "api.open-meteo.com"
    with audit.context(skill="weather"):
        dial(EXT)  # declared in the weather manifest
    assert guard == [(EXT, 443)] and ledger()[-1]["who"] == "weather"
    privacy._resolved[EXT] = "evil.example.net"
    with audit.context(skill="weather"):
        with pytest.raises(scope.ScopeError):  # the skill's own wording, same error the call-level check raises
            dial(EXT)
    assert ledger()[-1]["allowed"] is False
    privacy._resolved[EXT] = "anything.example.org"
    with audit.context(skill="web_fetch"):  # net: ["*"]
        dial(EXT)
    assert len(guard) == 2


def test_telemetry_hosts_are_refused_even_for_a_wildcard_skill_and_allow(guard):
    privacy._resolved[EXT] = "us.i.posthog.com"
    with audit.context(skill="web_fetch"), pytest.raises(privacy.EgressBlocked):
        dial(EXT)
    with privacy.allow("download"), pytest.raises(privacy.EgressBlocked):
        dial(EXT)
    assert guard == [] and "telemetry" in ledger()[-1]["reason"]


def test_master_switch_off_blocks_inside_a_skill_and_allow(guard):
    privacy._resolved[EXT] = "api.open-meteo.com"
    privacy.set_internet(False)
    with audit.context(skill="weather"), pytest.raises(privacy.EgressBlocked):
        dial(EXT)
    with audit.context(skill="web_fetch"), pytest.raises(privacy.EgressBlocked):
        dial(EXT)
    with privacy.allow("download"), pytest.raises(privacy.EgressBlocked):
        dial(EXT)
    dial("127.0.0.1")  # the local brain keeps working
    assert guard == [("127.0.0.1", 443)] and "OFF" in ledger()[-1]["reason"]
    privacy.set_internet(True)
    with audit.context(skill="weather"):
        dial(EXT)


def test_dns_lookup_of_an_external_name_is_blocked_before_resolving(guard):
    with pytest.raises(privacy.EgressBlocked):
        socket.getaddrinfo("telemetry.example.com", 443)
    assert ledger()[-1]["host"] == "telemetry.example.com"


def test_ledger_holds_only_host_port_who_and_never_a_payload(guard):
    s = socket.socket()
    secret = "my-bank-password-hunter2"
    with pytest.raises(privacy.EgressBlocked):
        s.connect((EXT, 443))
        s.sendall(secret.encode())  # never reached
    with pytest.raises(privacy.EgressBlocked):
        s.sendto(secret.encode(), (EXT, 9))
    s.close()
    raw = open(privacy.LEDGER_PATH, encoding="utf-8").read()
    assert secret not in raw
    assert all(set(r) == {"ts", "host", "port", "who", "allowed", "reason"} for r in ledger())


def test_ledger_rotates_and_keeps_two_files(guard, monkeypatch):
    monkeypatch.setattr(privacy, "LEDGER_MAX", 300)
    for i in range(12):
        privacy._last.clear()
        with pytest.raises(privacy.EgressBlocked):
            dial(f"8.8.{i}.8")
    d = os.path.dirname(privacy.LEDGER_PATH)
    names = sorted(n for n in os.listdir(d) if n.startswith("outbound"))
    assert names == ["outbound.log", "outbound.log.1"]
    assert privacy.snapshot()["blocked_total"] == 12


def test_harden_sets_the_privacy_environment(monkeypatch, tmp_path):
    for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "ANONYMIZED_TELEMETRY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "True")  # a stray owner setting must not win
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    applied = privacy.harden()
    for k, v in (("HF_HUB_DISABLE_TELEMETRY", "1"), ("ANONYMIZED_TELEMETRY", "False"), ("DO_NOT_TRACK", "1"),
                 ("SCARF_NO_ANALYTICS", "true"), ("GRADIO_ANALYTICS_ENABLED", "False"), ("OLLAMA_NO_CLOUD", "1")):
        assert os.environ[k] == v == applied[k]
    assert "HF_HUB_OFFLINE" not in os.environ  # no model cache on disk: never forced offline (a first download must work)
    (tmp_path / "hub" / "models--x--y").mkdir(parents=True)
    privacy.harden()
    assert os.environ["HF_HUB_OFFLINE"] == os.environ["TRANSFORMERS_OFFLINE"] == "1"
    monkeypatch.delenv("HF_HUB_OFFLINE"), monkeypatch.delenv("TRANSFORMERS_OFFLINE")


def test_webview2_env_restricts_the_window_to_loopback():
    args = privacy.webview2_env()["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"]
    for flag in ("--disable-background-networking", "--disable-sync", "--disable-component-update", "--no-pings", "MAP * ~NOTFOUND"):
        assert flag in args


def test_privacy_endpoint_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy, "SETTINGS_PATH", str(tmp_path / "privacy.json"))
    monkeypatch.setattr(privacy, "LEDGER_PATH", str(tmp_path / "outbound.log"))
    monkeypatch.setattr(audit, "DB", str(tmp_path / "audit.db"))
    audit._reset_cache()
    privacy._sw.update(mtime=None, checked=0.0, internet=True)
    privacy.install()  # the REAL connect underneath: the test client's own loopback sockets must keep working under the guard
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ishti import privacy_api
    app = FastAPI()
    app.include_router(privacy_api.router)
    c = TestClient(app)
    snap = c.get("/api/privacy").json()
    assert snap["internet"] is True and {"blocked_total", "allowed_total", "recent", "skills", "hardening", "third_parties_found"} <= set(snap)
    assert any(s["name"] == "weather" and "open-meteo.com" in s["hosts"] for s in snap["skills"])
    assert c.post("/api/privacy", json={"internet": "yes"}).status_code == 400
    assert c.post("/api/privacy", json={"internet": False}).json()["internet"] is False
    assert privacy.internet() is False
    privacy.uninstall()
    audit._reset_cache()
