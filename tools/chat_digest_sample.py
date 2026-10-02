r"""Build the chat-digest samples from the SYNTHETIC dataset (no real chats, no real people).

    venv\Scripts\python tools\chat_digest_sample.py

Writes into reports/:
    <stamp>_chat_digest.md / .docx / .pdf        the three exports
    <stamp>_chat_digest.html                    the page the PDF is printed from
    <stamp>_chat_digest_p1.png ...              every PDF page rasterised (pypdfium2)
    <stamp>_chat_digest_wide.png / _narrow.png  the same tables at a wide and a narrow window
plus reports/chat_digest_sample_ground_truth.json (what the fake world contained).
"""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_chat_digest import NOW, synthetic                      # noqa: E402
from ishti.skills.chat_digest import analyze, export, report, store   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "reports")


def screenshot(html_path, png_path, width=1400, height=1800):
    """Render the HTML page with the installed Edge/Chrome (no GUI, no network)."""
    chrome = export._find_chrome()
    if not chrome:
        return ""
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-first-run", f"--hide-scrollbars",
                    f"--window-size={width},{height}", f"--screenshot={png_path}",
                    f"file:///{os.path.abspath(html_path).replace(os.sep, '/')}"],
                   capture_output=True, text=True, timeout=180,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return png_path if os.path.isfile(png_path) else ""


def main():
    stamp = time.strftime("%Y-%m-%d_%H%M")
    os.makedirs(SAMPLES, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="cd_sample_")      # the fake store/graph never land in reports/
    store.DB_PATH = os.path.join(scratch, "sample_store.db")
    store.close()
    from ishti import rgraph
    rgraph.DB_PATH = os.path.join(scratch, "sample_rgraph.db")   # fake graph, never the real one
    rgraph._conn = None
    rgraph.relate("me", "brother", "Bilal Khan", source_type="explicit_user", confidence=1.0)
    payload, truth = synthetic(NOW)
    store.ingest_json(payload, source="sample")
    from ishti.skills.chat_digest import window as win_mod
    result = analyze.analyze(win_mod.window(now=NOW), use_llm=False)
    md = report.render(result)
    made = {"md": export.save_md(md, path=os.path.join(SAMPLES, f"{stamp}_chat_digest.md")),
            "docx": export.save_docx(md, path=os.path.join(SAMPLES, f"{stamp}_chat_digest.docx"))}
    try:
        pdf, method = export.save_pdf(md, path=os.path.join(SAMPLES, f"{stamp}_chat_digest.pdf"))
        made["pdf"], made["pdf_method"] = pdf, method
        made["pages"] = export.rasterize(pdf, out_dir=SAMPLES)["png"]
    except Exception as exc:
        made["pdf_error"] = f"{type(exc).__name__}: {exc}"
    html = os.path.join(SAMPLES, f"{stamp}_chat_digest.html")
    with open(html, "w", encoding="utf-8") as f:
        f.write(export.to_html(md))
    made["html"] = html
    made["wide"] = screenshot(html, os.path.join(SAMPLES, f"{stamp}_chat_digest_wide.png"))
    made["narrow"] = screenshot(html, os.path.join(SAMPLES, f"{stamp}_chat_digest_narrow.png"), width=680, height=2000)
    truth_path = os.path.join(SAMPLES, "chat_digest_sample_ground_truth.json")
    with open(truth_path, "w", encoding="utf-8") as f:
        # file paths are stored relative to the checkout so the record is valid on any machine
        rel = lambda v: (os.path.relpath(v, ROOT).replace(os.sep, "/")  # noqa: E731
                         if isinstance(v, str) and os.path.isabs(v) else [rel(x) for x in v] if isinstance(v, list) else v)
        json.dump({"ground_truth": truth, "totals": result["totals"], "files": {k: rel(v) for k, v in made.items()}},
                  f, indent=2, default=str)
    made["ground_truth"] = truth_path
    print(json.dumps(made, indent=2, default=str))
    print(f"\n[chat_digest_sample] {result['totals']}")
    print(f"[chat_digest_sample] markdown tables: {len(report.table_problems(md))} problem(s)")


if __name__ == "__main__":
    main()
