# ARCH COMPARE REPORT — arch1 vs arch2, same 20-row workload (2026-09-30)

## Method
- Driver: [tools/arch_compare.py](../tools/arch_compare.py) (committed; raw per-row data in
  `reports/arch_compare_results.json`). Same 20 requests through the REAL `dispatcher.handle()`
  twice — once with `ishti/arch2/registry.set_active("arch1")`, once with `("arch2")` (the same
  code path the dashboard and the "use arch 2" voice command use). `ISHTI_TEST_MODE=1` throughout;
  config.json restored to arch1 after (verified).
- A FRESH session per row (an earlier draft shared sessions and one row's pending T2 gate ate the
  next row — evidence that session isolation matters, kept as a driver rule).
- Rows: deterministic answers (calc, sudoku, quickcalc, multi-step math), storage skills (notes,
  checklist, subscriptions, memory store/recall), routing-sensitive asks (owner facts, meta health,
  vocab), language handling (Urdu script), honesty probes (decline, unknown-refusal), conversation.

## Headline numbers (measured, one full run after checker fixes)
| metric | arch1 (dispatcher) | arch2 (two-pass pipeline) |
|---|---|---|
| checks passed | **19/20** | **19/20** |
| mean latency | 7.2 s | **1.3 s** |
| median latency | 1.4 s | **0.9 s** |
| max | 79.7 s (meta health row) | 8.7 s (sudoku row) |
| pipeline mode | rules/skills | two-pass answered 18/20 (2 rows caught by arch1's deterministic memory-manager rules first, by design) |

Caveat stated plainly: arch1's mean is dominated by the meta health row (79.7 s of real
subprocess self-checks); medians (1.4 vs 0.9 s) are the fairer view — arch2 is faster on this
workload but not 5× on typical rows. arch2's qwen3:14b was warm (loaded during the arch1 phase);
cold-start cost is reported separately in the perf baselines.

## What each architecture won (evidence = recorded reply heads)
### arch2 wins
- **Multi-step arithmetic**: "250 + 175" computed → 425. arch1 routed the same sentence to the
  LEDGER skill (answered "Spending this month: PKR 2,500" — a month summary, not the question).
- **Episodic memory recall**: "what is my sister's name" → arch2 memory layer: "Your sister's name
  is Zainab." arch1 routed it to FILES ("No files matching 'what is my sister's name'…").
- **No translator tax**: arch1's language-rewrite layer (owner lock = Roman Urdu) sends every
  English reply through gemma3:4b; arch2's branch skips it — and answered the Urdu row in Urdu
  natively, correctly routed (see the arch1 cost below).

### arch1 wins
- **Live-data discipline**: the Urdu weather row — arch1's router hit the WEATHER skill and
  returned real data ("Today in Peshawar: 32°C high, 19°C low, mostly clear, open-meteo.com").
  arch2's brain chose to answer DIRECTLY from the model and hallucinated a confident paragraph
  about Peshawar's "seasons" (no live data). **This is arch2's biggest accuracy risk.**
- **Dedicated solvers**: sudoku — arch1's solver produced the verified grid (rows re-checked in
  code). arch2 shortlisted the skill but tried to solve it MANUALLY in the model and failed.
  qwen3's 1B-scale reasoning cannot beat a deterministic backtracker it already has access to.
- **Edge correctness**: honest declines and the T2 gate text were exact on arch1.

### Bugs the comparison exposed (arch1) — fixed this session
- **FIX-13 (real user-facing bug)**: with the language lock set, the translator model sometimes
  ignores the payload and answers "Please provide the Urdu/Hindi text…", and that question USED
  TO REPLACE the notes skill's correct reply. translate() now retries once and falls back to the
  original text. Regression tests: `tests/test_dispatcher_translate_guard.py` (4).
  arch1 lost the notes row to this bug in the first run; arch2 won the same row because its path
  skips the rewrite layer.

## Verdict and ready-to-implement recommendations
The architectures are complementary: arch1 = fast deterministic edges + real-data discipline;
arch2 = robust reasoning, memory recall, and no single-skill routing gaps. arch2's check score
equalled arch1's while answering 18/20 through the brain — but only after FIX-13 levelled the
field, and its two wrong answers (weather hallucination, sudoku manual solve) are exactly the
class arch1 never makes. Highest-value next steps (both one-line prompt/shortlist nudges in
`ishti/arch2/pipeline.py`, not yet implemented — owner call):
1. Force tool-use for live-data request classes (weather/prices/news): add to SYSTEM_PASS1:
   "For anything that changes by the hour (weather, prices, news), ALWAYS call a tool; never
   answer from memory."
2. Prefer dedicated solver skills: "If a shortlisted skill exactly matches the task (sudoku,
   calc, forecast), call it instead of solving manually."
3. arch1: add arithmetic phrases to the calc skill's router (or planner) so "how much did I
   spend" math isn't eaten by LEDGER; and consider skipping the translator rewrite when the
   skill reply is data the owner asked for in English (path/number-heavy rows).
