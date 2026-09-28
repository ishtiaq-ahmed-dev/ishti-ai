# MAC_CHANGES — what was changed on the Mac session (2026-09-28) & what the PC agent must do

**Read this first if you are the PC agent.** The Mac session implemented the user's new
ARCH2 architecture (two-pass Qwen3-14B brain + four-layer memory + dashboard switching)
but **could not run ISHTI's real stack** (no Ollama, no ChromaDB, no GPU, Windows-only
paths like `D:\ISHTI` do not exist there). Everything below lists exactly what was
verified on the Mac and what was **NOT tested** — re-run the checks on the PC before
trusting any of it.

---

## 1. What was added / changed (files)

| File | Change | Mac-verified? |
|---|---|---|
| `ishti/arch2/__init__.py` | NEW — package docstring describing the architecture | compile-only |
| `ishti/arch2/registry.py` | NEW — single owner of "which architecture is active" (`arch1`/`arch2`), hot-switch, persists to `ishti/config.json`, voice command parser ("use arch 2") | compile-only |
| `ishti/arch2/memory.py` | NEW — the four-layer memory store (spec sections 3–21): semantic / episodic / procedural / observations on SQLite `data/arch2_memory.db`, unified memory objects, configurable promotion thresholds, supersede-not-overwrite, decay that only flags (never deletes), privacy-safe export, entities + links | compile-only |
| `ishti/arch2/retrieval.py` | NEW — spec section 14 pipeline: keyword+graph+vector recall, budgeted context assembly (`RELEVANT MEMORIES / PEOPLE / PROCEDURES / PAST CONTEXT`) | compile-only |
| `ishti/arch2/consolidate.py` | NEW — spec section 12: LLM extraction after each exchange; explicit-vs-observed rule; **kin relationships are never inferred** (spec 8/10); user memory commands (spec 18: "what do you remember about me", "who is Ahmed", "forget that", "remember that I …") | compile-only |
| `ishti/arch2/pipeline.py` | NEW — the two-pass brain: pass 1 Qwen3-14B thinks/plans/calls tools (tools ARE arch1's skills — same T2 gates, scopes, audit), **validation pass** judges every tool result, pass 2 writes the final answer. Spec 24 safety: high-impact verbs always go through the yes/no gate — memory never authorizes an action | compile-only |
| `ishti/llm.py` | `model_for("main"/"validation")` reads `arch.arch2.models`, falls back to planner model when qwen3:14b isn't installed | compile-only |
| `ishti/dispatcher.py` | 4 hooks: (a) arch-switch voice command, (b) memory commands (always available, both archs), (c) `arch.active == "arch2"` branch routes the turn through `pipeline.run`, (d) `_arch2_consolidate` runs in a background thread on `_finish` (skips scheduled + memory commands) | compile-only |
| `ishti/config.json` | NEW `"arch"` section: `active`, `arch2.models {main: qwen3:14b, validation: qwen3:14b, fallback}`, memory knobs (db name, retrieval k, context budget, **configurable thresholds** 2/5/10, decay settings) | JSON parse OK |
| `interface/web_ui/server.py` | NEW endpoints: `GET /api/arch`, `POST /api/arch/switch`, `POST /api/arch/memory/promote`, `POST /api/arch/memory/decay` | NOT tested (server needs the full stack) |
| `interface/app/src/components/ArchitectureCard.tsx` | NEW — Settings card: click ARCH1/ARCH2 to switch (live, no restart), four-layer memory stats, "Promote now" / "Decay check" buttons | **tsc clean + vite build OK** |
| `interface/app/src/sections/Pages.tsx` | Renders `<ArchitectureCard />` at the top of Settings | **tsc clean + vite build OK** |
| `tests/test_arch2_memory.py` | NEW — 13 offline tests (registry, four layers, thresholds, supersede/forget, decay floor, entities, privacy export, retrieval relevance, pipeline parsing/safety, memory commands, explicit-vs-observed, unified object shape) | **pytest reported 13/13 on the Mac** (see §3 caveat) |

No existing arch1 behavior was removed or rewritten. With `arch.active = "arch1"`
(the default), ISHTI behaves exactly as before; the only additions are the arch-switch
command, the memory commands, and the background consolidation writes.

## 2. Models the PC agent must download (NOT downloaded on the Mac)

```powershell
ollama pull qwen3:14b          # ~9.3 GB — ARCH2 main + validation brain
ollama pull nomic-embed-text   # ~274 MB — probably already installed (semantic memory uses it)
```

- qwen3:14b in 4-bit ≈ 9.3 GB VRAM. **It cannot sit next to the 10.4 GB planner** — with
  ARCH2 active, ISHTI's `ishti/models.py` residency manager treats it like any other
  role model (loads on demand, evicts idle). Expected worst case: arch2 brain (9.3) +
  Whisper STT (1.6) ≈ 11 GB — fits, but pause any pinned planner during ARCH2 testing.
- Nothing else is required. ARCH2 deliberately reuses Laya, Whisper, Kokoro and all skills.
- If you do NOT want to pull qwen3:14b yet: ARCH2 still works — `llm.model_for` falls
  back to `ishti-qwen2.5:14b` automatically (slower, tested-13B quality).

## 3. Verification status — what the PC agent must run

**Honest caveat:** near the end of the Mac session the terminal tool started returning
garbled, self-contradictory output (outputs not matching the commands sent). The React
build and one pytest run were observed green; the final repeated runs of the Python test
file could not be trusted. Treat all Python checks below as **NOT DONE until re-run**.

```powershell
:: 1. compile everything touched
python -m py_compile ishti\arch2\registry.py ishti\arch2\memory.py ishti\arch2\retrieval.py ishti\arch2\consolidate.py ishti\arch2\pipeline.py ishti\llm.py ishti\dispatcher.py

:: 2. the new offline suite (no Ollama needed)
python -m pytest tests\test_arch2_memory.py -v

:: 3. the two new self-checks
python -m ishti.arch2.memory        :: needs no services
python -m ishti.arch2.pipeline      :: parsing/safety parts need no Ollama; LLM parts print a note

:: 4. regression: arch1 must be untouched
python tools\verify_all.py --quick

:: 5. end-to-end with Ollama running (after the pulls in §2)
python -m ishti.check
```

Then the live smoke test (dashboard):

1. `python -m ishti` → open `http://127.0.0.1:8888` → Settings → **Architecture** card
   (top of the page). Click **ARCH 2 · Two-pass brain**.
2. Send: *what's the weather in Peshawar?* — expect trace events:
   `thinking with qwen3:14b (pass 1)` → `tool web_search` → `verify: checking the tool
   really did what was asked` → `writing the final answer ... (pass 2)`.
3. Say: *remember that I prefer short emails* → then *what do you remember about me?*
   → the fact must come back.
4. Say: *forget short emails* → ask again → must be gone.
5. Check `data/arch2_memory.db` exists and Settings shows live memory counts.
6. Switch back to ARCH 1 in the card → next message must route through Laya as before.

## 4. Known gaps / next passes (for whoever picks this up)

- **ARCH2 tool-calling relies on qwen3:14b emitting strict JSON.** If it wobbles, the
  parser (`pipeline._parse_tool_call`) returns None and the model just answers directly —
  graceful, but you lose tools. If that shows up on the PC, add one retry with a
  stricter prompt, or route pass 1 through Ollama's native tool-call API like the chat skill does.
- Consolidation extraction is one extra LLM call per exchange (~0.5 s on the 14B, in a
  background thread — never blocks the answer). If the PC feels slower with ARCH2,
  make it sample (e.g. 1 in 2 turns) via a config knob.
- The relationship graph is still `ishti/rgraph.py` (shared with arch1) — arch2 reads it
  through `retrieval.graph_hits` and `memory.entity_card`. Neo4j upgrade path stays as documented there.
- Dashboard switching, promote/decay buttons and the `/api/arch` endpoints were never
  clicked in a running ISHTI (Mac has no server) — that's what smoke steps 1/5 above cover.
- `write_todos`'s final audit (the `/audit` skill) could not be completed honestly: with
  the terminal corrupted, CORRECTNESS caps at what was actually exercised before the
  corruption (compile checks, one clean React build, one clean pytest run). Design/spec
  coverage is strong; runtime behavior is unproven until the PC run.
