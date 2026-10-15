"""ISHTI's evidence report for one revision:  python tools/evidence.py

Answers the question a buyer asks first - "how well does it actually work, and on which build?" - with
one command that produces one file. Three sources, all offline:

  core    the curated pytest set CI runs (the eight files listed below, which live here and nowhere
          else so there is exactly one copy of that list). Graded pass/fail from pytest's JUnit XML.
  eval    tests/eval's five offline suites through tests/eval/harness.py:run_suites. Metrics only.
  skills  the per-skill suites under tests/skills, driven by tests/run_skill.py. pytest.ini excludes
          that directory, so this is the only thing that runs them.

Every number carries the revision that produced it (ishti/version.py) and is either measured with a
numerator and a denominator or `unmeasured` **with a stated reason** - never a zero standing in for
"nobody looked". That shape is tests/eval/harness.py:metric(), reused rather than reinvented.

    venv\\Scripts\\python tools\\evidence.py                   # core + eval + skills
    venv\\Scripts\\python tools\\evidence.py --no-skills       # offline and quick (what CI runs)
    venv\\Scripts\\python tools\\evidence.py --markdown docs\\EVIDENCE.md

Writes reports/evidence.json - machine-readable and complete, and gitignored, like every other report.
`--markdown` additionally refreshes the tracked human-readable summary; commit that file when the
numbers change, because a number that lives only in reports/ is one `git clean` away from gone
(docs/ACCURACY.md records that this nearly happened once).

Exit: 0 when the curated core passed, 1 when it did not, 2 when a source could not run at all.
`--strict-skills` also fails on a red per-skill suite; the nightly job uses it.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
REPORTS = os.path.join(ROOT, "reports")

# The curated offline core. .github/workflows/ci.yml calls this tool instead of naming them itself.
CORE_TESTS = ("tests/test_brain.py", "tests/test_llm_provider.py", "tests/test_market_readiness.py",
              "tests/test_workflow_continuation.py", "tests/test_business_desk.py",
              "tests/test_audit_fixes.py", "tests/test_interoperability.py", "tests/test_searxng_heal.py")


def metric(**kw):
    """tests/eval/harness.py:metric - imported late, because importing it boots product modules."""
    from tests.eval.harness import metric as _metric
    return _metric(**kw)


def run(cmd, timeout):
    """Run a child process. -> (returncode | None on timeout, tail of its output)."""
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"the step did not finish within {timeout}s"
    tail = "\n".join([l for l in (proc.stdout or "").splitlines() if l.strip()][-4:])
    return proc.returncode, tail or (proc.stderr or "").strip()[-400:]


def junit_totals(path):
    """(tests, failures, errors, skipped) from a JUnit XML file, or None when it is missing/unreadable."""
    try:
        suites = list(ET.parse(path).getroot().iter("testsuite"))
    except (OSError, ET.ParseError):
        return None
    return tuple(sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped"))


def section_core(junit_path):
    """The curated offline set: pass/fail, and the JUnit record that proves the run happened."""
    started = time.perf_counter()
    code, tail = run([sys.executable, "-u", "-X", "faulthandler", "-m", "pytest", *CORE_TESTS,
                      "-p", "no:cacheprovider", f"--junitxml={junit_path}"], timeout=3600)
    totals = junit_totals(junit_path)
    detail = {"command": " ".join(CORE_TESTS), "exit_code": code, "tail": tail,
              "junit": junit_path.replace(ROOT + os.sep, ""), "seconds": round(time.perf_counter() - started, 1)}
    if totals is None:
        return {"metric": metric(measured=False, reason=f"pytest produced no JUnit record ({tail[-160:]})"),
                "detail": detail, "failed": True}
    tests, failures, errors, skipped = totals
    if tests == 0:
        return {"metric": metric(measured=False, reason="the JUnit record contains no tests at all"),
                "detail": detail, "failed": True}
    passed = tests - failures - errors - skipped
    detail.update(tests=tests, failures=failures, errors=errors, skipped=skipped)
    return {"metric": metric(measured=True, value=passed / tests, numerator=passed, denominator=tests),
            "detail": detail, "failed": bool(failures or errors) or code != 0}


def section_eval(repeat, out_path):
    """The five offline suites. Emits every metric the harness produced, measured or not."""
    from tests.eval.harness import concise_table, run_suites
    started = time.perf_counter()
    report = run_suites("all", repeat=repeat)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    rows = []
    for suite in report["results"]:
        for name, item in suite["metrics"].items():
            rows.append({"suite": suite["suite"], "metric": name, **item})
    measured = [r for r in rows if r["status"] == "measured"]
    headline = (metric(measured=True, value=len(measured) / len(rows), numerator=len(measured), denominator=len(rows))
                if rows else metric(measured=False, reason="no eval suite emitted a metric"))
    return {"metric": headline, "detail": {"repeat": repeat, "seconds": round(time.perf_counter() - started, 1),
                                            "report": out_path.replace(ROOT + os.sep, ""),
                                            "table": concise_table(report), "rows": rows}}


def section_skills(runs, timeout_s, wait_idle, force, budget_s):
    """The per-skill suites, through tests/run_skill.py so there is one runner and one policy."""
    detail_path = os.path.join(REPORTS, "skills.json")
    summary_path = os.path.join(REPORTS, "skills_summary.json")
    for stale in (detail_path, summary_path):
        if os.path.exists(stale):
            os.remove(stale)   # never read a previous run's numbers as this run's
    cmd = [sys.executable, "tests/run_skill.py", "all", "--runs", str(runs), "--timeout", str(timeout_s),
           "--json", "--out", detail_path, "--summary", summary_path, "--wait-idle", str(wait_idle)]
    if force:
        cmd.append("--force")
    started = time.perf_counter()
    code, tail = run(cmd, timeout=budget_s)
    seconds = round(time.perf_counter() - started, 1)
    detail = {"command": " ".join(cmd[1:]), "exit_code": code, "seconds": seconds, "tail": tail,
              "detail": detail_path.replace(ROOT + os.sep, ""), "budget_s": budget_s}
    try:
        with open(summary_path, encoding="utf-8") as fh:
            summary = json.load(fh)
    except (OSError, ValueError):
        reason = ("the GPU was too busy to start a batch and the wait expired" if code == 3 else
                  f"tests/run_skill.py produced no aggregate ({tail[-160:]})")
        return {"metric": metric(measured=False, reason=reason), "detail": detail}
    graded, green = summary.get("graded", 0), summary.get("green", 0)
    if not graded:
        return {"metric": metric(measured=False, reason="no suite under tests/skills collected any test"),
                "detail": detail}
    detail.update(summary["counts"], flagged=summary.get("flagged", []))
    return {"metric": metric(measured=True, value=green / graded, numerator=green, denominator=graded),
            "detail": detail}


def section_privacy(path):
    """Optional: the privacy report tools/privacy_report.py writes, folded in when it is present."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"metric": metric(measured=False, reason="not run; produce it with tools/privacy_report.py"),
                "detail": {"report": path.replace(ROOT + os.sep, "")}}
    checks = data.get("checks", [])
    passed = [c for c in checks if c.get("status") == "pass"]
    detail = {"report": path.replace(ROOT + os.sep, ""), "revision": data.get("product", {}).get("revision"),
              "checks": [{"check": c.get("check"), "status": c.get("status")} for c in checks]}
    if not checks:
        return {"metric": metric(measured=False, reason="the privacy report contained no checks"), "detail": detail}
    return {"metric": metric(measured=True, value=len(passed) / len(checks), numerator=len(passed),
                            denominator=len(checks)), "detail": detail}


def summarize(sections):
    flat = [{"section": name, **sec["metric"]} for name, sec in sections.items()]
    measured = [m for m in flat if m["status"] == "measured"]
    unmeasured = [m for m in flat if m["status"] != "measured"]
    return {"measured": len(measured), "unmeasured": len(unmeasured), "sections": flat,
            "unmeasured_reasons": {m["section"]: m.get("reason") for m in unmeasured}}


def markdown(evidence):
    """The tracked, human-readable form of reports/evidence.json (docs/EVIDENCE.md)."""
    product, sections = evidence["product"], evidence["sections"]
    rev = product["revision"] or "not a git checkout"
    lines = [
        "# Evidence for this revision",
        "",
        "Generated by `venv\\Scripts\\python tools\\evidence.py --markdown docs\\EVIDENCE.md` on "
        f"{evidence['generated']}. Every number below belongs to the revision named here and to no other - "
        "a result is only valid for the code that produced it.",
        "",
        "| | |",
        "|---|---|",
        f"| Version | {product['version']} |",
        f"| Revision | `{rev}` ({product['revision_status']}) |",
        f"| Source hash | `{product['source_hash']}` |",
        f"| Hash covers | {', '.join(product['source_hash_covers'])}, and the root build files listed in "
        f"`ishti/version.py:SOURCE_FILES` (the `source_hash_covers` field names the directories only) |",
        f"| Python | {product['python']} on {product['platform']} |",
        "",
    ]
    for name, title in (("core", "Curated offline tests"), ("eval", "Offline evaluation suites"),
                        ("skills", "Per-skill suites"), ("privacy", "Privacy and egress")):
        sec = sections.get(name)
        if not sec:
            continue
        m, d = sec["metric"], sec["detail"]
        lines += [f"## {title}", ""]
        lines.append(f"- {_shown(m)}" + (f"  ({d['seconds']}s)" if d.get("seconds") else ""))
        if name == "core" and d.get("tests"):
            lines.append(f"- tests {d['tests']}, failures {d['failures']}, errors {d['errors']}, skipped {d['skipped']}")
        if name == "eval":
            lines += ["", "```", (d.get("table") or "").strip(), "```"]
        if name == "skills":
            counts = {k: v for k, v in d.items() if k in ("green", "red", "no-suite", "SAFETY-FAIL")}
            lines.append(f"- {counts}")
            for bad in (d.get("flagged") or []):
                lines.append(f"- **{bad['status']}** `{bad['skill']}`: {str(bad.get('last_error'))[:180]}")
        lines.append("")
    unmeasured = evidence["summary"]["unmeasured_reasons"]
    lines += ["## Not measured", "",
              "Stated as a gap, because a gap a reader can trust you about is worth more than a number "
              "nobody checked.", ""]
    lines += [f"- **{k}**: {v}" for k, v in unmeasured.items()] or ["- (nothing: every source reported a number)"]
    lines += ["",
              "For the accuracy picture as a whole - including what has never been measured at all, such as "
              "voice accuracy in any language and behaviour on hardware other than the author's PC - read "
              "[ACCURACY.md](ACCURACY.md), which this file does not replace.",
              ""]
    return "\n".join(lines)


def _shown(m):
    if m["status"] != "measured":
        return f"UNMEASURED - {m.get('reason')}"
    # harness._ratio() reports value=None when the denominator is 0 - a measured zero is honest, but
    # formatting it as a float would raise here, so say what it is instead.
    if m.get("value") is None:
        return f"{m.get('numerator')}/{m.get('denominator')} = not computable (the denominator is empty)"
    return f"{m['numerator']}/{m['denominator']} = {m['value']:.4f}"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Produce ISHTI's evidence report for this revision.")
    ap.add_argument("--out", default=os.path.join(REPORTS, "evidence.json"), help="where the JSON report goes")
    ap.add_argument("--markdown", help="also write the tracked summary to this path (e.g. docs/EVIDENCE.md)")
    ap.add_argument("--no-core", action="store_true", help="skip the curated pytest set")
    ap.add_argument("--no-eval", action="store_true", help="skip the offline evaluation suites")
    ap.add_argument("--no-skills", action="store_true", help="skip the per-skill suites (they need a GPU)")
    ap.add_argument("--strict-skills", action="store_true", help="exit 1 if any per-skill suite is red")
    ap.add_argument("--repeat", type=int, default=1, help="independent runs of each eval suite (default 1)")
    ap.add_argument("--runs", type=int, default=1, help="repeats per skill suite (default 1)")
    ap.add_argument("--skill-timeout", type=int, default=90, help="per-test timeout for a skill suite")
    ap.add_argument("--skill-budget", type=int, default=5400, help="give up on the skill suites after this many seconds")
    ap.add_argument("--wait-idle", type=int, default=300, help="wait up to S seconds for a quiet GPU before the skill suites")
    ap.add_argument("--force-skills", action="store_true", help="run the skill suites even if the GPU is busy")
    args = ap.parse_args(argv)
    if args.repeat < 1 or args.runs < 1:
        ap.error("--repeat and --runs must be at least 1")

    os.makedirs(REPORTS, exist_ok=True)
    from ishti import version

    product = version.info()
    print(version.describe())
    # Scratch mode for everything below, the way tests/eval/run.py does it: the curated set, the eval
    # suites and the skill runs all write into one throwaway dir instead of the owner's data/.
    with tempfile.TemporaryDirectory(prefix="ishti-evidence-", ignore_cleanup_errors=True) as scratch:
        os.environ["ISHTI_TEST_MODE"] = "1"
        os.environ["ISHTI_SCRATCH"] = "1"
        os.environ["ISHTI_SCRATCH_DIR"] = scratch
        os.environ.setdefault("ISHTI_OFFLINE_EVAL", "1")
        sections = {}
        if not args.no_core:
            print("[evidence] curated offline tests...")
            sections["core"] = section_core(os.path.join(REPORTS, "ci-core.xml"))
            print(f"[evidence]   {_shown(sections['core']['metric'])}")
        if not args.no_eval:
            print("[evidence] offline evaluation suites...")
            sections["eval"] = section_eval(args.repeat, os.path.join(REPORTS, "eval_all.json"))
            print(f"[evidence]   {_shown(sections['eval']['metric'])}")
        if not args.no_skills:
            print(f"[evidence] per-skill suites (up to {args.skill_budget}s)...")
            sections["skills"] = section_skills(args.runs, args.skill_timeout, args.wait_idle,
                                                args.force_skills, args.skill_budget)
            print(f"[evidence]   {_shown(sections['skills']['metric'])}")
        sections["privacy"] = section_privacy(os.path.join(REPORTS, "privacy.json"))

        evidence = {"schema_version": 1, "generated": dt.datetime.now().isoformat(timespec="seconds"),
                    "product": product, "sections": sections, "summary": summarize(sections)}
        out = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(evidence, fh, indent=2, ensure_ascii=False)
        if args.markdown:
            md = os.path.abspath(args.markdown)
            os.makedirs(os.path.dirname(md), exist_ok=True)
            with open(md, "w", encoding="utf-8") as fh:
                fh.write(markdown(evidence))
            print(f"[evidence] wrote {md}")
        print(f"[evidence] wrote {out}")

    summary = evidence["summary"]
    print(f"[evidence] {summary['measured']} measured section(s), {summary['unmeasured']} unmeasured")
    for name, reason in summary["unmeasured_reasons"].items():
        print(f"[evidence]   unmeasured {name}: {reason}")
    core = sections.get("core")
    if core and core.get("failed"):
        return 1
    if args.strict_skills and sections.get("skills", {}).get("detail", {}).get("red"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
