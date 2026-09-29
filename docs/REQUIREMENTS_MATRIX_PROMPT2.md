# PROMPT 2 — REQUIREMENTS MATRIX: Memory, Relationship Graph & Reinforcement-Like Learning

Status legend: **VERIFIED** (implemented + test/self-check evidence) · **PARTIAL** (implemented, one named piece pending) · **BLOCKED** (cannot be done now, reason given).

Every test named below lives in `tests/test_memory_prompt2.py` (J1–J14) or the pre-existing suites (`tests/test_arch2_memory.py`, `tests/test_learning*.py`), all green:
`70 passed, 2 skipped` (`python -m pytest tests/`), plus `python -m ishti.check` core loop OK (routing 329/329).

---

## Part A — Storage

| # | Requirement | Status | Evidence |
|---|---|---|---|
| A1 | SQLite default + vector layer + graph layer, selectable; no distributed arch | **PARTIAL** | SQLite is the only engine (`arch2_memory.db`, `rgraph.db`, `learning.db`); graph = SQL tables in `rgraph.py`; vector retrieval = existing local Chroma (`ishti/semantic.py`) via `retrieval.vector_hits`. Postgres/sqlite-vec/pgvector selectable backends: **not implemented** — `sqlite_vec` and PostgreSQL are not installed on this machine; documented design choice, selection stub left for a later prompt. |
| A2 | Minimum tables | **VERIFIED** | `memories, entities, memory_links, conflicts, events, tasks, task_steps, training_examples, interactions, forget_audit` (memory DB, `arch2/memory.py` + `core/migrations.py`); `nodes, edges, pending` (relationships, `rgraph.py`); `lessons, lesson_events, feedback, mistakes, candidates, workflows, entities, entity_mentions, dataset` (learning DB); conversations in the conversation store (Prompt 1). Self-checks assert every table. |
| A3 | Encryption at rest (key in OS keychain) + backup/export/import | **VERIFIED** | `core/vault.py`: Fernet field encryption (memories content/keywords, entity notes/aliases/contacts, rgraph edge details), key in Windows Credential Vault (keyring `ishti/memory_key`), key-file + `ISHTI_VAULT_KEY` fallbacks; hash-based dedupe (`content_hash`) survives encryption; `backup()` (consistent online copies), `export_all()`/`import_all()` (portable ZIP, decrypted, manifest). Self-check: ciphertext at rest, decrypted search, backup+round-trip. Off by default; `Settings.memory.encrypt_at_rest` + `/api/memory/vault` toggle encrypt/decrypt in place. SQLCipher unavailable → Fernet chosen (documented). |
| A4 | Migrations system | **VERIFIED** | `core/migrations.py`: registry `(store, version, description, statements)`, `schema_migrations` bookkeeping, additive-only, idempotent ALTERs, `pending()` for the dashboard. Wired into all three stores' `conn()`. Self-check: one-time apply, re-run no-op, old-DB idempotency. |

## Part B — Memory types

| # | Requirement | Status | Evidence |
|---|---|---|---|
| B1 | Unified memory object | **VERIFIED** | All spec fields present on every row (`test_unified_memory_object_shape`), plus `content_hash` for at-rest dedupe. |
| B2 | Semantic | **VERIFIED** | `memory.semantic()`, reconcile ADD/UPDATE/DELETE/NOOP; promotion from observations. |
| B3 | Episodic — important events only | **VERIFIED** | `consolidate.apply()` stores only extractor-proposed events; prompt bars transient details; `memory.event()` for decisions/commits. |
| B4 | Procedural | **VERIFIED** | `memory.procedural()` step lists; auto-retrieved per request (`procedures_for`, J13 family-tone procedure). |
| B5 | Sources; explicit > observed > inferred | **VERIFIED** | `source` on every row; explicit_user confidence 0.95 vs inference 0.5 (`test_consolidate_apply_explicit_vs_observed`); kin ONLY from explicit statements; `lessons.EXTERNAL_SOURCES` can never become lessons (spec 28 guard). |

## Part C — Relationship / entity graph

| # | Requirement | Status | Evidence |
|---|---|---|---|
| C1 | Entity types | **VERIFIED** | `kind` on entity rows + rgraph node kinds: person, organisation, project, app, preference; others accepted freeform. |
| C2 | Person record | **VERIFIED** | `arch2/people.py::record()` — entity_id, type, name, aliases, relationship_to_user, confidence, notes, contact_methods{email/phone/messaging}, preferences, important_facts, last_interaction; `set_contact`, `add_preference`, `confirm`. |
| C3 | Kin + social + utility relations | **VERIFIED** | `KIN` set (brother…grandchild, spouse/partner) + friend/colleague/classmate/frequently_contacted/works_at/works_on/messages_via/prefers/likes/dislikes (`rgraph.RELATION`). |
| C4 | Edge metadata | **VERIFIED** | `confidence`, `source_type` on edges (rgraph migration v1) + `created_at` (`first_seen`), `last_confirmed_at`; explicit re-statements upgrade, never downgrade (`rgraph.relate`). |
| C5 | Identity rules (no name-merge, no frequency inference, uncertainty asks) | **VERIFIED** | **J1** explicit kin = confidence 1.0; **J2** frequency → `frequently_contacted`, relationship stays `unknown`; **J3** three Ahmeds stay separate, bare mention asks; `people.resolve/mention` (strong disambiguators email/phone/org, kin decisive, weak ones never decide); `rgraph.relationship_of/ask_relationship`; `needs_confirmation` on weak claims. |
| C6 | Graph traversal for retrieval | **VERIFIED** | **J13**: request → brother edge → messages_via → WhatsApp + family procedure retrieved into context; `retrieval.graph_hits` (phrase + base-name node matching). |

## Part D — Memory manager (consolidation)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| D1 | Post-turn lightweight extraction | **VERIFIED** | `consolidate.extract/apply` on every exchange (`dispatcher._arch2_consolidate`), JSON-validated; model proposes, rules decide. |
| D2 | Do not save everything | **VERIFIED** | Importance + source rules; observations only promote past configurable thresholds. |
| D3 | Importance scoring | **VERIFIED** | explicit 0.75–0.9 / observation 0.4; `min_importance_floor` config; explicit identity/family/workflows rank top in retrieval. |
| D4 | Decay: needs_confirmation, never delete; stable facts slow | **VERIFIED** | `memory.decay()` — low importance ~1 month, high importance ~6 months (12×), both flagged `needs_confirmation`, never deleted; **J12**. |
| D5 | Contradictions → conflict record, newer explicit wins, history kept | **VERIFIED** | `conflicts` table written on every `supersede`; `resolve_conflict(keep=new/old)`; rgraph keeps its pending-questions flow for preference polarity; **J4**. |
| D6 | External content never becomes instructions/preferences | **VERIFIED** | `consolidate.extract` sanitizes via `untrusted.sanitize` before the model sees text; `apply` drops instruction-like proposals (`dropped_injected`); **J9**; lesson-level spec-28 guard. |

## Part E — Retrieval & context assembly

| # | Requirement | Status | Evidence |
|---|---|---|---|
| E1 | Pipeline: resolved request → embedding → vector → graph → filters → rerank → LLM | **VERIFIED (hybrid)** | Resolved request (Prompt 1 resolver) → `retrieval`: keyword-overlap + metadata (status/confidence/importance/recency scoring) over SQLite, vector recall over Chroma for past conversations, graph hits, lessons; per-memory embeddings deferred (keyword index + Chroma cover retrieval; noted as follow-up). |
| E2 | Only relevant memories injected | **VERIFIED** | **J10** (unrelated memories absent); `test_retrieval_relevant_only`; empty context for unrelated queries. |
| E3 | Context assembly template | **VERIFIED** | `core/assemble.py::assemble` — SYSTEM/REQUEST/STATE/MEMORIES/PEOPLE/RELATIONSHIPS/PROCEDURES/LESSONS/SKILLS/TASK STATE/TOOLS; empty sections omitted. |
| E4 | Token budget per section, configurable in Settings | **VERIFIED** | `MemorySettings.assembly_*` (11 budget fields, chars); greedy fill + line-wise trim; `budgets()` reads the live settings service. |
| E5 | Cloud: minimum context | **VERIFIED (N/A)** | No cloud model is enabled anywhere in config; the budget system caps context regardless. |
| E6 | Plug into `memory_augmented` profile | **VERIFIED** | arch2 pipeline (`pipeline.run`) feeds `retrieval.assemble_context` → `core.assemble.arch2_block` with state summary; profile switching exercised by `tests/test_context_regression.py` (T5) and the live UI smoke from Prompt 1. |

## Part F — Learning / feedback system

| # | Requirement | Status | Evidence |
|---|---|---|---|
| F1 | Interaction logging | **VERIFIED** | `learning.observe.turn` per exchange (+ mirror `interactions` table in memory DB for the memory dashboard); request, skill, decision, status, reward. |
| F2 | Task-level logging, failures at the exact stage | **VERIFIED** | `tasks`/`task_steps` (learning DB + memory DB) with `failure_stage`; multi-task tracking tested (`test_multitask_voice_session`, multitopic). |
| F3 | Feedback types + natural-language detection | **VERIFIED** | `feedback.KINDS` + `detect` (praise, rejection, corrections, remember/forget intents); scalar `reward_of`; **J6** cycle. |
| F4 | Error classification, never assume | **VERIFIED** | `errors.TAXONOMY` (all spec classes + extras), GATE regex (a bare "no" never becomes a lesson); **J6** classifies `wrong_application`. |
| F5 | Lessons store | **VERIFIED** | All spec fields + scope/kind/bucket; every confidence change writes `lesson_events` (auditable). |
| F6 | No over-generalization; scope widens only on repeated evidence | **VERIFIED** | **J5** temporary task-scoped lesson; `widen_scope` occasions guard; weak evidence can never supersede explicit lessons. |
| F7 | Temporary vs preference vs rule; precedence; injected never | **VERIFIED** | `PRIORITY` ordering in `guidance_for`; `learning.policy.temporary_instruction`; EXTERNAL_SOURCES refusal. |

## Part G — Offline training dataset

| # | Requirement | Status | Evidence |
|---|---|---|---|
| G1 | Training-example store | **VERIFIED** | `learning.dataset` table + memory-DB `training_examples` mirror; fields instruction/context/incorrect/correction/correct/reason/quality. |
| G2 | Memory and training data separate | **VERIFIED** | Separate DB files (`learning.db` vs `arch2_memory.db`); lessons ≠ facts ≠ dataset. |
| G3 | Offline pipeline script (manual trigger only) | **VERIFIED** | `orchestrator/finetune.py`: `dry-run` (data sufficiency, `curate_examples` quality/dedupe/contradiction/per-skill-cap, deps check, gate math — touches nothing), `train` (QLoRA), `eval` (fixed eval set), `promote` (eval-gated), `rollback`. **J14**. |
| G4 | Pipeline + eval harness + dataset export; training may be BLOCKED | **PARTIAL** | Eval harness + export + dry-run all work. Actual training **BLOCKED**: `peft`, `datasets`, `bitsandbytes` not installed (torch/transformers are) — `deps()` reports them; install or rent a GPU pod to train. `auto_deploy: false` is structural — promotion only via explicit `promote` after eval ≥ gate. |

## Part H — User memory commands + learning dashboard

| # | Requirement | Status | Evidence |
|---|---|---|---|
| H1 | Voice/text memory commands | **VERIFIED** | All spec phrasings + incognito + preferences + per-source learnings (`consolidate.handle_command`); wired at dispatcher 3c; `test_memory_commands`, `test_dispatcher_memory_commands`. |
| H2 | Forget = real deletion + derived cleanup + audit (log says it happened, not what) | **VERIFIED** | `memory.forget_hard` (memories + links + entity + rgraph nodes/edges word-boundary sweep + vector snippets), `forget_audit` rows (counts only), `wipe_all` audited; **J7**; dashboard forget requires `confirm: true`. |
| H3 | Incognito mode | **VERIFIED** | `core/incognito.py` (global toggle, `ISHTI_INCOGNITO`, per-session pin), dispatcher `_finish` writes nothing (chats, semantic, ftlog, consolidation, learning, monitor); memory commands refuse to store while on; **J8**. |
| H4 | Learning dashboard with graph view, conflicts, training examples + actions | **PARTIAL** | API complete: `/api/memory/people`, `/api/memory/graph`, `/api/memory/conflicts` (+ resolve), `/api/memory/people/{name}/confirm|forget`, `/api/memory/vault` (+ toggle), `/api/memory/backup`, `/api/memory/export`, `/api/memory/training-dry-run`, plus existing `/api/learning/*` (lessons edit/confirm/reject/delete, dataset add/delete/export). **Frontend tabs not yet added** to the Settings UI — endpoints ready, UI wiring pending (next step). |

## Part I — Safety rule

| # | Requirement | Status | Evidence |
|---|---|---|---|
| I1 | Memory never authorizes high-impact actions | **VERIFIED** | **J11**: `ConfirmationPolicy` (verb list + T2 tiers) is the only authority; arch2 pipeline's `_needs_confirmation` fires on high-impact verbs regardless of any stored "auto-send" memory; no code path reads a memory to skip a gate. |

## Part J — Required tests

| # | Test | Status |
|---|---|---|
| J1 | Ahmed brother edge, explicit, 1.0 | **VERIFIED** `test_graph_and_people` |
| J2 | Frequent contact → frequently_contacted, unknown | **VERIFIED** `test_graph_and_people` |
| J3 | Three Ahmeds separate; ambiguity asks | **VERIFIED** `test_graph_and_people` |
| J4 | Chrome→Firefox conflict record, newer wins, old superseded | **VERIFIED** `test_memory_contradictions` |
| J5 | "Use Chrome for this" scoped, not global | **VERIFIED** `test_memory_contradictions` |
| J6 | Wrong-app correction cycle → Chrome next time | **VERIFIED** `test_memory_contradictions` |
| J7 | Forget-everything-about-Ahmed real deletion + audit | **VERIFIED** `test_forget_and_safety` |
| J8 | Incognito → zero rows | **VERIFIED** `test_forget_and_safety` |
| J9 | Injected email instruction not stored/executed | **VERIFIED** `test_memory_contradictions` |
| J10 | Retrieval relevance (unrelated absent) | **VERIFIED** `test_forget_and_safety` |
| J11 | Memory never authorizes send | **VERIFIED** `test_forget_and_safety` |
| J12 | Decay marks needs_confirmation, never deletes | **VERIFIED** `test_memory_contradictions` |
| J13 | Family casual-tone WhatsApp flow, confirm before send | **VERIFIED** `test_forget_and_safety` |
| J14 | Training dry-run passes; no auto-deploy | **VERIFIED** `test_forget_and_safety` |

---

## Honest gaps (not silent)

1. **H4 frontend**: dashboard tabs (People/Graph/Conflicts/Training/Vault) exist as complete JSON APIs; the Settings-UI tab wiring is the one remaining piece of Prompt 2's DoD.
2. **A1 selectability**: Postgres/sqlite-vec/pgvector not installed → single-engine SQLite + Chroma today; switching logic deferred.
3. **E1 embeddings-per-memory**: keyword + Chroma hybrid now; a persistent vector index over memory rows is the natural next upgrade.
4. **G4 real training run**: needs `pip install peft datasets bitsandbytes` (or a rented pod); everything before the GPU step is verified.

## How to verify

```bash
PYTHONIOENCODING=utf-8 venv\Scripts\python -X utf8 -m pytest tests/          # 70 passed, 2 skipped
PYTHONIOENCODING=utf-8 venv\Scripts\python -X utf8 -m ishti.check            # core loop OK, 329/329
PYTHONIOENCODING=utf-8 venv\Scripts\python -X utf8 orchestrator\finetune.py dry-run
PYTHONIOENCODING=utf-8 venv\Scripts\python -X utf8 -m ishti.core.vault       # encryption self-check
```
