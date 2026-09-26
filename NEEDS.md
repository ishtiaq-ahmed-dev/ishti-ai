# NEEDS: blocked or deferred work

Each entry lists what's needed, why it's blocked, and what would have to change. Items are removed when done.

## 1. Fine-tune Laya on ISHTI's own traffic
- **Need:** a `laya-typed-decisions` checkpoint fine-tuned on real ISHTI routing decisions.
- **Blocked by:** data volume. Every decision is logged to `data/ishti.db` (`route_log`). Saying **"that should have been <skill>"** or **"use the <skill> for that"** re-runs the last request with that skill and logs it with `source = correction` (a gold label). A fine-tune needs a few hundred such labels; so far there are about 25 (`ishti/check.py` `LABELLED`).
- **To change:** once there are enough, export `GET /api/route_log` (corrections are gold labels, `rule` rows are high-precision weak labels), run the upstream Kaggle notebook (`notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb` in NandhaKishorM/laya), and point `router.laya_model` at the new checkpoint. No router code change is needed.

## 2. Visual (screenshot) fallback for the browser skill
- **Need:** click by coordinates on pages whose accessibility tree is useless (canvas apps, some captcha-free widgets).
- **Blocked by:** no local vision model. `qwen2.5:14b` is text-only, so Playwright MCP's `--caps vision` tools would be driven blind.
- **To change:** pull a local VLM (e.g. `qwen2.5vl:7b`, about 6 GB) and add a `vision` role in `config.json`. When a step fails because no usable ref exists, the browser skill takes `browser_take_screenshot`, asks the VLM for coordinates, and uses the vision tools. VRAM is the constraint: 14B planner (11 GB) + VLM (6 GB) don't fit together, so Ollama will swap (about 5 s per swap).

## 3. Planner and coder can't both stay in VRAM
- **Now:** a code question loads `ishti-qwen2.5-coder:14b` and evicts the planner, so the first reply after a switch costs about 5 s.
- **Options:** use `qwen2.5-coder:7b` (both fit), or accept the swap. The config change is one line; no code change.

## 4. Delete the old engine
- **Now:** every request goes through dispatcher skills. The `legacy` skill is gone, replaced by `files`, `apps`, `system`, `video`, `deep_research` and `web_fetch`. `orchestrator/crew.py` (1100 LOC), `planner.py`, `self_heal.py`, `model_router.py` and `memory/capability_registry.py` + `capability_escalation.py` are no longer on the request path.
- **Still uses them:** `orchestrator/test_e2e.py`, `test_capabilities.py` and `doctor.py`, which test through `ExecutionEngine`. `server.py` still builds one at import (for `/api/status` and `/api/rebenchmark`).
- **To change:** port those tests and doctor checks to `ishti.dispatcher.handle`, then delete the modules. `intent_detector.py` stays: skills use it as their trigger library.
- **Missing feature this leaves:** multi-skill plans ("find my resume and summarize it"). The old DAG planner only knew its own action types. A new planner would ask the LLM for a list of `(skill, request)` steps over the skill manifests and run them in order through `handle`.

## 6. Custom "hey ishti" wake word
- **Now:** hands-free listening works (`ishti/wake.py`) with openWakeWord's pretrained **"hey jarvis"** model. Replies are spoken, and each exchange shows up in the HUD. No microphone is connected at the moment; the listener retries every 30 s.
- **Need:** a "hey ishti" model.
- **Why deferred:** openWakeWord custom models are trained from thousands of synthetic clips plus negative audio (about 1 hour on a GPU), using their training notebook.
- **To change:** generate the positive clips locally with Piper (already installed, many voices), train with openWakeWord's `train.py`, drop the `.onnx` into `data/`, and set `voice.wake_model` to its path. No code change.

## 9. Semantic-memory threshold
- `ishti/semantic.py` keeps recalls with cosine distance ≤ 0.45, calibrated on a handful of samples. Re-tune it once real conversation history exists (log the distances, pick the knee).
