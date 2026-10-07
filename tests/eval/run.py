from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# These are invariants, not caller preferences. Set them before product imports.
os.environ["ISHTI_TEST_MODE"] = "1"
os.environ["ISHTI_SCRATCH"] = "1"

from tests.eval.harness import SUITES, concise_table, run_suites  # noqa: E402


def safe_output_dir(raw: str | None) -> Path:
    path = (Path(raw) if raw else ROOT / "docs" / "audit").resolve()
    root = ROOT.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("output must stay inside the repository") from exc
    if path.exists() and not path.is_dir():
        raise ValueError("output must be a directory")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run ISHTI's offline evaluation suites.")
    parser.add_argument("suite", nargs="?", default="all", choices=["all", *SUITES])
    parser.add_argument("--output", help="safe output directory inside this repository")
    parser.add_argument("--repeat", type=int, default=1, help="number of independent runs (default: 1)")
    parser.add_argument("--ollama-ps", action="store_true",
                        help="capture read-only Ollama /api/ps residency metadata per run")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    output = safe_output_dir(args.output)
    output.mkdir(parents=True, exist_ok=True)
    # Some imported SQLite helpers retain a process-lifetime connection on
    # Windows. The scratch directory is still isolated; tolerate only cleanup
    # errors so an open handle cannot turn a completed evaluation into failure.
    with tempfile.TemporaryDirectory(prefix="ishti-eval-scratch-", ignore_cleanup_errors=True) as scratch:
        os.environ["ISHTI_SCRATCH_DIR"] = scratch
        report = run_suites(args.suite, repeat=args.repeat, ollama_ps=args.ollama_ps)
    stem = f"eval_{args.suite}"
    json_path = output / f"{stem}.json"
    table_path = output / f"{stem}.txt"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    table = concise_table(report)
    table_path.write_text(table, encoding="utf-8")
    print(table, end="")
    print(f"json: {json_path}")
    print(f"table: {table_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
