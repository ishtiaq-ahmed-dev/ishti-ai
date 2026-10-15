from __future__ import annotations

import ast
import datetime as dt
import json
import importlib.util
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
CASE_DIR = ROOT / "tests" / "task_suite" / "cases"


def metric(*, measured: bool, value: float | None = None, numerator: int | None = None,
           denominator: int | None = None, reason: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"status": "measured" if measured else "unmeasured"}
    if measured:
        row.update(value=value, numerator=numerator, denominator=denominator)
    else:
        row["reason"] = reason or "No ground truth is available."
    return row


def _ratio(correct: int, total: int) -> dict[str, Any]:
    return metric(measured=True, value=(correct / total if total else None),
                  numerator=correct, denominator=total)


def _case_rows() -> list[dict[str, Any]]:
    rows = []
    for path in sorted(CASE_DIR.glob("*.json"), key=lambda p: int(p.stem)):
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    return rows


SYNTHETIC_UNDERSTANDING_CASES = [
    {"id": "eval-urdu-1", "prompt_text": "آج موسم کیسا ہے؟", "expected": ["weather"], "tags": ["urdu"]},
    {"id": "eval-pashto-1", "prompt_text": "نن هوا څنګه ده؟", "expected": ["weather"], "tags": ["pashto"]},
    {"id": "eval-roman-urdu-1", "prompt_text": "aaj mausam kaisa hai", "expected": ["weather"], "tags": ["roman_urdu"]},
    {"id": "eval-mixed-1", "prompt_text": "Please آج weather بتائیں", "expected": ["weather"], "tags": ["mixed"]},
    {"id": "eval-typo-1", "prompt_text": "open google calssrom", "expected": ["browser"], "tags": ["english", "typo"]},
    {"id": "eval-vague-1", "prompt_text": "Delete the old file", "expected": ["chat"], "tags": ["english", "vague_ref", "ambiguity"]},
    {"id": "eval-ambiguous-1", "prompt_text": "Send it to him", "expected": ["chat"], "tags": ["english", "vague_ref", "ambiguity"]},
    {"id": "eval-multistep-1", "prompt_text": "Find my report and then summarize it", "expected": ["files"], "tags": ["english", "multi_step"]},
    {"id": "eval-roman-urdu-2", "prompt_text": "kal 8 baje yaad dila dena", "expected": ["reminders"], "tags": ["roman_urdu"]},
    {"id": "eval-mixed-2", "prompt_text": "میری file find کریں", "expected": ["files"], "tags": ["mixed"]},
]


def corpus_coverage(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {name: 0 for name in (
        "english", "urdu", "pashto", "roman_urdu", "mixed", "typo", "vague_ref", "multi_step", "ambiguity"
    )}
    typo = re.compile(r"\b(?:calssrom|clasrom|wahstap|whstap|assigment|asigment|downlaods|pleae)\b", re.I)
    vague = re.compile(r"\b(?:it|them|him|her|that one|this one|same thing|old file|there)\b", re.I)
    multistep = re.compile(r"\b(?:and then|then|after that|first.+then|and also)\b", re.I)
    roman = re.compile(r"\b(?:mausam|barish|yaad dila|kar do|karo|baje|kaisa|aaj|kal)\b", re.I)
    for row in rows:
        text = str(row.get("prompt_text") or "")
        tags = set(row.get("tags") or [])
        if not tags & {"english", "urdu", "pashto", "roman_urdu", "mixed"}:
            has_arabic = bool(re.search(r"[\u0600-\u06ff]", text))
            has_latin = bool(re.search(r"[A-Za-z]", text))
            if has_arabic and has_latin:
                tags.add("mixed")
            elif roman.search(text):
                tags.add("roman_urdu")
            elif has_latin:
                tags.add("english")
        if typo.search(text):
            tags.add("typo")
        if vague.search(text):
            tags.add("vague_ref")
        if multistep.search(text):
            tags.add("multi_step")
        category = str(row.get("category") or "").lower()
        if "ambiguous" in category or "vague or incomplete" in category:
            tags.add("ambiguity")
        for tag in counts.keys() & tags:
            counts[tag] += 1
    return counts


@dataclass
class ManifestSkill:
    """A matcher-backed skill adapter; importing it never executes the skill."""

    name: str
    manifest: dict[str, Any]
    module: Any = None
    problem: str = ""

    @property
    def status(self) -> str:
        return "broken" if self.problem else str(self.manifest.get("status", "working"))

    def matches(self, text: str) -> bool:
        fn = getattr(self.module, "match", None)
        try:
            return bool(fn and fn(text))
        except Exception:
            return False


def _offline_skills() -> dict[str, ManifestSkill]:
    """Load manifest metadata and deterministic matchers, but never call run()."""
    import importlib

    found: dict[str, ManifestSkill] = {}
    base = ROOT / "ishti" / "skills"
    for path in sorted(base.rglob("manifest.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        name = str(data.get("name") or path.parent.name)
        rel = path.parent.relative_to(base)
        module_name = "ishti.skills." + ".".join(rel.parts) + ".skill"
        try:
            module = importlib.import_module(module_name)
            found[name] = ManifestSkill(name, data, module)
        except Exception as exc:
            found[name] = ManifestSkill(name, data, problem=f"{type(exc).__name__}: {exc}")
    return found


def run_understanding() -> dict[str, Any]:
    from ishti import tool_routing
    from orchestrator.intent_detector import IntentDetector

    corpus_rows = _case_rows()
    rows = [*corpus_rows, *[
        {"id": row["id"], "prompt_text": row["prompt_text"], "tags": row["tags"],
         "pass_criteria": {"expected_skill": row["expected"]}, "category": "eval-only synthetic"}
        for row in SYNTHETIC_UNDERSTANDING_CASES
    ]]
    skills = _offline_skills()
    details = []
    skill_correct = skill_total = 0
    intent_correct = intent_total = 0
    intent_map = {
        "weather": "WEATHER", "deep_research": "DEEP_RESEARCH",
        "web_fetch": "WEB_FETCH", "files": "PC_SEARCH",
        "browser": "BROWSER_FORM", "video": "VIDEO_EDIT",
    }
    for case in rows:
        prompt = str(case.get("prompt_text") or "")
        expected = list(case.get("pass_criteria", {}).get("expected_skill") or [])
        # Learned hints live in a persistent store and are owner-specific, so they are
        # deliberately excluded from this corpus measurement.
        original_hint = tool_routing._learned_hint
        tool_routing._learned_hint = lambda _text: None
        try:
            decision = tool_routing.route(
                prompt, skills, session={"history": []})
        finally:
            tool_routing._learned_hint = original_hint
        actual = str(decision.get("skill") or "")
        if expected:
            skill_total += 1
            ok = actual in expected
            skill_correct += int(ok)
            if not ok:
                details.append({"id": case.get("id"), "field": "skill", "expected": expected,
                                "actual": actual, "source": decision.get("source")})
        expected_intents = {intent_map[x] for x in expected if x in intent_map}
        if expected_intents:
            intent_total += 1
            actual_intent = IntentDetector.classify(prompt).get("mode")
            ok = actual_intent in expected_intents
            intent_correct += int(ok)
            if not ok:
                details.append({"id": case.get("id"), "field": "intent",
                                "expected": sorted(expected_intents), "actual": actual_intent})
    return {
        "suite": "understanding", "case_count": len(rows),
        "metrics": {
            "skill_accuracy": _ratio(skill_correct, skill_total),
            "intent_accuracy": _ratio(intent_correct, intent_total),
            "argument_accuracy": metric(measured=False, reason="The 300-case corpus has no structured expected argument labels."),
            "completion_accuracy": metric(measured=False, reason="Routing-only execution produces no completion outcome."),
            "clarification_accuracy": metric(measured=False, reason="The corpus has no structured clarification-required label."),
        },
        "failures": details,
        "metadata": {"corpus": "tests/task_suite/cases/*.json", "structured_corpus_cases": len(corpus_rows),
                     "eval_only_synthetic_cases": len(SYNTHETIC_UNDERSTANDING_CASES),
                     "coverage_counts": corpus_coverage(rows),
                     "coverage_method": "explicit eval tags plus documented regex heuristics",
                     "offline_llm": "fixed empty JSON"},
    }


_FAILURE = re.compile(
    r"\b(?:can(?:not|'t)|couldn(?:'t| not)|unable|failed|failure|not found|no (?:results?|files?)|nothing found|error|sorry)\b",
    re.I,
)
_RECOVERY = re.compile(
    r"\b(?:try|instead|provide|give me|narrow|specify|choose|which|what exact|where(?: is| should| exact)|"
    r"unlock|upload|attach|next (?:step|option)|can you (?:provide|choose|open|upload)|"
    r"could you (?:provide|choose|open|upload))\b",
    re.I,
)


def detect_dead_end(reply: Any) -> dict[str, Any]:
    text = str(reply or "").strip()
    reasons = []
    if not text:
        reasons.append("empty_reply")
    if re.search(r"did you mean\s*:?\s*$", text, re.I):
        reasons.append("empty_disambiguation")
    if re.search(r"\b(?:todo|not implemented|coming soon)\b", text, re.I):
        reasons.append("placeholder")
    if _FAILURE.search(text) and not _RECOVERY.search(text):
        reasons.append("failure_without_recovery")
    return {"dead_end": bool(reasons), "reasons": reasons}


def run_dead_end() -> dict[str, Any]:
    cases = [
        ("", True), ("Sorry, I can't do that.", True), ("No results found.", True),
        ("Did you mean:", True), ("TODO", True),
        ("I couldn't find it. Which folder should I search?", False),
        ("No results; try a narrower filename.", False), ("I couldn't open it. Did that help?", True),
        ("No files.", True),
        ("That failed, but I can use the local copy instead.", False),
        ("Done: saved the synthetic report.", False), ("What exact file do you mean?", False),
    ]
    correct = sum(detect_dead_end(text)["dead_end"] == label for text, label in cases)
    fallbacks = [
        ("No results found.", "Try a narrower filename.", True),
        ("Failed.", "", False),
        ("Unable to parse.", "Which format should I use?", True),
        ("Sorry, not found.", "No results found.", False),
    ]
    recovered = 0
    fallback_correct = 0
    for primary, fallback, expected in fallbacks:
        outcome = detect_dead_end(primary)["dead_end"] and bool(fallback) and not detect_dead_end(fallback)["dead_end"]
        recovered += int(outcome)
        fallback_correct += int(outcome == expected)
    product_rows = _product_failure_outcomes()
    measured_product = [row for row in product_rows if row["status"] == "measured"]
    handled = sum(not row["dead_end"] for row in measured_product)
    return {"suite": "dead_end", "case_count": len(cases) + len(fallbacks) + len(product_rows),
            "metrics": {"detector_accuracy": _ratio(correct, len(cases)),
                        "fallback_outcome_accuracy": _ratio(fallback_correct, len(fallbacks)),
                        "fallback_recovery_rate": _ratio(recovered, len(fallbacks)),
                        "dependency_boundary_handling_rate": (_ratio(handled, len(measured_product))
                                                               if measured_product else metric(
                                                                   measured=False, reason="No safe dependency boundary was measurable.")),
                        "product_failure_handling_rate": metric(
                            measured=False,
                            reason="Direct dependency probes bypass dispatcher/Arch2 recovery; orchestrated outcome is not yet measured."),
                        "orchestrated_recovery_rate": metric(
                            measured=False,
                            reason="No injectable end-to-end recovery seam exists before WP-B.")},
            "failures": [row for row in product_rows if row.get("dead_end")],
            "metadata": {"detector_calibration_cases": len(cases), "product_scenarios": product_rows}}


def _product_failure_outcomes() -> list[dict[str, Any]]:
    """Inject failures at product dependency boundaries; never contact a network or real store."""
    from types import SimpleNamespace

    from desktop import computer_use
    from ishti import document_reader
    from ishti.skills.docs import skill as docs_skill
    from ishti.skills.files import skill as files_skill
    from ishti.skills.utilities import skill as utilities_skill

    rows = []

    def record(name: str, call: Callable[[], Any], fallback: str = "") -> None:
        try:
            out = call()
            reply = out.get("reply", "") if isinstance(out, dict) else str(out or "")
            state = detect_dead_end(reply)
            rows.append({"scenario": name, "status": "measured", "reply": reply[:240],
                         "expected_recovery": fallback, "fallback_executed": False,
                         "boundary_reached": True, "dead_end": state["dead_end"], "reasons": state["reasons"]})
        except Exception as exc:
            rows.append({"scenario": name, "status": "measured", "reply": "",
                         "expected_recovery": fallback, "fallback_executed": False,
                         "boundary_reached": True, "dead_end": True,
                         "reasons": [f"exception:{type(exc).__name__}"]})

    with tempfile.TemporaryDirectory(prefix="ishti-eval-failures-") as raw:
        folder = Path(raw)
        missing = folder / "missing.txt"
        record("missing_file", lambda: docs_skill.run(f"summarize {missing}", {}), "request full path")

        source = folder / "locked.txt"
        source.write_text("synthetic locked content " * 4, encoding="utf-8")
        old_text_of = docs_skill.text_of
        docs_skill.text_of = lambda *_a, **_k: (_ for _ in ()).throw(PermissionError("synthetic lock"))
        try:
            record("locked_file", lambda: docs_skill.run(f"summarize {source}", {}), "explain unreadable file")
        finally:
            docs_skill.text_of = old_text_of

        old_agent = computer_use.get_computer_use_agent
        computer_use.get_computer_use_agent = lambda: SimpleNamespace(
            search_pc=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("synthetic tool failure")))
        try:
            record("tool_failure", lambda: files_skill.run("find my synthetic report file", {}), "router fallback")
        finally:
            computer_use.get_computer_use_agent = old_agent

        old_get, old_place = utilities_skill.requests.get, utilities_skill.owner_place
        utilities_skill.owner_place = lambda: ("Synthetic City", "Synthetic Country")
        utilities_skill.requests.get = lambda *_a, **_k: (_ for _ in ()).throw(
            ConnectionError("synthetic network unavailable"))
        try:
            record("network_unavailable", lambda: utilities_skill.run("prayer times today", {}), "offline explanation")
        finally:
            utilities_skill.requests.get, utilities_skill.owner_place = old_get, old_place

        unknown = folder / "sample.unknown"
        unknown.write_text("synthetic", encoding="utf-8")
        record("unknown_format", lambda: document_reader.read_document(str(unknown)), "unsupported-format explanation")
    return rows


def exact_text(actual: Any, expected: Any) -> bool:
    return isinstance(actual, str) and isinstance(expected, str) and actual == expected


def exact_json(actual: Any, expected: Any) -> bool:
    try:
        left = json.loads(actual) if isinstance(actual, str) else actual
        right = json.loads(expected) if isinstance(expected, str) else expected
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return left == right


def numeric(actual: Any, expected: Any, tolerance: float = 0.0) -> bool:
    try:
        return math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=tolerance)
    except (TypeError, ValueError):
        return False


def run_correctness() -> dict[str, Any]:
    validator_cases: list[tuple[str, Callable[..., bool], tuple[Any, ...], bool]] = [
        ("text_equal", exact_text, ("Astana", "Astana"), True),
        ("text_case_is_exact", exact_text, ("astana", "Astana"), False),
        ("text_type_is_exact", exact_text, (42, "42"), False),
        ("json_key_order", exact_json, ('{"b":2,"a":1}', {"a": 1, "b": 2}), True),
        ("json_list_order", exact_json, ("[2,1]", [1, 2]), False),
        ("json_invalid", exact_json, ("{bad", {}), False),
        ("number_exact", numeric, (4, "4", 0.0), True),
        ("number_wrong", numeric, (4.1, 4, 0.0), False),
        ("number_tolerance", numeric, (4.001, 4, 0.01), True),
    ]
    validator_correct = sum(fn(*args) == expected for _name, fn, args, expected in validator_cases)
    from ishti.document_reader import read_document
    from ishti.skills.calc import skill as calc_skill
    from ishti.skills.docs import skill as docs_skill
    from ishti.skills.utilities import skill as utilities_skill

    actual: list[tuple[str, bool, str]] = []
    product_cases = [
        ("solve", lambda: calc_skill.run("solve for x: 2x + 3 = 11", {})["reply"], lambda x: x == "x = [4]"),
        ("percent", lambda: calc_skill.run("what is 15% of 2400", {})["reply"], lambda x: x == "15% of 2400 = 360"),
        ("gcd", lambda: calc_skill.run("gcd of 48 and 180", {})["reply"], lambda x: x == "GCD(48, 180) = 12"),
        ("temperature", lambda: utilities_skill.run("32 f to c", {})["reply"], lambda x: "0.0 °C" in x),
        ("distance", lambda: utilities_skill.run("1 km to m", {})["reply"], lambda x: "1,000 m" in x),
    ]
    for name, call, check in product_cases:
        try:
            reply = str(call())
            actual.append((name, bool(check(reply)), reply[:200]))
        except Exception as exc:
            actual.append((name, False, f"{type(exc).__name__}: {exc}"))
    calibration_ok = hallucination_ok = False
    with tempfile.TemporaryDirectory(prefix="ishti-eval-correct-") as raw:
        source = Path(raw) / "facts.txt"
        source.write_text("Synthetic checksum phrase CERULEAN-42", encoding="utf-8")
        try:
            body = read_document(str(source))
            actual.append(("provided_file_contents", body == "Synthetic checksum phrase CERULEAN-42", body[:200]))
        except Exception as exc:
            actual.append(("provided_file_contents", False, f"{type(exc).__name__}: {exc}"))
        missing = Path(raw) / "absent.txt"
        reply_obj = docs_skill.run(f"summarize {missing}", {})
        missing_reply = str(reply_obj.get("reply", "") if isinstance(reply_obj, dict) else reply_obj or "")
        calibration_ok = "full path" in missing_reply.lower() or "exist" in missing_reply.lower()
        hallucination_ok = not re.search(r"\b(?:summarized|saved|created|read successfully)\b", missing_reply, re.I)
    product_correct = sum(ok for _name, ok, _reply in actual)
    wrong = len(actual) - product_correct
    return {"suite": "correctness", "case_count": len(validator_cases) + len(actual) + 2,
            "metrics": {"validator_accuracy": _ratio(validator_correct, len(validator_cases)),
                        "product_exact_accuracy": _ratio(product_correct, len(actual)),
                        "wrong_answer_rate": _ratio(wrong, len(actual)),
                        "missing_input_hallucination_rate": _ratio(int(not hallucination_ok), 1),
                        "failure_calibration_rate": _ratio(int(calibration_ok), 1)},
            "failures": [{"case": name, "reply": reply} for name, ok, reply in actual if not ok],
            "metadata": {"product_cases": [{"case": n, "correct": ok, "reply": r} for n, ok, r in actual]}}


def run_files() -> dict[str, Any]:
    from ishti.document_reader import read_document
    from ishti.security.upload_scanner import is_safe_extension, validate_magic_bytes
    from ishti.skills.docs import skill as docs_skill
    from ishti.skills.fileops.skill import duplicates
    from ishti.skills.files.skill import match, query_of
    from ishti.skills.redact.skill import redact_file

    checks: list[tuple[str, bool]] = []
    extensions = ("py", "js", "ts", "html", "css", "json", "csv", "txt", "md", "ipynb",
                  "docx", "xlsx", "pdf", "pptx", "doc", "xls")
    operations = ("create", "read", "edit", "convert", "validate")
    matrix: dict[str, dict[str, dict[str, Any]]] = {
        ext: {op: {"status": "unmeasured", "reason": "No deterministic offline product path was exercised."}
              for op in operations} for ext in extensions
    }
    with tempfile.TemporaryDirectory(prefix="ishti-eval-files-") as raw:
        folder = Path(raw)
        (folder / "alpha.txt").write_text("same synthetic content", encoding="utf-8")
        (folder / "alpha-copy.txt").write_text("same synthetic content", encoding="utf-8")
        (folder / "different.txt").write_text("different", encoding="utf-8")
        groups = duplicates(str(folder), min_bytes=0, budget_s=5)
        flattened = [{Path(p).name for p in group} for group in groups]
        checks.extend([
            ("file_route", match("find my thesis proposal file")),
            ("not_generic_route", not match("what is a file system?")),
            ("query_extract", "proposal" in query_of("find my proposal file").lower()),
            ("duplicate_pair", {"alpha.txt", "alpha-copy.txt"} in flattened),
            ("different_excluded", all("different.txt" not in group for group in flattened)),
        ])
        phrase = "CERULEAN-42 synthetic document"
        samples: dict[str, Path] = {}
        setup_errors: dict[str, str] = {}
        text_fixtures = {
            "py": "VALUE = 'CERULEAN-42 synthetic document'\n",
            "js": "const value = 'CERULEAN-42 synthetic document';\n",
            "ts": "const value: string = 'CERULEAN-42 synthetic document';\n",
            "html": "<!doctype html><html><body><p>CERULEAN-42 synthetic document</p></body></html>\n",
            "css": "body { color: #123456; } /* CERULEAN-42 synthetic document */\n",
            "json": json.dumps({"value": phrase}) + "\n",
            "csv": f"name,value\nsynthetic,{phrase}\n",
            "txt": phrase,
            "md": f"# {phrase}\n",
        }
        for ext, body in text_fixtures.items():
            path = folder / f"sample.{ext}"
            path.write_text(body, encoding="utf-8")
            samples[ext] = path
        if importlib.util.find_spec("nbformat"):
            try:
                import nbformat
                path = folder / "sample.ipynb"
                notebook = nbformat.v4.new_notebook(cells=[nbformat.v4.new_markdown_cell(phrase)])
                nbformat.write(notebook, path)
                samples["ipynb"] = path
            except Exception as exc:
                setup_errors["ipynb"] = f"nbformat fixture failed: {type(exc).__name__}: {exc}"
        else:
            setup_errors["ipynb"] = "missing dependency: nbformat"
        try:
            from docx import Document
            path = folder / "sample.docx"
            document = Document()
            document.add_paragraph(phrase)
            document.save(path)
            samples["docx"] = path
        except Exception as exc:
            setup_errors["docx"] = f"{type(exc).__name__}: {exc}"
        try:
            from openpyxl import Workbook
            path = folder / "sample.xlsx"
            book = Workbook()
            book.active.append(["name", "value"])
            book.active.append(["synthetic", phrase])
            book.save(path)
            samples["xlsx"] = path
        except Exception as exc:
            setup_errors["xlsx"] = f"{type(exc).__name__}: {exc}"
        try:
            from pptx import Presentation
            path = folder / "sample.pptx"
            deck = Presentation()
            slide = deck.slides.add_slide(deck.slide_layouts[1])
            slide.shapes.title.text = phrase
            deck.save(path)
            samples["pptx"] = path
        except Exception as exc:
            setup_errors["pptx"] = f"{type(exc).__name__}: {exc}"
        try:
            from reportlab.pdfgen import canvas
            path = folder / "sample.pdf"
            page = canvas.Canvas(str(path))
            page.drawString(72, 720, phrase)
            page.save()
            samples["pdf"] = path
        except Exception as exc:
            setup_errors["pdf"] = f"{type(exc).__name__}: {exc}"

        for ext in extensions:
            matrix[ext]["create"] = {
                "status": "unmeasured",
                "reason": "Fixtures are eval setup, not evidence that the product create path works offline."
            }
            path = samples.get(ext)
            if path is None:
                reason = setup_errors.get(ext, f"No safe installed fixture writer for legacy .{ext}.")
                for op in ("read", "edit", "convert", "validate"):
                    matrix[ext][op] = {"status": "unmeasured", "reason": reason}
                if ext == "ipynb":
                    matrix[ext]["read"] = {"status": "unsupported",
                                             "reason": "ishti.document_reader does not support .ipynb."}
                continue
            if ext in {"json", "ipynb"}:
                matrix[ext]["read"] = {"status": "unsupported",
                                        "reason": f"ishti.document_reader does not support .{ext}."}
            else:
                try:
                    body = read_document(str(path), timeout=15)
                    ok = phrase in body
                    matrix[ext]["read"] = {"status": "measured", "passed": ok}
                except Exception as exc:
                    matrix[ext]["read"] = {"status": "measured", "passed": False,
                                            "reason": f"{type(exc).__name__}: {exc}"}
            try:
                header = path.read_bytes()[:512]
                safe, reason = validate_magic_bytes(header, path.name)
                accepted = bool(safe and is_safe_extension(path.name))
                syntax = _syntax_validation(ext, path)
                syntax_passed = syntax.get("passed") if syntax["status"] == "measured" else None
                matrix[ext]["validate"] = {
                    "status": "measured" if syntax["status"] == "measured" else syntax["status"],
                    "passed": bool(accepted and syntax_passed) if syntax_passed is not None else None,
                    "upload_accepted": accepted, "upload_evidence": reason, "syntax": syntax,
                }
            except Exception as exc:
                matrix[ext]["validate"] = {"status": "measured", "passed": False,
                                            "reason": f"{type(exc).__name__}: {exc}"}

        for ext in ("txt", "md"):
            try:
                result = redact_file(str(samples[ext]), session={"_redact_out": str(folder / "redacted")})
                matrix[ext]["edit"] = {"status": "measured", "passed": "Redacted copy written" in result}
            except Exception as exc:
                matrix[ext]["edit"] = {"status": "measured", "passed": False,
                                        "reason": f"{type(exc).__name__}: {exc}"}
        for ext in ("csv", "xlsx"):
            try:
                result = docs_skill.op_excel_clean(None, f"clean {samples[ext]}", {})
                output_match = re.search(r"Saved:\s*(.+)$", result)
                output = Path(output_match.group(1)) if output_match else None
                ok = bool(output and output.is_file())
                matrix[ext]["edit"] = {"status": "measured", "passed": ok}
                if ext == "csv":
                    matrix[ext]["convert"] = {"status": "measured", "passed": ok,
                                               "evidence": ".csv -> .xlsx clean-copy path"}
            except Exception as exc:
                matrix[ext]["edit"] = {"status": "measured", "passed": False,
                                        "reason": f"{type(exc).__name__}: {exc}"}
                matrix[ext]["convert"] = {"status": "measured", "passed": False,
                                           "reason": f"{type(exc).__name__}: {exc}"}
        if "pdf" in samples:
            try:
                result = docs_skill.op_pdf_split(None, f"split {samples['pdf']}", {})
                matrix["pdf"]["edit"] = {"status": "measured", "passed": "Split into 1" in result}
            except Exception as exc:
                matrix["pdf"]["edit"] = {"status": "unmeasured",
                                           "reason": f"PDF edit dependency unavailable: {type(exc).__name__}: {exc}"}

        for ext in extensions:
            if matrix[ext]["edit"]["status"] == "unmeasured" and ext not in {"doc", "xls"}:
                matrix[ext]["edit"]["reason"] = "No deterministic edit path for this format is exposed without LLM/COM."
            if matrix[ext]["convert"]["status"] == "unmeasured" and ext not in {"doc", "xls"}:
                matrix[ext]["convert"]["reason"] = "Conversion requires COM, an LLM, or has no product path; not exercised."
        overwrite_regressions = []
        safe_enabled = docs_skill._safe_outputs_enabled()
        old_home, old_ask = docs_skill.HOME, docs_skill.llm.ask
        docs_skill.HOME = str(folder)
        try:
            for ext, operation in (("docx", "create"), ("pptx", "create")):
                title = "same synthetic document" if ext == "docx" else "Same synthetic deck"
                base = folder / f"{title} {dt.date.today()}.{ext}"
                base.write_bytes(b"owner sentinel")
                try:
                    if ext == "docx":
                        docs_skill.llm.ask = lambda *_a, **_k: "# same synthetic document\nCERULEAN-42"
                        reply = docs_skill.op_make_doc(None, "create a document about same synthetic document", {})
                        made = Path(reply.split("Word document: ", 1)[1].splitlines()[0])
                        from docx import Document
                        reopened = "CERULEAN-42" in "\n".join(p.text for p in Document(made).paragraphs)
                    else:
                        docs_skill.llm.ask = lambda *_a, **_k: json.dumps({
                            "title": title, "subtitle": "CERULEAN-42",
                            "slides": [{"title": "proof", "bullets": ["safe"]}],
                        })
                        reply = docs_skill.op_make_deck(None, "create a presentation about safe output", {})
                        made = Path(reply.split(": ", 1)[1].splitlines()[0])
                        from pptx import Presentation
                        reopened = Presentation(made).slides[0].shapes.title.text == title
                    risk = base.read_bytes() != b"owner sentinel"
                    row = {"extension": ext, "operation": operation, "same_target": made == base,
                           "overwrite_risk": risk, "flag_enabled": safe_enabled,
                           "output_reopened": reopened,
                           "evidence": "product writer executed and output was reopened"}
                    overwrite_regressions.append(row)
                    matrix[ext]["create"] = {"status": "measured", "passed": not risk and reopened,
                                               "product_defect": risk, "evidence": row["evidence"]}
                except Exception as exc:
                    unavailable = isinstance(exc, (ImportError, ModuleNotFoundError))
                    reason = f"Product writer {'unavailable' if unavailable else 'failed'}: {type(exc).__name__}: {exc}"
                    overwrite_regressions.append({
                        "extension": ext, "operation": operation, "overwrite_risk": None,
                        "flag_enabled": safe_enabled, "output_reopened": False, "evidence": reason,
                    })
                    matrix[ext]["create"] = {
                        "status": "unmeasured" if unavailable else "measured",
                        "passed": False, "reason": reason,
                    }
        finally:
            docs_skill.HOME, docs_skill.llm.ask = old_home, old_ask

        if "pdf" in samples:
            collision_pdf = folder / "collision.pdf"
            collision_pdf.write_bytes(samples["pdf"].read_bytes())
            base_dir = folder / "collision_pages"
            base_dir.mkdir()
            sentinel = base_dir / "page_001.pdf"
            sentinel.write_bytes(b"owner sentinel")
            try:
                reply = docs_skill.op_pdf_split(None, f"split {collision_pdf}", {})
                made_dir = Path(reply.rsplit(" in ", 1)[1])
                made = made_dir / "page_001.pdf"
                import pikepdf
                with pikepdf.open(made) as reopened_pdf:
                    reopened = len(reopened_pdf.pages) == 1
                risk = sentinel.read_bytes() != b"owner sentinel"
                overwrite_regressions.append({
                    "extension": "pdf", "operation": "split", "same_target": made_dir == base_dir,
                    "overwrite_risk": risk, "flag_enabled": safe_enabled, "output_reopened": reopened,
                    "evidence": "product split executed and output was reopened",
                })
            except Exception as exc:
                unavailable = isinstance(exc, (ImportError, ModuleNotFoundError))
                reason = f"Product splitter {'unavailable' if unavailable else 'failed'}: {type(exc).__name__}: {exc}"
                overwrite_regressions.append({
                    "extension": "pdf", "operation": "split", "overwrite_risk": None,
                    "flag_enabled": safe_enabled, "output_reopened": False, "evidence": reason,
                })
                matrix["pdf"]["edit"] = {
                    "status": "unmeasured" if unavailable else "measured",
                    "passed": False, "reason": reason,
                }
    correct = sum(ok for _name, ok in checks)
    measured_cells = [cell for row in matrix.values() for cell in row.values() if cell["status"] == "measured"]
    passed_cells = sum(bool(cell.get("passed")) for cell in measured_cells)
    measured_overwrites = [row for row in overwrite_regressions
                           if isinstance(row.get("overwrite_risk"), bool)]
    return {"suite": "files", "case_count": len(checks) + len(measured_cells),
            "metrics": {"synthetic_file_checks": _ratio(correct, len(checks)),
                        "measured_operation_pass_rate": _ratio(passed_cells, len(measured_cells)),
                        "overwrite_defect_rate": _ratio(
                            sum(row["overwrite_risk"] for row in measured_overwrites), len(measured_overwrites))},
            "failures": ([name for name, ok in checks if not ok]
                         + [{"extension": ext, "operation": op, **cell}
                            for ext, row in matrix.items() for op, cell in row.items()
                            if cell["status"] == "measured" and not cell.get("passed")]),
            "metadata": {"storage": "TemporaryDirectory only", "real_user_paths_read": False,
                         "operation_matrix": matrix, "overwrite_regressions": overwrite_regressions,
                         "ipynb_support": {
                             "fixture_dependency": "nbformat" if importlib.util.find_spec("nbformat") else "missing: nbformat",
                             "execution_dependency": "nbclient installed" if importlib.util.find_spec("nbclient") else "missing: nbclient",
                             "product_create": "unmeasured", "product_edit": "unmeasured",
                             "product_execute": "unmeasured (no product execution path exercised)"}}}


def _syntax_validation(ext: str, path: Path) -> dict[str, Any]:
    """Validate synthetic fixtures with deterministic installed tooling, not an LLM."""
    try:
        if ext == "py":
            ast.parse(path.read_text(encoding="utf-8"))
        elif ext == "json":
            json.loads(path.read_text(encoding="utf-8"))
        elif ext == "ipynb":
            if not importlib.util.find_spec("nbformat"):
                return {"status": "unmeasured", "reason": "missing dependency: nbformat"}
            import nbformat
            nbformat.validate(nbformat.read(path, as_version=4))
        elif ext == "html":
            from html.parser import HTMLParser
            class ShapeParser(HTMLParser):
                void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                        "param", "source", "track", "wbr"}

                def __init__(self):
                    super().__init__()
                    self.stack: list[str] = []

                def handle_starttag(self, tag, _attrs):
                    if tag not in self.void:
                        self.stack.append(tag)

                def handle_endtag(self, tag):
                    if not self.stack or self.stack.pop() != tag:
                        raise ValueError(f"mismatched closing tag: {tag}")

            parser = ShapeParser()
            parser.feed(path.read_text(encoding="utf-8"))
            parser.close()
            if parser.stack:
                raise ValueError(f"unclosed tag: {parser.stack[-1]}")
        elif ext == "css":
            css = path.read_text(encoding="utf-8")
            if importlib.util.find_spec("tinycss2"):
                import tinycss2
                tokens = tinycss2.parse_stylesheet(css)
                if any(token.type == "error" for token in tokens):
                    raise ValueError("tinycss2 reported a parse error")
            elif css.count("{") != css.count("}") or not re.search(r"[^{}]+\{[^{}]*:[^{}]*\}", css):
                raise ValueError("stdlib fallback found invalid CSS block shape")
        elif ext == "js":
            node = shutil.which("node")
            if not node:
                return {"status": "unmeasured", "reason": "missing dependency: node"}
            probe = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=15)
            if probe.returncode:
                raise ValueError(probe.stderr.strip()[:200])
        elif ext == "ts":
            tsc = ROOT / "interface" / "app" / "node_modules" / ".bin" / "tsc.cmd"
            if not tsc.is_file():
                return {"status": "unmeasured", "reason": f"missing dependency: {tsc}"}
            probe = subprocess.run([str(tsc), "--noEmit", "--skipLibCheck", str(path)], cwd=path.parent,
                                   capture_output=True, text=True, timeout=30)
            if probe.returncode:
                raise ValueError((probe.stderr or probe.stdout).strip()[:200])
        elif ext in {"txt", "md", "csv"}:
            path.read_text(encoding="utf-8")
        else:
            return {"status": "measured", "passed": True, "tool": "magic bytes + product document reader"}
        return {"status": "measured", "passed": True, "tool": "deterministic installed parser"}
    except Exception as exc:
        return {"status": "measured", "passed": False, "reason": f"{type(exc).__name__}: {exc}"}


def _history(count: int, fact_index: int) -> list[dict[str, str]]:
    rows = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"synthetic ordinary turn {i}"}
            for i in range(count)]
    rows[fact_index]["content"] = "Synthetic fact: project codename is CERULEAN-42."
    return rows


def run_memory() -> dict[str, Any]:
    from ishti.core.context import ContextAssembler
    from ishti.core.state import ConversationState, ConversationStateManager, MemoryStore, SQLiteStore

    results = []
    for count in (1_000, 10_000):
        history = _history(count, 2)
        started = time.perf_counter()
        context = ContextAssembler(max_total_chars=16_000).build(
            history, ConversationState(f"synthetic-{count}"), query="What is the project codename?")
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        joined = "\n".join(str(item.get("content", "")) for item in context)
        results.append({"history_size": count, "fact_recalled": "CERULEAN-42" in joined,
                        "context_chars": sum(len(str(item.get("content", ""))) for item in context),
                        "elapsed_ms": elapsed_ms})
    checks: list[dict[str, Any]] = []
    store = MemoryStore()
    manager = ConversationStateManager(store)
    latest = manager.get("latest")
    manager.observe_user_turn(latest, "research Sony WH-1000XM5")
    manager.observe_user_turn(latest, "research Samsung S25")
    manager.save(latest)
    checks.append({"case": "latest_wins", "passed": "Samsung S25" in manager.get("latest").active_topic})

    a = manager.get("session-a")
    a.active_topic = "ALPHA-ONLY"
    manager.save(a)
    b = manager.get("session-b")
    b.active_topic = "BETA-ONLY"
    manager.save(b)
    checks.append({"case": "session_isolation", "passed": manager.get("session-a").active_topic == "ALPHA-ONLY"
                   and manager.get("session-b").active_topic == "BETA-ONLY"})

    unmeasured = [
        {"case": "arch2_profile_namespace_isolation", "status": "unmeasured",
         "reason": "Arch2 retrieval has no profile namespace argument; session-id separation is not profile isolation."},
        {"case": "arch2_client_namespace_isolation", "status": "unmeasured",
         "reason": "Arch2 retrieval has no client namespace argument; CRM isolation cannot be inferred here."},
        {"case": "arch2_namespaced_retrieval", "status": "unmeasured",
         "reason": "The scratch-safe retrieval API accepts only query text, so cross-namespace retrieval is not testable."},
    ]

    with tempfile.TemporaryDirectory(prefix="ishti-eval-memory-store-") as raw:
        db_path = str(Path(raw) / "state.db")
        first = ConversationStateManager(SQLiteStore(db_path))
        persisted = first.get("restart-case")
        persisted.active_topic = "PERSISTED-42"
        first.save(persisted)
        code = ("from ishti.core.state import ConversationStateManager,SQLiteStore; "
                f"print(ConversationStateManager(SQLiteStore({db_path!r})).get('restart-case').active_topic)")
        probe = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=20)
        checks.append({"case": "process_restart_persistence",
                       "passed": probe.returncode == 0 and probe.stdout.strip() == "PERSISTED-42"})

    history_passed = sum(row["fact_recalled"] and row["context_chars"] <= 16_000 for row in results)
    behavior_passed = sum(row["passed"] for row in checks)
    return {"suite": "memory", "case_count": len(results) + len(checks),
            "metrics": {"synthetic_recall_and_budget": _ratio(history_passed, len(results)),
                        "memory_behavior_accuracy": _ratio(behavior_passed, len(checks)),
                        "arch2_profile_client_isolation": metric(
                            measured=False, reason="Arch2 exposes no profile/client namespace for a valid measurement.")},
            "failures": ([row for row in results if not row["fact_recalled"] or row["context_chars"] > 16_000]
                         + [row for row in checks if not row["passed"]]),
            "metadata": {"histories": results, "real_persistent_store_used": False,
                         "scratch_sqlite_used_for_restart_only": True,
                         "behavior_checks": checks, "unmeasured_checks": unmeasured,
                         "isolation_note": "Only session isolation is measured; profile/client/Arch2 isolation remains unmeasured.",
                         "content": "generated synthetic metadata only"}}


SUITES: dict[str, Callable[[], dict[str, Any]]] = {
    "understanding": run_understanding,
    "dead_end": run_dead_end,
    "correctness": run_correctness,
    "files": run_files,
    "memory": run_memory,
}


def capture_ollama_ps(timeout: float = 0.5) -> dict[str, Any]:
    """Optional loopback-only, read-only residency observation."""
    request = urllib.request.Request("http://127.0.0.1:11434/api/ps", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(1_000_000))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return {"status": "unmeasured", "reason": f"Ollama loopback unavailable: {type(exc).__name__}"}
    models = []
    for row in payload.get("models", []):
        size, size_vram = row.get("size"), row.get("size_vram")
        measurable = isinstance(size, int) and isinstance(size_vram, int)
        models.append({"name": row.get("name") or row.get("model"), "size": size, "size_vram": size_vram,
                       "invariant_status": ("pass" if size == size_vram else "fail") if measurable else "unmeasured"})
    measured = [row for row in models if row["invariant_status"] != "unmeasured"]
    return {"status": "measured" if measured else "unmeasured", "models": models,
            "all_fully_resident": all(row["invariant_status"] == "pass" for row in measured) if measured else None,
            "reason": None if measured else "No model exposed measurable size and size_vram fields."}


def _aggregate_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate: dict[str, Any] = {
        "median_latency_ms": statistics.median(run["duration_ms"] for run in runs), "metrics": {}
    }
    suite_names = [suite["suite"] for suite in runs[0]["results"]]
    for suite_name in suite_names:
        suite_metrics: dict[str, Any] = {}
        metric_names = runs[0]["results"][suite_names.index(suite_name)]["metrics"].keys()
        for metric_name in metric_names:
            values = []
            for run in runs:
                suite = next(item for item in run["results"] if item["suite"] == suite_name)
                item = suite["metrics"][metric_name]
                if item["status"] == "measured" and item.get("value") is not None:
                    values.append(float(item["value"]))
            suite_metrics[metric_name] = ({"status": "measured", "median_value": statistics.median(values),
                                           "measured_runs": len(values), "total_runs": len(runs)}
                                          if values else {"status": "unmeasured", "measured_runs": 0,
                                                          "total_runs": len(runs),
                                                          "reason": "Metric was unmeasured in every run."})
        aggregate["metrics"][suite_name] = suite_metrics
    return aggregate


def run_suites(name: str, *, repeat: int = 1, ollama_ps: bool = False) -> dict[str, Any]:
    selected = list(SUITES) if name == "all" else [name]
    if any(item not in SUITES for item in selected):
        raise ValueError(f"unknown suite {name!r}; choose all or {', '.join(SUITES)}")
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    all_started = time.perf_counter()
    runs = []
    for index in range(1, repeat + 1):
        started = time.perf_counter()
        reports = [SUITES[item]() for item in selected]
        runs.append({"run": index, "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                     "results": reports,
                     "ollama_ps": capture_ollama_ps() if ollama_ps else {
                         "status": "unmeasured", "reason": "Capture not requested; pass --ollama-ps to enable."}})
    return {"schema_version": 1, "offline": True,
            "environment": {"ISHTI_TEST_MODE": os.environ.get("ISHTI_TEST_MODE"),
                            "ISHTI_SCRATCH": os.environ.get("ISHTI_SCRATCH")},
            "suite": name, "repeat": repeat,
            "duration_ms": round((time.perf_counter() - all_started) * 1000, 3),
            "runs": runs, "aggregate": _aggregate_runs(runs),
            "results": runs[-1]["results"]}


def concise_table(report: dict[str, Any]) -> str:
    lines = ["suite          metric                         result", "-------------  -----------------------------  ----------------"]
    for suite_name, metrics in report["aggregate"]["metrics"].items():
        for name, value in metrics.items():
            if value["status"] == "unmeasured":
                shown = "UNMEASURED"
            else:
                shown = f"median {value['median_value']:.4f} ({value['measured_runs']} run(s))"
            lines.append(f"{suite_name:<13}  {name:<29}  {shown}")
    lines.append(f"median latency: {report['aggregate']['median_latency_ms']:.3f} ms")
    return "\n".join(lines) + "\n"
