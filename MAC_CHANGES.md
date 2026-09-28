# MAC_CHANGES — arch2 upgrade pass (2026-09-28, v2) & the PC agent's checklist

**For the PC agent / owner:** this file is the hand-off. Everything was re-verified on the
Mac after the v1 pass; the runtime checks that need Ollama/Chroma/Windows are listed in §5
and are the first thing to run when the PC opens this project.

---

## 1. What this pass changed (all verified, see §4)

### 1.1 `ishti/arch2/jsonio.py` — NEW: robust JSON layer (Ollama structured outputs)
Every arch2 model call that must return JSON now goes through `ask_json()` /
`parse_json()`:
- `response_format={"type": "json_object"}` — Ollama constrains the decoder itself
  (logit shaping), so qwen3:14b physically cannot wrap tool calls or verdicts in prose.
- Defensive parse: markdown fences stripped, `json.loads`, then a balanced-brace scan
  (survives leading chatter, trailing text, braces inside strings, escaped quotes).
- One explicit retry on parse failure; callers pass `on_json_fail` for a safe default.
This closes the #1 architectural risk: "will the 14B emit clean tool-call JSON?"

### 1.2 `ishti/arch2/pipeline.py` — the spec's Intent Router + 5–20 skills stage
- NEW `shortlist()`: deterministic rules + Laya's per-skill scores (0.2 ms, already
  paid for in arch1) rank usable skills for THIS request; the top **SHORTLIST_MAX=20**
  (never fewer than SHORTLIST_MIN=5; core chat/memory/web_search always included) are
  what the Qwen3-14B brain sees — exactly the spec diagram's "Intent Router → 5–20 skills".
- Pass 1 tool calls parsed via jsonio (fences/chatter/trailing text all survive).
- **Validation layer** is JSON-constrained with a safe default: if the validator
  itself fails, the tool's own output is trusted rather than blocking the answer.
- Spec-24 safety unchanged: send/delete/pay/etc. always hit the T2 yes/no gate —
  memory never authorizes an action.
- Self-check: `python -m ishti.arch2.pipeline` (no Ollama needed).

### 1.3 `ishti/arch2/memory.py` — mem0 reconcile + Zep bi-temporal + provenance
- **`reconcile()`** (mem0-style): a new candidate fact is classified against existing
  memories as **ADD / UPDATE / DELETE / NOOP** before storing. UPDATE supersedes,
  DELETE only when the owner explicitly retracted, NOOP skips duplicates. Degrades to
  a plain ADD when the model stack is absent (self-checks/CI never crash on it).
- **Bi-temporal-lite** (Zep/Graphiti): `supersede()` stamps `valid_until` on the old
  row ("was true until T") and `meta={"supersedes": old_id}` on the new one — full
  provenance chain, nothing silently overwritten (spec 17).
- **`display` column**: people keep their typed casing ("Ahmed") while the key stays
  lowercase for matching; entity cards and family lists now show it properly.
- **`forget_person()`** + **`entities()`**: "forget everything about Ahmed" marks every
  memory mentioning the person forgotten, drops their links, and resets the entity to
  `unknown` with a note (audit trail preserved — rows are never physically deleted).
- Schema migration is automatic (`ALTER TABLE ... ADD COLUMN` guarded).

### 1.4 `ishti/arch2/consolidate.py` — extraction hardened + spec-18 command set complete
- Extraction now requests `response_format={"type": "json_object"}` (decoder-constrained).
- **Spec-18 user commands, complete:**
  "what do you remember about me" · "remember that I ..." · "forget that" ·
  **"forget everything about Ahmed"** (entity-scoped) · "who is Ahmed" ·
  **"what do you know about my family"** (kin entities from arch2 entities + rgraph
  kin edges; family facts never exported to client-facing context) ·
  "what preferences have you learned" / "what have you learned from my behavior" ·
  **"don't remember this conversation"**.
- Kin relationships still can never be inferred (spec 8/10): explicit statement in the
  owner's own words or nothing.

### 1.5 Unchanged from v1 (already in place)
- `registry.py`: hot arch1/arch2 switch persisted to `ishti/config.json`; voice command
  "use arch 2"; `/api/arch`, `/api/arch/switch`, `/api/arch/memory/promote`,
  `/api/arch/memory/decay` endpoints; Settings → Architecture card in the HUD
  (switch by click, live memory stats, Promote/Decay buttons).
- Dispatcher wiring: arch2 branch, memory commands, background consolidation.
- `config.json` `arch` section: models (`main`/`validation` = qwen3:14b, fallback
  ishti-qwen2.5:14b), thresholds 2/5/10 (configurable, spec 11), decay settings.
- Privacy: `export("client")` strips health/finance/conversation-sourced rows; rgraph
  kin edges stay private.

## 2. File map (what to look at first)

| File | Role |
|---|---|
| `ishti/arch2/registry.py` | which architecture is active (single owner) |
| `ishti/arch2/pipeline.py` | the two-pass brain: router → shortlist → Qwen3-14B → tools → validation → final |
| `ishti/arch2/memory.py` | four-layer store + reconcile + bi-temporal + entities |
| `ishti/arch2/retrieval.py` | relevant-only context assembly (budgeted) |
| `ishti/arch2/consolidate.py` | learning after each turn + the spec-18 commands |
| `ishti/arch2/jsonio.py` | decoder-constrained JSON for every structured call |
| `tests/test_arch2_memory.py` | 17 offline tests |
| `interface/app/src/components/ArchitectureCard.tsx` | the dashboard switch |

## 3. Mac-verified this pass (all green)

```
python -m ishti.arch2.pipeline     [self-check] parse, confirmation, shortlist window
python -m ishti.arch2.memory       [self-check] reconcile/ADD, bi-temporal, decay, privacy export
python -m ishti.arch2.retrieval    [self-check] keyword recall, procedure match, no noise
python -m ishti.arch2.jsonio       [self-check] fences, chatter, nested braces, escapes
python -m pytest tests/test_arch2_memory.py    17 passed
tsc -b + npm run build (interface/app)         clean build
```

Not run on the Mac (by design — this is the Windows runtime): Ollama calls, Chroma,
the FastAPI server, the HUD against a live backend.

## 4. The one design trade-off to know about

`reconcile()` adds one extra LLM classification per new semantic fact (consolidation
runs in a background thread, never blocking the answer; JSON-fail degrades to ADD).
If the PC shows memory churn, raise `arch.arch2.confirmation_thresholds.temporary`
or sample consolidation to every Nth turn — both are config-only changes.

## 5. PC agent checklist (run in this order)

```powershell
:: 0. models (skip what's already installed)
ollama pull qwen3:14b          ;: ~9.3 GB - arch2 main+validation brain
ollama pull nomic-embed-text   ;: ~274 MB - probably present already

:: 1. offline re-verification (no services needed)
python -m py_compile ishti\arch2\pipeline.py ishti\arch2\memory.py ishti\arch2\retrieval.py ishti\arch2\consolidate.py ishti\arch2\jsonio.py ishti\arch2\registry.py ishti\dispatcher.py
python -m ishti.arch2.pipeline
python -m ishti.arch2.memory
python -m ishti.arch2.retrieval
python -m ishti.arch2.jsonio
python -m pytest tests\test_arch2_memory.py -v        :: expect 17 passed

:: 2. arch1 regression (must be untouched)
python tools\verify_all.py --quick

:: 3. live end-to-end (Ollama running)
python -m ishti.check
python -m ishti                        ;: then open http://127.0.0.1:8888
```

Dashboard smoke test:
1. Settings → **Architecture** (top card) → click **ARCH 2 · Two-pass brain**.
2. Chat: *"what's the weather in Peshawar?"* → Thinking panel must show
   `thinking with qwen3:14b (pass 1)` → `tool web_search` → `verify` →
   `writing the final answer ... (pass 2)`.
3. *"remember that I prefer short emails"* → then *"what do you remember about me?"*
   → fact returns. *"forget that"* → gone.
4. *"Sara is my sister"* → *"what do you know about my family"* → Sara listed.
   *"forget everything about Sara"* → gone, entity reset.
5. *"use arch 1"* (voice or chat) → next message routes through Laya as before.
6. Check `data/arch2_memory.db` exists; Settings card counts match `memory.stats()`.

Report results into `PERF_LOG.md` (latency: arch2 pass1+validate+pass2 vs arch1) and
`NEEDS.md` for anything broken — the standard ISHTI gates.

## 6. Next passes worth doing (in value order)

1. **Per-arch performance board** (Overview tab): p50/p95 latency + routing accuracy
   per architecture from `route_log` + a new arch2 timing row — the owner's original
   "compare architectures" ask, one query + one chart.
2. **Hybrid retrieval fusion**: reciprocal-rank-fuse `memory_hits` (keyword) with
   Chroma vector hits instead of concatenating — highest-impact recall upgrade in the
   2026 retrieval literature.
3. **Voice for arch2 memory commands** is already wired; next is a wake-word-free
   "quick capture" phrase so habits/notes land without a full turn.
4. Procedural-memory *execution*: when a procedure matches ("my email workflow"),
   offer to run its steps through the planner with the owner's approval at each step.
