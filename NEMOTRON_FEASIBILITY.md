# NEMOTRON_FEASIBILITY.md - Nemotron 3 Diarization on this PC (2026-09-30)

Everything below was measured on this machine (RTX 5070 Ti 16 GB, Python 3.11.9, torch 2.11.0+cu128),
not quoted from NVIDIA's material. Probes: `.qa_nemotron/probe1_result.json`, `probe2_result.json`.

## What was actually run

| Check | Result | Evidence |
|---|---|---|
| transformers path available | **PARTIAL**: PyPI `transformers 5.17.0` (latest release) does NOT contain the model; only git `main` (5.18.0.dev0, model merged 2026-09-23) does | `import transformers; hasattr(...,'Nemotron3DiarizationModel')` -> False on 5.17.0; True on git-main installed to `.qa_nemotron/transformers_main` (isolated, ISHTI venv untouched) |
| Loads on Blackwell (Windows 11) | **YES** - CUDA, fp32 | probe1: `{"load_s": 42.1, "device": "cuda"}` (42 s incl. first weight download) |
| VRAM after load | **~1.0 GB** (1,037 MiB total incl. desktop; model itself small) | probe1 nvidia-smi; probe2 1,153 MiB total used |
| Offline speed | **RTF 0.005** - 97.6 s real speech in 0.51 s | probe2 `{"offline_infer_s": 0.51, "rtf": 0.005}` |
| Offline quality (real speech) | **29 segments, 6 speakers found** on the official 6-speaker sample; boundaries look sane (sample segs 0.35-9.31 = spk0, 9.15-13.81 = spk1, ...) | probe2 |
| Streaming (low_latency) | **works; 1.04 s algorithmic latency, 15.6 ms/step compute, RTF 0.022**, 136 steps for 97.6 s | probe2 |
| Synthetic tones (sanity) | 0 segments on pure sine "speech" - correct behavior: it is a SPEECH model, silence/tones score below threshold; cannot be used as an accuracy benchmark | probe1 `segments_found: 0` |
| Voice-note / Urdu-Pashto accuracy on MY audio | **NOT MEASURED** - needs real multi-speaker recordings (mic hardware absent, NEEDS.md N7) | BLOCKED |
| NeMo path | not attempted: `nemo-toolkit[asr]` wants Python 3.12 + apt packages; the transformers path already works | skipped with reason |
| NeMo-Speech.cpp | not attempted (C++ build); unnecessary given the transformers result | skipped with reason |

## Verdict: **GO (as an on-demand tool)** - with two honest BLOCKED sub-items

- Runs natively on this GPU via transformers git-main today; the class ships in the next stable
  transformers release, after which the pinned version can move to PyPI.
- Cheap: ~1 GB VRAM, RTF 0.005-0.022 - fits beside whisper + Fish Speech well inside 16 GB with no
  partial offload (governor can hold it as an on-demand model, unloaded when idle like other rungs).
- BLOCKED sub-items: (1) accuracy on the owner's Urdu/Pashto/WhatsApp voice notes - needs real
  recordings; (2) streaming latency under live conditions - algorithmic latency measured (1.04 s /
  0.64 s / 0.32 s modes), compute per step 15.6 ms; end-to-end mic latency needs the headset (N7).

## What it does NOT do (fit discipline)

- No identities: generic `speaker_0..7` ordered by first arrival. "Who is this?" needs separate
  voice enrollment (local-only, owner-enrolled people) - a different model, not this one.
- Does not fix English-misheard-as-Urdu: that is ASR/language-ID, not diarization.
- Does not replace Silero VAD for ~150 ms barge-in (its lowest useful buffer is 0.32 s).

## Worth building (proposal, in order)

1. **chat_digest voice notes with several voices** (offline path): diarize the note, per-speaker
   whisper passes, "Speaker 1 (probably Bilal) said..." with Unknown where enrollment is absent. GO.
2. **Multi-speaker meeting/call recordings** -> speaker-tagged transcripts (offline). GO.
3. **"Ignore background/other speakers"** for wake-word robustness (streaming, ultra_low_latency).
   PARTIAL - only worth it if false-wake data shows the problem; do not build speculatively.
4. Live voice-session speaker tagging: NO-GO for now (adds VRAM pressure during voice; the 0.32 s
   mode still is not barge-in).

## Integration proposal (ISHTI style)

- New on-demand module `ishti/diarize_nemotron.py` behind the VRAM governor (`vram.admit` role
  "diarization", unload after 10 idle minutes like every non-pinned model; never partial-offload).
- Transformes pin: use git-main commit + hash in MODEL_DECISIONS.md until PyPI carries the model;
  the isolated `.qa_nemotron/transformers_main` install stays OUT of the ISHTI venv.
- License: **OpenMDW 1.1** - commercial use allowed; keep the license file next to the weights and
  the attribution line in MODEL_DECISIONS.md.
- Entry point: extend the `chat_digest` voice-note path + a `who spoke in <file>` route on the
  existing `diarize.py` skill; T0 read-only on files.
