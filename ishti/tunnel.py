"""Owner-controlled ngrok tunnel lifecycle. The agent API is queried on loopback only."""
from __future__ import annotations

from ishti.core.logutil import swallowed
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

from ishti import audit, CONFIG
from ishti.test_mode import scratch_path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = Path(scratch_path(str(Path(CONFIG["memory"]["db_path"]).parent / "tunnel_state.json")))
API_BASE = "http://127.0.0.1:4040/api"
_ERROR = re.compile(r"\bERR_NGROK_\d+\b")


class TunnelError(RuntimeError):
    pass


def locate_ngrok() -> str:
    found = shutil.which("ngrok")
    if found and Path(found).is_file():
        return found
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates = [local / "Microsoft" / "WinGet" / "Links" / "ngrok.exe"]
    packages = local / "Microsoft" / "WinGet" / "Packages"
    if packages.is_dir():
        candidates.extend(packages.glob("Ngrok.Ngrok_Microsoft.Winget.Source_*/*/ngrok.exe"))
        candidates.extend(packages.glob("Ngrok.Ngrok_Microsoft.Winget.Source_*/ngrok.exe"))
    return next((str(path) for path in candidates if path.is_file()), "")


def _safe_error(lines: list[str]) -> str:
    text = " ".join(str(line).strip() for line in lines[-8:] if str(line).strip())
    code = _ERROR.search(text)
    if code:
        return f"ngrok could not start ({code.group(0)}); review the local ngrok configuration"
    if re.search(r"(?i)authtoken|authentication failed|not authenticated", text):
        return "ngrok is not authenticated; add your authtoken with the ngrok CLI, then retry"
    return "ngrok did not publish a tunnel; review its local log" if not text else text[:240]


class TunnelManager:
    def __init__(self, *, api_base=API_BASE, process_factory=subprocess.Popen, sleep=time.sleep,
                 ngrok_path=None, state_path=None, allow_test=False):
        self.api_base = api_base.rstrip("/")
        self.process_factory = process_factory
        self.sleep = sleep
        self.ngrok_path = ngrok_path
        self.state_path = Path(state_path or STATE_PATH)
        self.allow_test = allow_test
        self.process = None
        self.public_url = ""
        self.port = 0
        self.last_error = ""
        self._lines: list[str] = []
        self._log_handle = None
        self._log_offset = 0
        self._lock = threading.RLock()

    def _agent_tunnels(self) -> list[dict]:
        request = urllib.request.Request(self.api_base + "/tunnels", method="GET")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=1.5) as response:
            raw = response.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            raise TunnelError("ngrok agent response exceeded the safety limit")
        parsed = json.loads(raw)
        return parsed.get("tunnels", []) if isinstance(parsed, dict) else []

    def _https_url(self) -> str:
        try:
            rows = self._agent_tunnels()
        except (OSError, TimeoutError, urllib.error.URLError, ValueError, json.JSONDecodeError):
            return ""
        return next((str(row.get("public_url")) for row in rows
                     if str(row.get("public_url", "")).startswith("https://")), "")

    def _write_state(self, value: dict | None):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if value is None:
            self.state_path.unlink(missing_ok=True)
            return
        tmp = self.state_path.with_suffix(f".{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(value), encoding="utf-8")
            os.replace(tmp, self.state_path)
        finally:
            tmp.unlink(missing_ok=True)

    def _saved_state(self) -> dict:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def _refresh_lines(self):
        try:
            log_path = self.state_path.with_name("ngrok_agent.log")
            with log_path.open("r", encoding="utf-8", errors="replace") as stream:
                stream.seek(self._log_offset)
                self._lines.extend(line.strip() for line in stream if line.strip())
                self._log_offset = stream.tell()
            self._lines[:] = self._lines[-40:]
        except (OSError, ValueError) as _exc:
            swallowed('ishti/tunnel.py' + ':115', _exc)

    def _owned_saved_process(self):
        saved = self._saved_state()
        pid = int(saved.get("pid") or 0)
        if pid <= 0:
            return None
        try:
            import psutil
            process = psutil.Process(pid)
            command = " ".join(process.cmdline()).casefold()
            if "ngrok" not in Path(process.exe()).name.casefold() or "127.0.0.1:" not in command:
                return None
            return process
        except Exception:
            return None

    def start(self, port: int = 8888, timeout_s: float = 12.0) -> dict:
        if os.environ.get("ISHTI_TEST_MODE") == "1" and not self.allow_test:
            raise TunnelError("Live tunnels are disabled in test mode")
        port = int(port)
        if not 1024 <= port <= 65535:
            raise TunnelError("Port must be between 1024 and 65535")
        with self._lock:
            current = self.status()
            if current["running"]:
                return current
            executable = self.ngrok_path or locate_ngrok()
            if not executable:
                raise TunnelError("ngrok is not installed or not visible on PATH/WinGet")
            self._lines.clear(); self.last_error = ""; self.port = port
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                log_path = self.state_path.with_name("ngrok_agent.log")
                log_path.parent.mkdir(parents=True, exist_ok=True)
                self._log_handle = log_path.open("a+", encoding="utf-8")
                self._log_handle.seek(0, os.SEEK_END); self._log_offset = self._log_handle.tell()
                self.process = self.process_factory(
                    [executable, "http", f"127.0.0.1:{port}", "--log=stdout"],
                    stdout=self._log_handle, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                    errors="replace", creationflags=flags,
                )
            except OSError as exc:
                if self._log_handle:
                    self._log_handle.close(); self._log_handle = None
                raise TunnelError(f"ngrok could not be launched: {type(exc).__name__}") from exc
            deadline = time.monotonic() + max(1.0, float(timeout_s))
            while time.monotonic() < deadline:
                self._refresh_lines()
                if self.process.poll() is not None:
                    self.last_error = _safe_error(self._lines)
                    self._write_state(None)
                    raise TunnelError(self.last_error)
                url = self._https_url()
                if url:
                    self.public_url = url
                    self._write_state({"pid": self.process.pid, "port": port, "public_url": url})
                    audit.record("remote tunnel started", f"port {port}", skill="remote", initiator="owner", tier="T1")
                    return self.status()
                self.sleep(0.25)
            self.last_error = _safe_error(self._lines)
            self.stop(record=False)
            raise TunnelError(self.last_error)

    def stop(self, *, record=True) -> dict:
        with self._lock:
            process = self.process if self.process and self.process.poll() is None else self._owned_saved_process()
            was_running = process is not None
            if process is not None:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    try:
                        process.kill()
                        process.wait(timeout=3)
                    except Exception as _exc:
                        swallowed('ishti/tunnel.py' + ':192', _exc)
            self.process = None; self.public_url = ""; self.port = 0
            if self._log_handle:
                try:
                    self._log_handle.close()
                except OSError as _exc:
                    swallowed('ishti/tunnel.py' + ':198', _exc)
                self._log_handle = None
            self._write_state(None)
            if was_running and record:
                audit.record("remote tunnel stopped", "owner request or ISHTI shutdown",
                             skill="remote", initiator="owner", tier="T1")
            return {"running": False, "public_url": "", "last_error": self.last_error}

    def status(self) -> dict:
        with self._lock:
            running = bool(self.process and self.process.poll() is None)
            saved = self._saved_state()
            if not running:
                running = self._owned_saved_process() is not None
            if running:
                self.public_url = self._https_url() or self.public_url or str(saved.get("public_url") or "")
                self.port = self.port or int(saved.get("port") or 0)
            else:
                self.public_url = ""
            return {"running": running, "public_url": self.public_url, "port": self.port,
                    "last_error": self.last_error}


_manager = TunnelManager()


def start(port=8888):
    return _manager.start(port)


def stop():
    return _manager.stop()


def status():
    return _manager.status()


def url():
    return status().get("public_url", "")


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Manage the owner-controlled ISHTI ngrok tunnel")
    parser.add_argument("action", choices=("start", "stop", "status"))
    parser.add_argument("port", nargs="?", type=int, default=8888)
    args = parser.parse_args(argv)
    try:
        if args.action == "start":
            from ishti.channels import policy
            policy.save({"remote_access_enabled": True})
            result = start(args.port)
        elif args.action == "stop":
            from ishti.channels import policy
            policy.save({"remote_access_enabled": False})
            result = stop()
        else:
            result = status()
        print(json.dumps(result, indent=2))
        return 0
    except TunnelError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
