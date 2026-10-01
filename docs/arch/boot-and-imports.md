# Boot and imports — what the process pays, and what the optimization changed

Measured 2026-10-01 with `python -X importtime` (evidence: `_work/importtime_skills.log` and the
re-runs referenced in TECHNIQUE_LOG.md).

## The chain

```
python -m ishti
└─ ishti/__init__            CONFIG (config.json), UTF-8 streams, PDFIUM_LOCK      ~10 ms
└─ ishti.dispatcher (import)
   ├─ ishti.scope            builtins.open / os / shutil / requests / urllib hooks
   ├─ ishti.killswitch       propagate() into Thread + ThreadPoolExecutor
   ├─ ishti.skills.load_skills()
   │    122 × Skill(manifest.json + skill.py import)          ← the 1174 ms → 358 ms line
   ├─ ishti.planner → ishti.llm   OpenAI client  (was 589 ms — now lazy, K1)
   └─ orchestrator.memory / response_guard
└─ ishti.lifecycle           Job Object, registry, boot stages
└─ interface/web_ui/server   FastAPI app + routers (dispatcher.api, lifecycle_api, remote_api)
└─ dispatcher.warm_up()      Laya fp16 preload, voice engine preload (background)
```

## The two kept changes

| # | Was | Cost (importtime cumulative) | Now |
|---|---|---|---|
| K1 | `ishti/llm.py` built the OpenAI client at module import (`from openai import OpenAI` + `OpenAI(...)`) — paid by 26 skill modules that `import llm`, even for reflex/rule turns | 589 ms | `_get_client()` creates it on first LLM call; base URL read per call so `ISHTI_OLLAMA_URL` still applies |
| K2 | `ishti/skills/browser/skill.py` imported `ishti.mcp_client` at module level, which imports the `mcp` package — paid at every boot even when the browser is never used | 362 ms | `get_hub()` in the browser skill is a call-time shim to the same singleton; tests that patch `skill.get_hub` as an attribute keep working |

Result: `skills_load` median **1174.04 → 358.43 ms (−69.5%)**; `import ishti` unchanged (~10 ms);
route/reflex/LLM/voice benchmarks unchanged (perf_guard PASS on all runs).

## What was measured and rejected (do not redo)

- Caching manifests across load_skills(): only games the benchmark — boot calls it once.
- Lazy skill.py imports: moves cost into the first request; `matches()` needs the module on every
  route; broken-skill status must stay honest at boot.
- Remaining ~250 ms = genuine import of 122 skill modules (requests 64 ms, numpy 46 ms,
  tools.web 24 ms…). Below that is per-skill refactoring, out of proportion.
- `corrections.learned()`: 0.007 ms at current table size — no change needed; revisit if the
  corrections table grows past ~2000 rows (it does one LIMIT-2000 SELECT per route; a 5 s TTL cache
  would be the fix, mirroring router._flags_cache).
