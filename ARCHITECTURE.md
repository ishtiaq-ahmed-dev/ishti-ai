# ARCHITECTURE — ISHTI (post-optimization, 2026-10-01)

One-page map of the running system; file-level detail is in [CODEMAP.md](CODEMAP.md), measured
numbers in [OPTIMIZATION_BASELINE.md](OPTIMIZATION_BASELINE.md), subsystem deep-dives in `docs/arch/`.

## Boot path (measured)

```
python -m ishti (ishti/__main__.py)
  └─ lifecycle.setup_logging / single_instance (mutex) / adopt_job (Job Object)
  └─ import ishti.dispatcher                     ← SKILLS = load_skills() at import
       └─ ishti/__init__ (CONFIG from ishti/config.json, UTF-8 streams, PDFIUM_LOCK)
       └─ scope.install() (builtins.open + os/shutil/requests/urllib hooks) + killswitch.propagate()
       └─ ishti.skills.load_skills()  →  122 Skill(manifest+skill.py), broken-skill marking
       └─ import ishti.llm  →  OpenAI client now LAZY (K1: −589 ms)
       └─ browser skill imports mcp hub LAZY (K2: −297 ms)   →  skills_load 1174 → 358 ms (−69.5%)
  └─ interface/web_ui/server.py (FastAPI :8888): dispatcher api router, lifecycle_api, remote_api, UI
  └─ boot stages (lifecycle STAGES): ollama_ready → memory → skills → planner_warm (vram.admit,
     verified size_vram==size, never partial) → voice_ready → ready (events.publish per stage)
  └─ dispatcher.warm_up(): Laya fp16 preloaded (~4 s saved on first request), voice engine preload
```

## Request path (text)

```
POST /api/chat → executor thread → dispatcher.handle()
  1 killswitch.begin → View(session) → in-flight gating (FOLLOW_UP/CORRECTION waits, else parallel)
  2 _handle cascade (deterministic first):
      pending yes/no (T2 gate) → ambiguous resolve → reflex (0.003 ms) → mic → arch2/multi-agent/
      flowise profiles → language lock → correction re-route → pending-sticky → contextualize
      (Conversation State resolver, deterministic; LLM rewriter only when state can't resolve)
  3 planner.candidate (clause split; LLM plan only if ≥2 skills) → planner.run (parallel steps,
     {prev} chains, stops at pending confirmations)
  4 router.route (corrections.learned first → manifest matches 0.24 ms → Laya fp16 ~35 ms →
     min_confidence → default chat); recency_tiebreak; escalation ladder browser→web_search→deep_research
  5 _run: audit.context(skill, initiator, tier) → skill.run(text, session) → T2 test-mode/last_t2
     guards → CLAIM-verified honesty note
  6 _finish: sources cited-filter (main_sources), secrets masked (no_history/redact), history kept
     [-20:], persistence, then BACKGROUND threads only (semantic remember, ftlog, arch2 consolidate,
     owner learn, learning turn, monitor) — none on the reply path
```

## Voice path

```
wake.Listener (openWakeWord ONNX on 80 ms frames + whisper-tiny spotter on speech bursts)
  → barge-in watch (echo filter vs recent_words, 200 ms stop latency)
  → voice.Stream (live partials, language detected once during speech) → transcribe() single decode
    (large-v3-turbo fp16 on GPU, one dedicated STT thread — CTranslate2 is not thread-safe, measured)
  → dispatcher.handle(channel="voice", on_token=…) → talk.Sentences feeds tokens
  → talk.Talker: one audio stream, sentence-by-sentence synthesis (Piper/Kokoro), pause<1 block,
    stop returns the unheard text ("continue" picks it up), ducking while speaking
```

## GPU policy

- `ishti/vram.py` governor: measure (nvidia-smi + WDDM per-process + Ollama /api/ps) → plan
  (models_ladder.yaml, budget 80%) → admit (cheapest-first make-room) → **verify size_vram==size,
  never accept partial offload** → leases, busy marks, upshift with hysteresis, voice priority.
- `ishti/gpu.py` slot(): one heavy job at a time; `ishti/models.py`: pinned (planner+Laya) vs
  10-min evictable; every load/unload logged to data/model_usage.db.
- K1/K2 lazy imports interact fine: first LLM call creates the client; governor is untouched.

## Memory

- **Facts** (SQLite, exact, one row per entity+attr, previous kept) beat **semantic** (Chroma +
  nomic-embed, task-prefixed) — chat skill injects `mentioned_in` first and drops recalled docs
  that duplicate an exact fact. Corrections (D4) teach the router (exact request → 1 vote,
  similar → 2+ agreeing). Route log + ftlog feed the future Laya fine-tune.
- Conversation State (ishti/core/state.py): topic/task/entities/constraints/last_searches per
  session; resolver rewrites follow-ups before routing (D2), asks ONE clarifying question when
  ambiguous (T3).

## Safety (unchanged by the optimization work)

- Tiers T0–T3 (manifest `tier`), T2 explicit-yes gates in the dispatcher (`ask_first`), T3 config-gated.
- `ishti/scope.py`: per-skill write/net/apps enforcement at the Python API level; system folders,
  ISHTI's own code and system processes protected; subprocess paths checked where known.
- Kill switch: one call ends speech, generation, browser steps, tool children, pending questions,
  mic (killswitch.py → mic_control.mic_off), reachable from hotkey watchdog + /api/kill.
- Remote channels (ishti/channels/, docs/API_CONTRACT.md §11): token + DPAPI pairing + PIN +
  rate limits + approval expiry; FORBIDDEN classifier; test mode blocks T2 side effects at the
  last step (ishti/test_mode.py).
- Prompt injection: ishti/untrusted.py fences outside text, strips instruction-like content,
  second-layer reply cleaning.

## Concurrency model

- One session, many requests: View(shared, own=trace/on_token/route) lets parallel requests read
  shared history; INFLIGHT condition variable serializes follow-ups per session.
- GPU: llm._create busy-marks before the governor admits → one model load at a time; gpu.slot
  serializes heavy jobs; browser skill LOCK serializes pages per profile (RWMutex-free RLock).
- STT: exactly one decode thread (voice.stt_call) — documented crash otherwise (0xC0000409).
- Threads spawned in a request inherit kill generation + audit/scope context (killswitch.propagate).
