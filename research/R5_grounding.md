# R5 — Local screen-grounding / vision for computer use on 16 GB VRAM (Grounding-Tester agent)

Session: 2026-10-01. Machine state at research time measured live (commands inline).

## A. Machine state measured now
- `nvidia-smi --query-gpu=memory.used,memory.total`: **1522 / 16303 MiB** (idle-ish, other apps hold ~1.5 GB) — 2026-10-01.
- Ollama: **not running** (`curl http://127.0.0.1:11434/api/version` → connection refused, 000; no ollama.exe in tasklist). Per the safety rules I do not start the model server un-supervised ⇒ **every VRAM/model measurement below that needs a live server is BLOCKED**, with the exact need in NEEDS.md (N24: "owner starts ISHTI/Ollama once so grounding can be measured").
- `faster-whisper` 1.2.1 installed (pip list 2026-10-01); the local turbo STT build lives in
  `data/models/whisper-large-v3-turbo` (repo voice docs; path checked) — voice notes arriving via Telegram
  will transcribe through the existing `ishti.voice.transcribe()` path, no new model needed.

## B. Grounding strategy (design, consistent with the master prompt's "UI Automation tree first")

| Layer | Mechanism | Cost | Status |
|---|---|---|---|
| 1. UI Automation / accessibility tree | Windows UIA via pywin32/UIAutomation — text pattern of buttons/fields/values, exactly like the repo's browser `browser_snapshot` text approach | CPU-only, ms-class | AVAILABLE (pywin32 312 present). This is the primary grounding: it gives element names + coordinates without any vision model. WP-B measures real coverage on the 5 protocol tasks. |
| 2. Screenshot + local vision model | Existing repo vision tiers: `gemma3:4b` (tier 2) / `gemma3:12b` (tier 3, needs 95% budget — NEEDS.md N3 measured 8973 MiB) describe screenshots; coordinates then come from the UIA tree or asked-for region logic | 4B ≈ 3 GB / 12B ≈ 9 GB VRAM (both previously measured on this GPU per MODEL_DECISIONS/NEEDS.md N3, 2026-09-29) | PARTIAL: mechanism exists in-repo; live accuracy/latency re-measure BLOCKED with Ollama down (N24) |
| 3. CUA-S1 / OmniParser-style detector | trycua's small grounding models / cua-perception extension | ONNX on GPU/CPU; extension is **AGPL-3.0-only** (R3) | EXCLUDED by default (license). CUA-S1 weights: early research release, separate HF license — watch-list only, not built now |

**Design decision (feeds WP-B):** grounding = UIA tree first (no VRAM), screenshot+existing vision tiers
as fallback (already governed by the VRAM governor), external detector models excluded on license grounds
unless the owner approves. No partial offload rule applies to any model used — verified by the existing
`/api/ps` sampler in the concurrency suite (0 partial offloads in 663 samples, CONCURRENCY_REPORT.md).

## C. What can be tested right now (no GPU needed) — and what was tested
1. UIA availability probe (pywin32 `win32com.client` + `IUIAutomation`) — run in WP-B feasibility, CPU-only.
2. Voice-note transcription pipeline offline: `ishti.voice.transcribe` on a synthesized opus/ogg file — covered by existing `tests/test_voice_note_generation.py` + STT tests; the channel test reuses those fixtures.
3. Image-in pipeline: routed to the existing vision skill chain behind the governor (measured in the skill loop, video 9.4 GB shared VRAM flat — PROGRESS.md QA table).
4. VRAM governor integration: the channel layer itself loads NO models; it only triggers existing governed paths ⇒ no new partial-offload risk surface (asserted by test 13 in TEST_RESULTS).

## D. cua-driver's own grounding (for WP-B)
The official README (retrieved 2026-10-01) says background delivery "when the app and platform support it";
on Windows the driver's accessibility/UIA usage is the vendor's approach — WP-B measures it on the 5 tasks
rather than trusting the claim. License facts in R3 §A.
