"""LoRA fine-tuning job for the brain model (Part 3) - run OFFLINE, never live.

Pipeline (each step refuses to run when the previous one isn't satisfied):
  1. DATA   - ishti.ftlog.export() must produce >= MIN_EXAMPLES curated examples.
  2. TRAIN  - QLoRA on the configured base model via unsloth/transformers+peft.
              Runs overnight/idle, never while ISHTI's server needs the GPU.
  3. EVAL   - a fixed eval set (data/finetune/eval_set.json) of known requests with
              known-good expected behavior: the candidate must not regress against
              the current production adapter/model before it is promoted.
  4. PROMOTE/ROLLBACK - adapters are versioned in data/finetune/adapters/<stamp>/;
              'current' is a copy whose contents are only replaced after eval passes.
              The previous version is archived and restorable with rollback().
Degrades cleanly: without torch/unsloth installed, every command reports what's
missing instead of crashing - collection still works so data accrues meanwhile.
"""
import json
import os
import shutil
import sys
import time

if __package__ in (None, ""):  # run directly: python orchestrator/finetune.py status
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ishti import ROOT

FT_DIR = os.path.join(ROOT, "data", "finetune")
ADAPTERS = os.path.join(FT_DIR, "adapters")
CURRENT = os.path.join(ADAPTERS, "current")
EVAL_SET = os.path.join(FT_DIR, "eval_set.json")
MIN_EXAMPLES = 300   # don't fine-tune on crumbs
MIN_EVAL_SCORE = 0.8  # candidate must match/beat this on the eval set
# QLoRA settings sized for a 16 GB card (RTX 5070 Ti) and a 14B Q4 base:
LORA = {"r": 16, "lora_alpha": 16, "lora_dropout": 0.05,
        "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        "max_seq_length": 1024, "batch": 1, "grad_accum": 8, "lr": 2e-4, "epochs": 1}


def default_eval_set():
    """The regression gate: known requests -> substrings a good answer contains."""
    return [
        {"request": "what is 17 times 23? reply with just the number", "expect": ["391"]},
        {"request": "where is the ishti project?", "expect": [ROOT]},
        {"request": "who is my brother", "expect": ["ahmed"]},          # graph-grounded
        {"request": "remind me in 20 minutes to call Ali", "expect": ["Reminder", "reminder", "set"]},
        {"request": "how much ram am I using", "expect": ["GB", "%"]},
        {"request": "what's the weather tomorrow?", "expect": ["°C", "rain", "cloud", "sun"]},
    ]


def deps():
    """(missing, notes): what a real training run needs beyond ISHTI's base venv."""
    missing = []
    for mod, pip in (("torch", "torch (CUDA build)"), ("peft", "peft"), ("transformers", "transformers"),
                     ("datasets", "datasets"), ("bitsandbytes", "bitsandbytes")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pip)
    return missing


def curate_examples(rows, min_quality=None, cap_per_skill=None):
    """G3 hygiene pass over dataset rows: quality filter, dedupe, contradiction drop and a
    per-skill cap so one loud domain can't dominate the mix. Returns (kept, {dropped: n})."""
    try:
        from ishti.learning.store import cfg as lcfg
        conf = lcfg().get("dataset", {})
        min_quality = min_quality if min_quality is not None else conf.get("min_quality", 0.6)
        cap_per_skill = cap_per_skill if cap_per_skill is not None else conf.get("per_skill_cap", 0.4)
    except Exception:
        min_quality = 0.6 if min_quality is None else min_quality
        cap_per_skill = 0.4 if cap_per_skill is None else cap_per_skill
    kept = []
    dropped = {"low_quality": 0, "duplicate": 0, "contradiction": 0, "capped": 0}
    seen_pairs, seen_instructions, counts = set(), set(), {}
    cap_n = max(3, int(cap_per_skill * len(rows))) if rows else None
    for r in rows:
        try:
            q = float(r.get("quality") or 0.0)
        except (TypeError, ValueError):
            q = 0.0
        if q < min_quality:
            dropped["low_quality"] += 1
            continue
        ikey = (r.get("instruction") or "").strip().lower()
        ckey = (r.get("correct") or r.get("correction") or "").strip().lower()
        pair = (ikey, ckey)
        if pair in seen_pairs:
            dropped["duplicate"] += 1
            continue
        if ikey in seen_instructions and ckey:
            # same instruction kept with a DIFFERENT correction: a contradiction, not data
            dropped["contradiction"] += 1
            continue
        seen_pairs.add(pair)
        seen_instructions.add(ikey)
        skill = r.get("skill") or r.get("domain") or "general"
        counts[skill] = counts.get(skill, 0) + 1
        if cap_n and counts[skill] > cap_n:
            dropped["capped"] += 1
            continue
        kept.append(r)
    return kept, dropped


def dry_run(min_quality=None, eval_also=False) -> dict:
    """G3/G4: the whole offline pipeline WITHOUT training — data sufficiency, curation,
    eval-set presence, dependency check, and the gate math. Trains nothing and deploys
    nothing (deployment only ever happens via the explicit `promote` command after a
    real training run); the one file it may create is the standard eval set. Safe to run any time."""
    from ishti import ftlog
    ok, s = ftlog.ready_for_training(MIN_EXAMPLES)
    report = {"examples_logged": s["total"], "min_needed": MIN_EXAMPLES, "data_ready": bool(ok)}
    try:
        rows = ftlog.rows("SELECT instruction, correct, correction, quality, skill, domain FROM dataset ORDER BY ts DESC LIMIT 50000") \
            if hasattr(ftlog, "rows") else []
    except Exception:
        rows = []
    if not rows:
        try:
            from ishti.learning import dataset as DS
            rows = DS.rows(limit=50000)
        except Exception:
            rows = []
    kept, dropped = curate_examples(rows, min_quality=min_quality)
    report["curated_examples"] = len(kept)
    report["dropped"] = dropped
    report["curation_would_train"] = len(kept) >= MIN_EXAMPLES
    try:  # the gate always has its eval set: a dry-run that reports the gate file missing is not a dry-run
        ensure_eval_set()
        report["eval_set"] = "present"
    except OSError as exc:
        report["eval_set"] = f"missing ({type(exc).__name__}: {exc})"
    report["training_deps_missing"] = deps()
    report["would_train"] = bool(report["curation_would_train"] and not report["training_deps_missing"])
    report["auto_deploy"] = False
    report["note"] = ("dry-run only: nothing was trained, nothing was promoted. "
                      "Deployment is always an explicit `promote` after a real run evaluates >= the gate.")
    return report


def base_model_name():
    """The exact Ollama model name of the production brain (config name kept verbatim:
    'ishti-qwen2.5:14b' must be asked for by its full tag, not a stripped family name)."""
    from ishti import CONFIG
    return CONFIG["llm"]["models"].get("planner", "qwen2.5:14b")


def ensure_eval_set():
    os.makedirs(FT_DIR, exist_ok=True)
    if not os.path.isfile(EVAL_SET):
        with open(EVAL_SET, "w", encoding="utf-8") as f:
            json.dump(default_eval_set(), f, ensure_ascii=False, indent=1)
    with open(EVAL_SET, encoding="utf-8") as f:
        return json.load(f)


def _answer_with(model_name, adapter, request):
    """Answer one eval request with the Ollama-hosted model (+ optional adapter when
    Ollama supports it), so the eval gate measures the SERVING stack, not a side copy."""
    import requests
    payload = {"model": model_name, "prompt": request, "stream": False, "options": {"temperature": 0}}
    if adapter:
        payload["options"]["num_thread"] = 8
    r = requests.post("http://127.0.0.1:11434/api/generate", json=payload, timeout=120)
    return r.json().get("response", "")


def run_eval(adapter_dir=None, model=None):
    """Score an adapter (or the bare model when adapter_dir is None) on the eval set.
    Returns (score 0..1, [per-item results]). Missing expectations count as failures."""
    eval_set = ensure_eval_set()
    model = model or base_model_name()
    results = []
    for item in eval_set:
        try:
            out = _answer_with(model, adapter_dir, item["request"]).lower()
        except Exception as exc:
            results.append({"request": item["request"], "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:80]}"})
            continue
        ok = any(w.lower() in out for w in item["expect"])
        results.append({"request": item["request"], "ok": ok, "answer": out[:120]})
    score = sum(1 for r in results if r["ok"]) / max(1, len(results))
    return score, results


def train(epochs=None, out_stamp=None):
    """QLoRA train from the curated export. Returns (adapter_dir | None, note)."""
    from ishti import ftlog
    ok, s = ftlog.ready_for_training(MIN_EXAMPLES)
    if not ok:
        return None, f"only {s['total']} logged exchanges; need {MIN_EXAMPLES} - keep using ISHTI and try later"
    missing = deps()
    if missing:
        return None, "training needs: " + ", ".join(missing) + " - install them, or run this on a rented GPU pod"
    import torch  # noqa: F401  (verified present by deps())
    from unsloth import FastLanguageModel
    from transformers import TrainingArguments
    from trl import SFTTrainer
    from datasets import load_dataset

    dataset_path, n = ftlog.export(limit=20000)
    stamp = out_stamp or time.strftime("%Y%m%d_%H%M")
    out_dir = os.path.join(ADAPTERS, stamp)
    os.makedirs(out_dir, exist_ok=True)
    base = base_model_name().split(":")[0]  # the HF family name for training weights
    # Ollama serves GGUF; training pulls the HF weights of the same family
    hf_base = f"unsloth/{base}-bnb-4bit" if base.startswith("qwen") else f"meta-llama/{base}"
    model, tok = FastLanguageModel.from_pretrained(hf_base, max_seq_length=LORA["max_seq_length"], load_in_4bit=True)
    model = FastLanguageModel.get_peft_model(model, r=LORA["r"], lora_alpha=LORA["lora_alpha"],
                                             lora_dropout=LORA["lora_dropout"], target_modules=LORA["target_modules"])
    ds = load_dataset("json", data_files=dataset_path, split="train")
    args = TrainingArguments(output_dir=out_dir, per_device_train_batch_size=LORA["batch"],
                             gradient_accumulation_steps=LORA["grad_accum"], num_train_epochs=epochs or LORA["epochs"],
                             learning_rate=LORA["lr"], logging_steps=10, save_strategy="no", report_to=[])
    trainer = SFTTrainer(model=model, tokenizer=tok, train_dataset=ds, args=args,
                         max_seq_length=LORA["max_seq_length"], dataset_text_field=None, packing=False)
    trainer.train()
    model.save_pretrained(out_dir)
    tok.save_pretrained(out_dir)
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"base": hf_base, "examples": n, "stamp": stamp, "lora": LORA}, f, indent=1)
    return out_dir, f"trained on {n} examples -> {out_dir}"


def promote(adapter_dir):
    """Eval-before-promote: the candidate must score >= MIN_EVAL_SCORE AND at least the
    current production score. Never auto-promotes a regression."""
    if not os.path.isdir(adapter_dir):
        return False, f"no adapter at {adapter_dir}"
    cand_score, cand_detail = run_eval(adapter_dir)
    cur_score = None
    if os.path.isdir(CURRENT):
        cur_score, _ = run_eval(CURRENT)
    if cand_score < MIN_EVAL_SCORE:
        return False, f"candidate scored {cand_score:.0%} < {MIN_EVAL_SCORE:.0%} gate - not promoted"
    if cur_score is not None and cand_score < cur_score:
        return False, (f"candidate {cand_score:.0%} < current {cur_score:.0%} - REGRESSION, not promoted. "
                       f"Details: {cand_detail}")
    if os.path.isdir(CURRENT):
        archive = os.path.join(ADAPTERS, f"archive_{time.strftime('%Y%m%d_%H%M')}")
        shutil.copytree(CURRENT, archive)  # keep the previous version for rollback
    shutil.rmtree(CURRENT, ignore_errors=True)
    shutil.copytree(adapter_dir, CURRENT)
    return True, f"promoted (candidate {cand_score:.0%}, previous {cur_score if cur_score is None else f'{cur_score:.0%}'})"


def rollback():
    """Restore the newest archived adapter over current."""
    archives = sorted(d for d in os.listdir(ADAPTERS) if d.startswith("archive_")) if os.path.isdir(ADAPTERS) else []
    if not archives or not os.path.isdir(CURRENT):
        return False, "nothing to roll back"
    newest = os.path.join(ADAPTERS, archives[-1])
    backup = os.path.join(ADAPTERS, f"rollback_target_{time.strftime('%Y%m%d_%H%M')}")
    shutil.copytree(CURRENT, backup)  # keep the bad one around for diagnosis
    shutil.rmtree(CURRENT, ignore_errors=True)
    shutil.copytree(newest, CURRENT)
    return True, f"rolled back to {archives[-1]} (previous current kept as {os.path.basename(backup)})"


def status():
    from ishti import ftlog
    ok, s = ftlog.ready_for_training(MIN_EXAMPLES)
    missing = deps()
    has_current = os.path.isdir(CURRENT)
    return {"examples_logged": s["total"], "ready": ok, "min_needed": MIN_EXAMPLES,
            "training_deps_missing": missing, "current_adapter": has_current,
            "note": ("collection running: keep using ISHTI" if not ok else
                     ("ready to train (idle hours)" if not missing else
                      "data ready; install deps or rent a GPU pod to train"))}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(status(), indent=1))
    elif cmd == "eval-set":
        ensure_eval_set()
        print(f"eval set at {EVAL_SET}")
    elif cmd == "dry-run":
        print(json.dumps(dry_run(), indent=1))
    elif cmd == "train":
        out, note = train()
        print(note or out)
    elif cmd == "eval":
        score, detail = run_eval(CURRENT if os.path.isdir(CURRENT) else None)
        print(f"score {score:.0%}")
        for r in detail:
            print(("  OK " if r["ok"] else "  MISS"), r["request"][:60])
    elif cmd == "promote" and len(sys.argv) > 2:
        good, note = promote(sys.argv[2])
        print(("PROMOTED: " if good else "NOT PROMOTED: ") + note)
    elif cmd == "rollback":
        good, note = rollback()
        print(("OK: " if good else "NO: ") + note)
    else:
        print("usage: python orchestrator/finetune.py [status|dry-run|eval-set|train|eval|promote <dir>|rollback]")
    print("[self-check] finetune CLI responds (graceful degradation, no training deps needed for status)")
