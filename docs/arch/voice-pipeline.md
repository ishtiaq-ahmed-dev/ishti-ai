# Voice pipeline (ishti/voice.py → wake.py → talk.py)

## In (STT)

- One decode thread, forever: CTranslate2's CUDA/cuBLAS state dies with a second decoding thread
  (measured 0xC0000409 crash, 2026-09-27) — `voice.stt_call` serialises every touch of the model
  (partials, language pick, warm-ups, unload) onto a single worker.
- Model: `large-v3-turbo` fp16 on the GPU (config `whisper_model`; ~3.0 GB footprint incl. context).
  Warm decode ≈ 167 ms for 2 s of speech (measured this session, 5-run median). Cold load = seconds
  of disk + kernel warm-up, paid at load time by `_warm` (never inside the owner's first command).
- One decode per utterance: language is detected once during speech (`Stream`, live partials) or one
  bounded pre-pass (`pick_language`, ~0.2 s, en/ur/ps with primary-bias thresholds) and handed to
  transcribe; `language=None` costs a second encoder pass and is not used.
- Long audio (>8 s, `BATCH_OVER_S` measured) goes through BatchedInferencePipeline (30 s chunks in
  one batch) instead of chunk-by-chunk.
- Idle unload after 600 s (`whisper_idle_unload_s`) — ~1.6–3 GB back to the planner when the owner
  walks away; the wake loop reloads transparently.

## Wake (ishti/wake.py)

- openWakeWord ONNX on 80 ms frames (config `hey_jarvis`), plus a whisper-tiny CPU spotter on
  speech bursts that catches the other configured phrases ("hey ishti", "hey my assistant") with
  fuzzy word matching (`phrase_in`).
- Barge-in: mic stays open while ISHTI thinks/speaks; ≥120 ms of speech pauses the voice within one
  audio block (measured stop latency ≤200 ms); a 700 ms transcript decides echo vs interruption
  (word-overlap vs `talk.recent_words`, backchannels ignored); an interruption cancels generation,
  keeps the unheard text, and records the new command; during thinking-only, the task continues in
  the background and its answer is announced at the next pause ("About …").

## Out (TTS)

- `talk.Talker` — one output stream for everything; sentences are synthesised and queued while the
  model still writes (first audio after one sentence); pause/resume/stop act within one 23 ms block;
  `stop()` returns the unheard text so "continue" replays it.
- Piper (~373–426 ms per sentence, CPU, ~13% run-to-run noise) is the default engine; Kokoro exists
  as an engine option. Sub-agents never speak (`talk.set_subagent_context` — single-voice rule).
- Ducking: other apps' volume lowers while ISHTI talks (`ishti/duck.py`), restored when idle.

## Dispatch

The transcript enters `dispatcher.handle(text, channel="voice", on_token=…)` like typed text — the
only differences are narration of tool events (`NARRATE` labels) and the spoken-reply path above.
