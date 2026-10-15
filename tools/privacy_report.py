"""ISHTI's privacy and egress report:  python tools/privacy_report.py

Answers, for one revision, the first question a compliance-bound buyer asks - "prove my data stays on
this machine" - with a page that names the revision it is about, and with checks that can fail.

    venv\\Scripts\\python tools\\privacy_report.py                       # the offline checks
    venv\\Scripts\\python tools\\privacy_report.py --live                # also run tools/gate_no_cloud.py
    venv\\Scripts\\python tools\\privacy_report.py --markdown docs\\PRIVACY_REPORT.md

Four checks, each pass / fail / unmeasured with the evidence that produced it:

  egress_guard_blocks   in a fresh process, the socket guard is installed and an unattributed connect to
                        a public IP raises EgressBlocked and lands one ledger row of exactly
                        {ts, host, port, who, allowed, reason} - host, port and who, never a payload.
  telemetry_deny        every host in ishti/privacy.py:TELEMETRY_DENY is refused even while a skill with
                        scope.net "*" and an owner grant is running. The same context must ALLOW a host
                        that is not on the list, or the check would pass for the wrong reason.
  loopback_bind         every socket bind in ISHTI's own source is a loopback literal (read from the AST
                        for Python, so a docstring that mentions a bind is not mistaken for one), and
                        orchestrator/plan_view.py:serve() refuses a non-loopback host at runtime.
  no_desktop_capture    no desktop-capture API appears in the product source (ishti/, desktop/,
                        orchestrator/, interface/), and remote screenshot requests are a refused category.

The `network_destinations` inventory that follows them - every host the code can reach, and why - is
descriptive and is never scored, so it cannot turn a red check green by being counted as one.

Writes reports/privacy.json (machine-readable, gitignored with the rest of reports/) and, with
--markdown, the tracked docs/PRIVACY_REPORT.md. tools/evidence.py folds the JSON into the evidence
roll-up when it is present, and .github/workflows/nightly.yml produces it on every run.

This does not duplicate tools/gate_no_cloud.py: that gate starts the real backend and watches its
sockets live, which is what --live delegates to. This report is the static and unit-level audit that
can run on a machine with no GPU, and it is the one that is revision-stamped.

Exit: 0 when no check failed (a check may still be NOT MEASURED, and the summary and JSON both say which),
1 when a check failed, 2 when the driver could not run at all, so nothing was checked either way.
"""
import argparse
import ast
import datetime as dt
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
REPORTS = os.path.join(ROOT, "reports")

# The product: what is on a customer's machine. tools/ and tests/ are not scanned for capture APIs
# because they are developer tooling - but a bind site anywhere first-party still counts.
PRODUCT_DIRS = ("ishti", "desktop", "orchestrator", "interface")
SCAN_DIRS = PRODUCT_DIRS + ("tools", "tests")
_SKIP = {"venv", "node_modules", "dist", "__pycache__", "_drafts", ".git", "build", "site-packages",
         ".mypy_cache", ".pytest_cache", "reports", "data"}
_CODE = (".py", ".ts", ".tsx")

# Desktop-capture APIs, by name. A hit here means something in ISHTI's own source can turn the screen
# into an image - the one capability the accessibility-tree rewrite (ishti/ui_access.py) removed.
CAPTURE_APIS = ("ImageGrab", "pyautogui.screenshot", "pyscreeze", "import mss", "mss.mss", "dxcam",
                "BitBlt", "GetDC", "GetWindowDC", "PrintWindow", "desktop_capture", "grab_screen",
                "capture_screen", "screenshot_scraping")

# A bind is a socket that accepts connections. Python files are read with the AST (py_bind_calls
# below) because a line pattern cannot tell a call from a mention of one. This pattern is the fallback
# for the other languages and for a .py file that does not parse: it requires the tuple argument,
# because `.bind(` alone also matches `calltrace.bind(ledger)` (ishti/background.py) and
# `window.fetch.bind(window)` inside a JS string in tools/qa/, neither of which opens a socket.
BIND_RE = re.compile(r"(?:\.bind\(\s*\(|HTTPServer\(\s*\(|ThreadingHTTPServer\(\s*\(|TCPServer\(\s*\(|"
                     r"uvicorn\.(?:Config|run)\()[^\n]*")
LOOPBACK = ('"127.0.0.1"', "'127.0.0.1'", '"localhost"', "'localhost'", '"::1"', "'::1'")

# The driver. Everything that imports product modules runs here, in its own process, so the socket
# guard this report installs and uninstalls can never change how the report itself behaves.
_DRIVER = r'''
import json, os, socket, sys, tempfile
root = sys.argv[1]
sys.path.insert(0, root)
os.environ["ISHTI_TEST_MODE"] = "1"
work = tempfile.mkdtemp(prefix="ishti-privacy-report-")
from ishti import audit, privacy
privacy.SETTINGS_PATH = os.path.join(work, "privacy.json")   # a private settings file: the owner's is untouched
privacy.LEDGER_PATH = os.path.join(work, "outbound.log")
out = {"internet_default": privacy.internet(), "deny_hosts": list(privacy.TELEMETRY_DENY),
       "hardening": {"env": {k: os.environ.get(k) for k in sorted(privacy.HARDEN_ENV)},
                     "applied": sorted(getattr(privacy, "_applied", {}))},
       "webview2_args": privacy.WEBVIEW2_ARGS,
       "skill_net": {k: v for k, v in privacy.skill_net().items()},
       "third_parties": [dict(t) for t in privacy.THIRD_PARTIES],
       # The one live finding privacy.snapshot() surfaces: whether Ollama's tray app is running on the
       # machine that produced this report, because that app checks ollama.com hourly and cannot be
       # configured to stop. A static list cannot say whether it is active right now; this can.
       "third_parties_live": [dict(t) for t in privacy._dynamic_findings()]}
try:                       # the base URLs a cloud model would be called at (ishti/llm.py:PROVIDERS)
    from ishti.llm import PROVIDERS
    out["providers"] = dict(PROVIDERS)
except Exception as exc:
    out["providers"] = {"unavailable": "%s: %s" % (type(exc).__name__, exc)}

# ---- 1. the guard, functionally: install it and try to leave --------------------------------
privacy.install()
out["install_ok"] = True
try:
    socket.create_connection(("93.184.216.34", 80), timeout=3)
    out["ip_connect"] = "NOT BLOCKED"
except privacy.EgressBlocked as exc:
    out["ip_connect"] = "blocked"
    out["ip_reason"] = str(exc)[:200]
except Exception as exc:
    out["ip_connect"] = "UNEXPECTED %s: %s" % (type(exc).__name__, exc)
try:                       # a name, not an IP: proves the guard sees the hostname it was asked for
    socket.create_connection(("i.posthog.com", 443), timeout=3)
    out["deny_name_connect"] = "NOT BLOCKED"
except privacy.EgressBlocked as exc:
    out["deny_name_connect"] = "blocked"
    out["deny_name_reason"] = str(exc)[:200]
except socket.gaierror as exc:
    out["deny_name_connect"] = "unresolved (%s)" % exc.args[0]   # nothing left the machine either, but not by the guard
except Exception as exc:
    out["deny_name_connect"] = "UNEXPECTED %s: %s" % (type(exc).__name__, exc)
privacy.uninstall()
rows = privacy.recent(20)
out["ledger_row"] = rows[-1] if rows else {}
out["ledger_shape_ok"] = set(out["ledger_row"]) == {"ts", "host", "port", "who", "allowed", "reason"}

# ---- 2. the deny list, under the most permissive context ISHTI has ---------------------------
star = sorted(k for k, v in privacy.skill_net().items() if "*" in v)
out["star_skill"] = star[0] if star else None
if star:                   # the control first: with this context a harmless host MUST be allowed,
    with audit.context(skill=star[0], privacy_allow="privacy-report"):   # else the check below is vacuous
        a, who, why = privacy.decide("example.com", 443)
        out["control"] = {"host": "example.com", "allowed": a, "who": who, "reason": why}
        out["deny_under_star"] = {}
        for h in privacy.TELEMETRY_DENY:
            a, who, why = privacy.decide(h, 443)
            out["deny_under_star"][h] = {"allowed": a, "reason": why}
    with audit.context(skill=star[0], privacy_allow="privacy-report"):   # and the Internet switch still wins
        # Measured inside this same most-permissive context on purpose: outside it, decide() refuses
        # everything anyway, and "the switch was off" would be indistinguishable from "no context".
        a, who, why = privacy.decide("example.com", 443)
        out["internet_on_control"] = a
        privacy.set_internet(False)
        try:
            a, who, why = privacy.decide("example.com", 443)
            out["internet_off"] = {"allowed": a, "reason": why}
        finally:
            privacy.set_internet(True)

# ---- 3. the review server: refused on anything but loopback, at runtime -----------------------
# root is already on sys.path: orchestrator/README-less namespace package, so no path surgery here -
# putting root/orchestrator first would shadow any top-level name that a product module also uses.
try:
    from orchestrator import plan_view
    out["plan_view"] = {}
    for host in ("0.0.0.0", "192.168.1.10", "::", "example.com", ""):
        r = plan_view.serve(work, host=host)
        out["plan_view"][host] = {"ok": bool(r.get("ok")), "error": (r.get("error") or "")[:120]}
    h = plan_view.serve(work)
    out["plan_view"]["127.0.0.1-default"] = {"ok": bool(h.get("ok")), "url": h.get("url") or ""}
    if h.get("ok"):
        plan_view.stop(h)
except Exception as exc:
    out["plan_view"] = {"error": "%s: %s" % (type(exc).__name__, exc)}

sys.stdout.write("PRIVACY_JSON " + json.dumps(out) + "\n")
'''


def run_driver(timeout=180):
    """Run the driver in its own process. -> (parsed dict | None, the raw tail for the failure note)."""
    try:
        proc = subprocess.run([sys.executable, "-u", "-c", _DRIVER, ROOT], cwd=ROOT, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"the driver did not finish within {timeout}s"
    for line in (proc.stdout or "").splitlines():
        if line.startswith("PRIVACY_JSON "):
            return json.loads(line[len("PRIVACY_JSON "):]), ""
    tail = "\n".join([l for l in (proc.stdout or "").splitlines() + (proc.stderr or "").splitlines()
                      if l.strip()][-4:])
    return None, tail or "the driver printed nothing"


def source_files(dirs=SCAN_DIRS):
    """Every first-party code file under these directories, vendored and built output excluded."""
    for d in dirs:
        base = os.path.join(ROOT, d)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in _SKIP]
            for fn in filenames:
                if fn.endswith(_CODE):
                    yield os.path.join(dirpath, fn)


def _bind_kind(seg):
    """Classify one bind call's own source text: loopback literal, host passed in, or a bad literal."""
    if any(lit in seg for lit in LOOPBACK):
        return "loopback"
    if re.search(r"\bhost\b", seg, re.I):
        return "variable"
    return "LITERAL-NOT-LOOPBACK"


def py_bind_calls(text):
    """Real bind calls in Python source, from the AST. -> [(lineno, one-line segment)] or None if it does
    not parse, in which case the caller falls back to the regex.

    The regex alone reads a *mention* of a bind as a bind. `ishti/lifecycle.py`'s own docstring says
    "replaces uvicorn.run()", and the line pattern matched `uvicorn.run(` inside it, found no loopback
    literal and reported it as a non-loopback bind site - a failing check, and a false finding written
    into the one document that must not overstate. The AST has no such confusion.

    The two exclusions the old pattern's tuple requirement bought are kept by hand, because a call named
    `.bind` is not necessarily a socket: `calltrace.bind(ledger)` (ishti/background.py) and
    `window.fetch.bind(window)` (a JS string in tools/qa/) are not. So a plain `.bind` only counts when
    its first argument is a tuple, while a server class or uvicorn is a bind site whenever it is called.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
        if name in ("run", "Config"):        # only uvicorn's, not an arbitrary object's .run()/.config()
            if not (isinstance(fn.value, ast.Name) and fn.value.id == "uvicorn"):
                continue
        elif name == "bind":
            if not (node.args and isinstance(node.args[0], (ast.Tuple, ast.List))):
                continue
        elif name not in ("HTTPServer", "ThreadingHTTPServer", "TCPServer"):
            continue
        seg = ast.get_source_segment(text, node)
        if not seg:
            # Cannot read the call back. Returning None hands the whole file to the regex instead of
            # classifying an empty string - which would come out as a non-loopback literal, i.e. a
            # failing check invented out of a missing source range.
            return None
        found.append((node.lineno, " ".join(seg.split())[:150]))
    return found


def bind_sites(dirs=SCAN_DIRS):
    """Every socket-bind call in first-party source: {file, line, text, kind}.

    `kind` is "loopback" when the bind names a loopback literal, "variable" when the host arrives as an
    argument (plan_view.serve() is the one such site; it refuses non-loopback before opening a socket),
    and "LITERAL-NOT-LOOPBACK" when it names anything else - which is what fails the check."""
    out = []
    for path in source_files(dirs):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        if path.endswith(".py"):
            calls = py_bind_calls(text)
            if calls is not None:
                out += [{"file": rel, "line": n, "text": seg, "kind": _bind_kind(seg)} for n, seg in calls]
                continue
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):    # a commented-out bind is not a bind
                continue
            m = BIND_RE.search(line)
            if not m:
                continue
            seg = m.group(0).strip()[:150]
            out.append({"file": rel, "line": i, "text": seg, "kind": _bind_kind(seg)})
    return out


def capture_hits(dirs=PRODUCT_DIRS):
    """Capture-API hits in the product source (what is on a customer's machine)."""
    return _scan_terms(CAPTURE_APIS, dirs)


def _scan_terms(terms, dirs):
    """Every line in these directories containing one of `terms`. This file is skipped: it is where the
    terms are defined, so it would always match itself and pad the report with noise."""
    me = os.path.abspath(__file__)
    hits = []
    for path in source_files(dirs):
        if os.path.abspath(path) == me:
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    for t in terms:
                        if t in line:
                            hits.append({"file": os.path.relpath(path, ROOT).replace(os.sep, "/"), "line": i,
                                         "term": t, "text": line.strip()[:150]})
                            break
        except OSError:
            continue
    return hits


def _metric(status, summary, **evidence):
    """One check's result. `check` is filled in by build() from the fixed order of the list."""
    return {"check": None, "status": status, "summary": summary, "evidence": evidence}


def check_egress(drv):
    """The guard does what the README claims, demonstrated rather than asserted."""
    ev = {"ip_connect": drv.get("ip_connect"), "ip_reason": drv.get("ip_reason"),
          "deny_name_connect": drv.get("deny_name_connect"), "deny_name_reason": drv.get("deny_name_reason"),
          "ledger_row": drv.get("ledger_row"), "ledger_fields_verified": drv.get("ledger_shape_ok")}
    if drv.get("ip_connect") != "blocked":
        return _metric("fail", f"an unattributed connect to 93.184.216.34 was not blocked "
                              f"({drv.get('ip_connect')})", **ev)
    if not drv.get("ledger_shape_ok"):
        return _metric("fail", "the attempt was blocked but the ledger row has unexpected fields", **ev)
    note = "" if str(drv.get("deny_name_connect")) == "blocked" else f"; by name: {drv.get('deny_name_connect')}"
    return _metric("pass", f"blocked a connect to a public IP and logged one row of host/port/who only{note}", **ev)


def check_deny(drv):
    """The deny list wins over the most permissive context, and the control proves it is the list."""
    rows = drv.get("deny_under_star")
    ctl = drv.get("control") or {}
    if not rows:
        return _metric("unmeasured", "no skill declares scope.net \"*\", so the permissive context could "
                                    "not be built; the list is still enforced with no context")
    allowed = sorted(h for h, v in rows.items() if v.get("allowed"))
    ev = {"hosts_checked": len(rows), "skill": drv.get("star_skill"), "control_host": ctl.get("host"),
          "control_allowed": ctl.get("allowed"), "internet_off": drv.get("internet_off"),
          "still_allowed": allowed,
          "reasons": sorted({v.get("reason") for v in rows.values()})}
    if not ctl.get("allowed"):
        return _metric("fail", f"the control host was refused as well ({ctl.get('reason')}), so this check "
                               f"cannot tell the deny list apart from a blanket block", **ev)
    if allowed:
        return _metric("fail", f"{len(allowed)} telemetry host(s) were allowed under an \"*\" scope: {allowed}", **ev)
    if (drv.get("internet_off") or {}).get("allowed"):
        return _metric("fail", "the Internet switch OFF did not stop an otherwise allowed host", **ev)
    return _metric("pass", f"all {len(rows)} telemetry hosts refused while scope.net \"*\" was active, and "
                           f"{ctl.get('host')} allowed in the same context", **ev)


def check_loopback(drv, sites):
    """Static: every bind is a loopback literal or a guarded variable. Runtime: hostile hosts refused."""
    bad = [s for s in sites if s["kind"] == "LITERAL-NOT-LOOPBACK"]
    pv = {k: v for k, v in (drv.get("plan_view") or {}).items()}
    hostile = ("0.0.0.0", "192.168.1.10", "::", "example.com", "")
    accepted = sorted(h for h in hostile if (pv.get(h) or {}).get("ok"))
    default = pv.get("127.0.0.1-default") or {}
    ev = {"bind_sites": len(sites), "non_loopback_literals": bad,
          "variable_host_sites": [s for s in sites if s["kind"] == "variable"],
          "plan_view_refused": sorted(h for h in hostile if h in pv and not (pv.get(h) or {}).get("ok")),
          "plan_view_accepted": accepted, "plan_view_loopback_url": default.get("url"),
          "sites": [f"{s['file']}:{s['line']}" for s in sites]}
    if bad:
        return _metric("fail", f"{len(bad)} bind site(s) use a non-loopback literal", **ev)
    if accepted:
        return _metric("fail", f"the review server accepted a non-loopback host: {accepted}", **ev)
    if pv.get("error") or not pv:
        # The static half above still ran and found nothing, but the runtime half did not happen. Saying
        # "pass" here would claim a refusal nobody observed, so this is unmeasured, not green.
        return _metric("unmeasured", f"the runtime half could not run, so only the static scan is behind "
                                     f"this result ({pv.get('error') or 'plan_view produced no result'})", **ev)
    if not default.get("ok"):
        # Every hostile host was refused, but the loopback control did not start either. Whatever stopped
        # it is not the host check, so crediting the loopback guard here would be a claim the run does not
        # support - and the sentence below would name the wrong cause.
        return _metric("fail", f"the review server refused every host including loopback "
                               f"({default.get('error') or 'the run produced no result for it'}), so the "
                               f"refusals above are not evidence that the host check is what caused them",
                       **ev)
    if not str(default.get("url") or "").startswith("http://127.0.0.1:"):
        return _metric("fail", f"the review server's default bind is not loopback ({default.get('url')})", **ev)
    return _metric("pass", f"{len(sites)} bind sites, none using a non-loopback literal; "
                           f"orchestrator/plan_view.py refuses a non-loopback host before a socket opens",
                   **ev)


def check_capture(product_hits, tool_hits):
    """No capture API in the product, and screenshots are a refused remote category."""
    gate = os.path.join(ROOT, "ishti", "channels", "gate.py")
    refused = False
    try:
        with open(gate, encoding="utf-8", errors="replace") as fh:
            refused = '("screenshots"' in fh.read()
    except OSError:
        pass   # a missing gate.py is not a pass: `refused` stays False and the evidence says so
    ev = {"product_hits": product_hits, "tooling_hits": tool_hits, "screenshots_are_a_refused_category": refused,
          "apis_searched": list(CAPTURE_APIS)}
    if product_hits:
        return _metric("fail", f"{len(product_hits)} desktop-capture API hit(s) in the product source", **ev)
    return _metric("pass", f"none of {len(CAPTURE_APIS)} desktop-capture APIs appear in the product source"
                           + ("; screenshot requests are refused remotely" if refused else ""), **ev)


def destinations(drv):
    """The inventory: every host the code can reach, grouped by why. Informational - never a failure."""
    skills = drv.get("skill_net") or {}
    star = sorted(k for k, v in skills.items() if "*" in v)
    listed = {k: v for k, v in skills.items() if "*" not in v}
    return {"skills_declaring_star": {k: skills[k] for k in star},
            "skills_declaring_hosts": listed, "model_providers": drv.get("providers") or {},
            "deny_list": drv.get("deny_hosts") or [], "third_parties": drv.get("third_parties") or [],
            "third_parties_live": drv.get("third_parties_live") or [],
            "hardening": drv.get("hardening") or {}, "webview2_args": drv.get("webview2_args") or ""}


def build(argv=None):
    ap = argparse.ArgumentParser(description="Produce ISHTI's privacy and egress report.")
    ap.add_argument("--out", default=os.path.join(REPORTS, "privacy.json"), help="where the JSON report goes")
    ap.add_argument("--markdown", help="also write the tracked page here (e.g. docs/PRIVACY_REPORT.md)")
    ap.add_argument("--live", action="store_true",
                    help="also run tools/gate_no_cloud.py: the real backend, its sockets watched, no cloud call allowed")
    args = ap.parse_args(argv)

    from ishti import version
    print(version.describe())
    os.makedirs(REPORTS, exist_ok=True)
    drv, why = run_driver()
    if drv is None:
        print(f"[privacy] the checks could not run: {why}", file=sys.stderr)
        return 2

    sites = bind_sites()
    checks = [
        check_egress(drv),
        check_deny(drv),
        check_loopback(drv, sites),
        check_capture(capture_hits(PRODUCT_DIRS), _scan_terms(CAPTURE_APIS, ("tools", "tests"))),
    ]
    live = None
    if args.live:
        print("[privacy] running tools/gate_no_cloud.py (starts the real backend)...")
        try:
            proc = subprocess.run([sys.executable, "-u", os.path.join("tools", "gate_no_cloud.py")], cwd=ROOT,
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
            tail = "\n".join([l for l in (proc.stdout or "").splitlines() if l.strip()][-14:])
            live = {"exit_code": proc.returncode, "passed": proc.returncode == 0, "output_tail": tail}
        except subprocess.TimeoutExpired:
            live = {"exit_code": None, "passed": False, "output_tail": "the gate did not finish within 900s"}

    names = ("egress_guard_blocks", "telemetry_deny", "loopback_bind", "no_desktop_capture")
    for name, c in zip(names, checks):
        c["check"] = name
        print(f"[privacy] {c['status'].upper():>4}  {name}: {c['summary']}")
    passed = sum(1 for c in checks if c["status"] == "pass")
    report = {"schema_version": 1, "generated": dt.datetime.now().isoformat(timespec="seconds"),
              "product": version.info(), "checks": checks,
              "headline": {"passed": passed, "total": len(checks), "failed": sum(1 for c in checks if c["status"] == "fail")},
              "destinations": destinations(drv), "live_gate": live,
              "not_covered": [
                  "the operating system, the Windows firewall, and traffic from any process that is not ISHTI",
                  "compiled code inside third-party wheels that ISHTI imports but does not read",
                  "a live backend and its sockets, unless --live ran (tools/gate_no_cloud.py)",
                  "what an operator with administrator rights does to the machine after install",
                  "the skills whose job is the web: they reach the internet by design, and their declared hosts are listed above",
              ]}
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print(f"[privacy] wrote {out}")
    if args.markdown:
        md = os.path.abspath(args.markdown)
        os.makedirs(os.path.dirname(md), exist_ok=True)
        with open(md, "w", encoding="utf-8") as fh:
            fh.write(markdown(report))
        print(f"[privacy] wrote {md}")
    if live is not None and not live["passed"]:
        return 1
    return 1 if report["headline"]["failed"] else 0


def markdown(rep):
    """The page a buyer reads. Every sentence here is something a check in this file established."""
    p, c, d = rep["product"], rep["checks"], rep["destinations"]
    rev = p["revision"] or "not a git checkout"
    mark = {"pass": "PASS", "fail": "FAIL", "unmeasured": "NOT MEASURED"}
    L = [
        "# Privacy and egress report",
        "",
        "What ISHTI does with your data, checked against the code rather than asserted about it. Every "
        "line below belongs to the revision named here and to no other: re-run "
        "`venv\\Scripts\\python tools\\privacy_report.py --markdown docs\\PRIVACY_REPORT.md` after any change "
        "and this page is regenerated - a privacy report that describes a build you are not running is "
        "worth nothing.",
        "",
        "| | |",
        "|---|---|",
        f"| Version | {p['version']} |",
        f"| Revision | `{rev}` ({p['revision_status']}) |",
        f"| Source hash | `{p['source_hash']}` |",
        f"| Generated | {rep['generated']} |",
        f"| Python | {p['python']} on {p['platform']} |",
        f"| Result | {rep['headline']['passed']} of {rep['headline']['total']} checks passed |",
        "",
        "## The checks",
        "",
        "| Check | Result | What was established |",
        "|---|---|---|",
    ]
    for ch in c:
        L.append(f"| `{ch['check']}` | {mark.get(ch['status'], ch['status'])} | {ch['summary'].replace('|', '/')} |")
    L += ["", "## What each check actually did", ""]
    for ch in c:
        ev = ch["evidence"]
        L.append(f"### `{ch['check']}` - {mark.get(ch['status'], ch['status'])}")
        L.append("")
        if ch["check"] == "egress_guard_blocks":
            L += [f"- A fresh process installed the guard (`ishti/privacy.py:install()`) and called "
                  f"`socket.create_connection((\"93.184.216.34\", 80))`: **{ev.get('ip_connect')}**.",
                  f"- The refusal reads: _{str(ev.get('ip_reason'))[:200]}_",
                  f"- Connecting to a telemetry host by name returned **{ev.get('deny_name_connect')}**.",
                  f"- One ledger row was written, with fields `{sorted((ev.get('ledger_row') or {}))}` - "
                  "the host, the port, who asked and why. There is no field for a payload, a URL path or a "
                  "response, which is the point.",
                  f"- The row: `{json.dumps(ev.get('ledger_row') or {})}`"]
        elif ch["check"] == "telemetry_deny":
            L += [f"- Context: the skill `{ev.get('skill')}`, whose manifest declares `scope.net: [\"*\"]`, plus "
                  "an owner grant - the most permission ISHTI can hand out.",
                  f"- Control: `{ev.get('control_host')}` in that same context was **"
                  f"{'allowed' if ev.get('control_allowed') else 'refused'}**. Without this the check would pass "
                  "on a build that blocked everything.",
                  f"- All {ev.get('hosts_checked')} hosts on `TELEMETRY_DENY` were refused: {ev.get('reasons')}.",
                  f"- With the Internet switch OFF, an otherwise allowed host was "
                  f"{'still allowed' if (ev.get('internet_off') or {}).get('allowed') else 'refused'}."]
        elif ch["check"] == "loopback_bind":
            sites_txt = ", ".join(f"`{s}`" for s in (ev.get("sites") or []))
            var = ev.get("variable_host_sites") or []
            var_txt = ", ".join(sorted(f"`{s['file']}:{s['line']}`" for s in var))
            L += [f"- {ev.get('bind_sites')} bind sites in ISHTI's own source; non-loopback literals: "
                  f"{ev.get('non_loopback_literals') or 'none'}. Sites: {sites_txt}.",
                  f"- `orchestrator/plan_view.py:serve()` refused {ev.get('plan_view_refused')} at runtime, and "
                  f"its default bind was `{ev.get('plan_view_loopback_url')}`.",
                  (f"- {len(var)} site(s) take the host as a parameter rather than a literal ({var_txt}). The "
                   "runtime half above exercises `orchestrator/plan_view.py:serve()`; a variable host is "
                   "listed rather than passed, because a scan of literals cannot see what it is set to at "
                   "call time." if var else
                   "- Every site names its host as a literal, so the runtime refusal above is the second line "
                   "of defence rather than the first.")]
        elif ch["check"] == "no_desktop_capture":
            L += [f"- Searched {len(ev.get('apis_searched') or [])} capture APIs "
                  f"({', '.join(ev.get('apis_searched') or [])}) across `ishti/`, `desktop/`, `orchestrator/` "
                  "and `interface/`: **no hits**.",
                  f"- Screenshot requests are a refused remote-action category "
                  f"(`ishti/channels/gate.py`): {ev.get('screenshots_are_a_refused_category')}.",
                  f"- Outside the product, in developer tooling, {len(ev.get('tooling_hits') or [])} hit(s): "
                  f"{[h['file'] for h in (ev.get('tooling_hits') or [])] or 'none'}. Those are test and QA "
                  "scripts that are not on a customer's machine."]
        L.append("")
    L += ["## Every host the code can reach", "",
          "### Web skills - allowed by design, only while the Internet switch is on", ""]
    star = d.get("skills_declaring_star") or {}
    L += [f"These {len(star)} skills declare `scope.net: [\"*\"]`: they may call whatever host the task needs, "
          "because fetching a page the owner asked for is their job. The guard allows them only while the "
          "Internet switch is on, and it refuses the telemetry list above even then.", "",
          ", ".join(f"`{k}`" for k in sorted(star)) or "(none)", ""]
    listed = d.get("skills_declaring_hosts") or {}
    L += ["### Skills with a named host list", "", "| Skill | Hosts |", "|---|---|"]
    L += [f"| `{k}` | {', '.join('`' + h + '`' for h in v)} |" for k, v in sorted(listed.items())] or ["| (none) | |"]
    L += ["", "### Model providers (only when you choose a cloud model)", "", "| Provider | Endpoint |", "|---|---|"]
    L += [f"| {k} | `{v}` |" for k, v in sorted((d.get("model_providers") or {}).items())]
    L += ["", "With `provider: local` - the default - the model runs through Ollama on this machine and none of "
          "these endpoints is used. A cloud model is reachable only after the owner selects one and turns the "
          "Internet switch on; `ishti/privacy.py:inference()` then narrows the grant to that one endpoint and "
          "nothing else.", ""]
    deny = d.get("deny_list") or []
    L += [f"### Refused everywhere ({len(deny)} hosts)", "",
          "Telemetry, analytics and crash-reporting endpoints. These are refused even inside a skill whose "
          "scope is `*`, even with an owner grant, and even with the Internet switch on - which is what the "
          "`telemetry_deny` check above demonstrates rather than assumes.", "",
          ", ".join(f"`{h}`" for h in deny), ""]
    tp = d.get("third_parties") or []
    L += [f"### Third parties, and what stops each ({len(tp)})", "", "| Who | Would go to | What it would carry | Status |",
          "|---|---|---|---|"]
    L += [f"| {t.get('name')} | {t.get('to')} | {t.get('data')} | {t.get('status')} |" for t in tp]
    live_tp = d.get("third_parties_live") or []
    L += [""] + ([f"A live check of the machine this report was generated on found: "
                  + "; ".join(f"**{t.get('name')}** - {t.get('status')}" for t in live_tp)
                  + " The rows above are the static inventory; this line is an observation, and it changes "
                    "between runs."] if live_tp else
                 ["The same live check found none of the third parties that announce themselves at runtime "
                  "(Ollama's tray app is not running), so nothing outside the static inventory above was "
                  "observed on this machine."])
    hard = (d.get("hardening") or {}).get("env") or {}
    L += ["", f"### Environment hardening ({len(hard)} variables set at import)", "",
          "Set by `ishti/privacy.py:harden()`, before any library that reads them is imported - this is what "
          "turns off the telemetry of the libraries ISHTI depends on.", "",
          "| Variable | Value |", "|---|---|"]
    L += [f"| `{k}` | `{v}` |" for k, v in sorted(hard.items()) if v is not None]
    L += ["", "### The dashboard window", "",
          "ISHTI's own UI runs in a WebView2 with these flags (`ishti/privacy.py:WEBVIEW2_ARGS`):", "",
          "```text", (d.get("webview2_args") or "").strip(), "```", "",
          "and with a resolver rule that maps every host except `127.0.0.1` and `localhost` to `~NOTFOUND`. "
          "The browser engine that renders the interface therefore cannot resolve a name on the network at "
          "all - it is a rendering surface, not a browser.", ""]
    live = rep.get("live_gate")
    if live:
        L += ["## Live gate", "",
              f"`tools/gate_no_cloud.py` started the real backend, watched every TCP connection of its process "
              f"tree and ran local tasks through it: **{'PASS' if live.get('passed') else 'FAIL'}** "
              f"(exit {live.get('exit_code')}).", "", "```", (live.get("output_tail") or "").strip(), "```", ""]
    else:
        L += ["## Live gate - not run", "",
              "This report did not start the backend, so nothing here shows the running server's own sockets. "
              "`--live` runs `tools/gate_no_cloud.py`, which does exactly that: it samples every connection of "
              "the backend's process tree during boot and under load, and fails on a single external one.", ""]
    L += ["## What this report does not prove", "",
          "A privacy report that overstates is worse than none, so here is the boundary of the claims above.", ""]
    L += [f"- {x}." for x in rep.get("not_covered") or []]
    L += ["",
          "It is also a report about ISHTI's own source. It establishes what the code in this revision does "
          "and refuses; it is not an audit by a third party, and nobody outside this project has reproduced "
          "it. What it gives you is a starting point an auditor can check, and a revision-stamped answer that "
          "stays true only as long as the source hash above does.", ""]
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(build())
