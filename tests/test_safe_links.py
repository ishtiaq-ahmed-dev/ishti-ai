"""WP-COUCOU acceptance: safe-link handling for overlay-rendered links (matrix row COU-22).

T-C22, against ishti.untrusted.safe_web_url (coucou SafeWebURL behaviour):
  - only http/https URLs are returned for opening
  - javascript:, file:, data:, vbscript:, blob:, about:, mailto:, ftp: -> None
  - scheme-relative //host, whitespace/control-hidden schemes (java\\tscript:),
    empty or host-less https:// -> None
  - the check is allow-list based: unknown future schemes are rejected by default
  - valid http/https links survive unchanged (no mangled hosts, ports, paths)
  - the module's own __main__ self-check (including safe_web_url rows) stays green

Run: venv\\Scripts\\python -m pytest tests/test_safe_links.py -q
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti import untrusted  # noqa: E402

safe_web_url = untrusted.safe_web_url


def test_http_https_pass_through_unchanged():
    for ok in ("https://example.com/a?b=1",
               "http://localhost:8888/ui",
               "https://sub.example.co.uk:8443/path/to/page",
               "HTTPS://Example.COM",
               "  https://padded.example/x  "):
        out = safe_web_url(ok)
        assert out is not None, ok
        assert out.strip().lower().startswith(("http://", "https://")), out


def test_dangerous_schemes_rejected():
    for bad in ("javascript:alert(1)",
                "file:///c:/windows/system32/cmd.exe",
                "data:text/html,<script>alert(1)</script>",
                "vbscript:msgbox(1)",
                "blob:https://example.com/uuid",
                "about:blank",
                "mailto:boss@secret.example",
                "ftp://files.example/pub",
                "chrome://settings",
                "ws://localhost:9/x"):
        assert safe_web_url(bad) is None, bad


def test_obfuscated_schemes_rejected():
    for bad in ("java\tscript:alert(1)",          # tab inside the scheme
                "java\nscript:alert(1)",
                "java\x00script:alert(1)",
                " javascript:alert(1)",            # leading space hides nothing
                "jav\nascript:alert(1)",
                "JaVaScRiPt:alert(1)"):
        assert safe_web_url(bad) is None, bad


def test_scheme_relative_and_hostless_rejected():
    for bad in ("//evil.example/x",               # inherits whatever scheme - not allowed
                "https://",
                "http://",
                "https:///path-only",
                "",
                None,
                "not a url at all"):
        assert safe_web_url(bad) is None, bad


def test_allow_list_rejects_unknown_future_schemes():
    # allow-list, not block-list: anything not explicitly http/https is a no
    for bad in ("ishti-internal:run", "app://do-thing", "ssh://host", "telnet://h"):
        assert safe_web_url(bad) is None, bad


def test_selfcheck_still_passes():
    """untrusted.py's __main__ block now includes the COU-22 url rows."""
    mod = os.path.join(ROOT, "ishti", "untrusted.py")
    env = {**os.environ,
           "PYTHONPATH": ROOT + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")}
    r = subprocess.run([sys.executable, mod], capture_output=True, text=True,
                       timeout=60, cwd=ROOT, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[self-check] untrusted OK" in r.stdout
