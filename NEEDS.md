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

## 4. Retire the `legacy` skill
- **Need:** each remaining `orchestrator/crew.py` route becomes its own skill: `pc_search`, `app_control`, `video_edit`, `telemetry`, `deep_research`, `web_fetch`.
- **Why deferred:** each route already works through the bridge. Splitting all six is a large rewrite with no user-visible gain today.
- **Then delete:** `orchestrator/memory/capability_registry.py` + `capability_escalation.py` (800 LOC, replaced by skill manifests), `planner.py`, `intent_detector.py` (replaced by Laya).

## 5. Streaming replies in the HUD
- **Now:** new skills return the whole reply at once. The HUD's word-streaming animation is cosmetic.
- **To change:** `llm.chat(stream=True)` plus a WebSocket event per chunk from the dispatcher (the HUD already listens on `/ws`).

## 6. Hands-free voice (wake word)
- **Now:** voice is push-to-talk (HUD mic button → local whisper).
- **To change:** wire `voice/wake_word.py` (energy threshold) or openWakeWord (open-source, CPU) to record → `ishti.voice.transcribe` → `dispatcher.handle(channel="voice")` → `ishti.voice.speak`. Needs a background audio thread in the server process.

## 7. Tests that write to production memory
- `orchestrator/doctor.py` tests 16-17 and `orchestrator/test_capabilities.py` still write "Test Project" / "my favorite color is blue" into the real owner memory (AUDIT B7).
- **To change:** point them at a temp directory, the way `ishti/check.py` does.

## 8. Remote access hardening
- The server binds 127.0.0.1 only. `/api/facts` (DELETE) and `/api/voice` have no auth, which is fine on localhost. If the Cloudflare tunnel in `remote/tunnel_setup.md` is ever enabled, Cloudflare Access must sit in front of it, or a bearer token must be added to the dispatcher routes first.

## 9. Semantic-memory threshold
- `ishti/semantic.py` keeps recalls with cosine distance ≤ 0.45, calibrated on a handful of samples. Re-tune it once real conversation history exists (log the distances, pick the knee).
