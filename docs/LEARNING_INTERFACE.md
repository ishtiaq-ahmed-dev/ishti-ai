# LEARNING_INTERFACE — frozen contract for the reinforcement-like learning system

This file is the contract between the learning core (`ishti/learning/`), the dispatcher hooks, the
`learning` skill, the HTTP layer (`interface/web_ui/server.py`) and the dashboard page
(`interface/app/src/sections/Learning.tsx`). It is written before the code so every workstream can
build in parallel against fixed shapes. Nothing here changes LLM weights; every effect is
store-based (memory / lessons / dataset) and reversible from the dashboard.

## 1. Data model (SQLite `data/learning.db`, see `ishti/learning/store.py`)

| Table | Purpose | Spec section |
|---|---|---|
| `interactions` | one row per agent turn: request, decision, memories, tool result, feedback, reward | 4 |
| `tasks` | one row per multi-step task with per-step results and final status | 17, 18 |
| `feedback` | structured feedback rows (kind, polarity, error_type, incorrect/correct) | 5, 6, 7 |
| `mistakes` | error-classified failures, `signature` groups repeats | 7, 10 |
| `lessons` | the lesson store (kind/scope/confidence/status/action/expiry) | 8, 9, 11, 12, 13 |
| `lesson_events` | evidence trail for every confidence change (auditable, undoable) | 11, 29 |
| `candidates` | patterns awaiting owner confirmation ("Should I remember that?") | 10, 23 |
| `workflows` | recurring successful sequences (candidate procedures) | 19 |
| `entities` / `entity_mentions` | separate people/entities with context, never merged by name | 25, 26 |
| `dataset` | curated offline training examples (separate from memory) | 20, 21, 22 |

## 2. Module API (`ishti/learning/`)

```
store.py      cfg() db_path() set_db_path(p) conn() reset() rows() row() add() ensure() now()
              enabled() proactive() thresholds() evidence()      # config.json + runtime override
              setting(key, default) set_setting(key, value)
observe.py    begin(text, sid, channel, dec, memories=None, task_id=None, step_no=None) -> interaction_id
              end(iid, reply=None, skill=None, status="ok", result=None, reward=None) -> dict
              task_begin(goal, domain="", source="") -> task_id
              task_step(task_id, step, tool, result, success, note="") -> None
              task_end(task_id, status, correction="", lessons=None, reward=None) -> None
              turn(...)   # one-shot convenience used by the dispatcher
              recent(limit=50) get(iid)
feedback.py   KINDS  detect(text, last_request="", last_skill="", pending=None) -> dict|None
              from_correction(text, wrong, right, session=None) -> dict      # "use web_search for that"
              apply(fb, interaction=None, session=None) -> dict{effects:[...], lesson, ask}
              reward_of(status, fb=None) -> float                            # spec 16 table
errors.py     TAXONOMY  classify(text, decision=None, result=None, history=None) -> dict
              signature(error_type, wrong, right, domain="") -> str
              analyze_failure(...) -> dict|None                              # spec 14
              repeated(min_count=None) -> list[{error_type, wrong, right, count, signature, domain}]
lessons.py    add(kind, statement, ...) -> dict
              adjust(lesson_id, delta, reason, interaction_id=None, evidence=None)
              attribute(lesson_ids, reward, interaction_id=None, reason="") -> list[lesson_id]
              apply_turn_outcome(interaction_id, reward, applied_lesson_ids=None, reason="") -> list
              decay(days=None, floor=0.05) -> int           # AdMem pruning (un-confirmed only)
              maybe_decay(force=False) -> int               # decay() at most once per interval
              confirm(lesson_id) reject(lesson_id) forget(lesson_id) delete(lesson_id) edit(lesson_id, **fields)
              active(context=None, kind=None, entity=None) -> list[dict]
              guidance_for(text, domain=None, limit=4) -> list[dict]         # priority ordered, spec 13/27
              context_for(text, domain=None, limit=4) -> str                 # the prompt block
              CONTEXT_INJECTING_SKILLS: frozenset          # skills whose prompt includes context_for()
              bucket(confidence) -> str  thresholds()
              detect_patterns(min_count=None) -> list[candidates]
              accept_candidate(candidate_id, keep, edits=None) -> dict
              stats() export(path=None) -> (path, n)
reflect.py    reflect(interaction=None, reward=None, feedback=None, lesson=None) -> str
              remember(text, interaction_id="", kind="reflection", context="", confidence=None) -> dict
              for_turn(interaction, reward=None, feedback=None, keep=True) -> {text, lesson}
              mine_insights(limit=50, min_words=4) -> list[{statement, context, provenance}]   # ExpeL
              promote_insights(limit=50, confidence=0.3) -> list[lesson]                        # ExpeL
policy.py     check(text, decision=None, session=None) -> {"overrides":[...], "skill":None|str, "why":str, "ask":str|None}
              apply_overrides(text, args) -> dict                            # parameter-level (application/entity)
              temporary_instruction(text) -> dict|None
entities.py   observe(name, org="", email="", phone="", relationship="", source="owner", ...) -> dict
              candidates(name, context="") -> list[{entity, org, relationship, score, why}]
              resolve(name, context="") -> {"selected", "confidence", "ambiguous", "question", "candidates"}
              note_contact(entity_key, channel="", detail="") -> dict        # frequency only, never invents a relationship
              list_people() merge(keep_key, drop_key, confirm=True) -> dict
selfeval.py   evaluate(action, args=None, text="", session=None) -> {"confident","score","checks","reversible","requires_confirmation","question"}
workflows.py  record(task_id) scan(min_count=None) -> list[candidates] suggest() -> str|None
              list_workflows() set_status(id, status)
dataset.py    add_example(instruction, correct="", ...) -> dict
              from_feedback(fb, interaction=None) -> dict|None
              curate(rows) -> (kept, skipped_counts) export(path=None, min_quality=None) -> (path, n)
              stats() rows(limit=200) delete(id)
dashboard.py  overview() lessons(...) update_lesson(id, **f) delete_lesson(id) candidates(status) answer_candidate(id, keep, edits)
              feedback_list(limit) mistakes(limit) interactions(limit) tasks(limit) people()
              reflections(limit=100) -> {reflections[], mined[], counts{reflections,insights,pending}}
              promote_insights(limit=50) -> {promoted[]}
              workflows() dataset(limit) add_dataset(payload) delete_dataset(id) sources()
              settings() update_settings(**kw) export(what) ack_and_ask()   # HITL question slot
```

Rule for every module: **fail soft**. A learning write must never break a reply. All public
functions catch their own exceptions and return empty/falsey values, and every module ends with an
`if __name__ == "__main__":` self-check printing `[self-check] <module> OK - ...`.


## 3. Config (`ishti/config.json` → `learning`)

```json
{"enabled": true, "db_path": "D:\\ISHTI\\data\\learning.db", "proactive": false,
 "thresholds": {"weak": 0.3, "possible": 0.6, "probable": 0.8, "strong": 0.95},
 "evidence": {"similar_request": 2, "widen_scope": 3, "candidate_rule": 3, "workflow": 3},
 "temporary_ttl_s": 3600, "entities": {"ambiguity_margin": 0.2, "min_confidence": 0.6},
 "dataset": {"min_quality": 0.6, "per_skill_cap": 0.4, "export_dir": "D:\\ISHTI\\data\\finetune"},
 "decay_days": 30, "decay_interval_s": 86400,
 "export_dir": "D:\\ISHTI\\data\\learning\\export"}
```

`decay_days` is the horizon after which an un-confirmed, un-re-earned lesson starts fading;
`decay_interval_s` is how often `maybe_decay()` is allowed to run (`lessons.py`, called from the
dispatcher's turn hook). Both are read through `store.cfg()`, i.e. they are **config-file** knobs
in `ishti/config.json` - `POST /api/learning/settings` only whitelists `enabled`, `proactive`,
`thresholds` and `evidence`.

Note `decay()` moves the `decayed` watermark, never `updated` - `updated` means "evidence touched
this" and the dashboard reads it.

Runtime overrides (`enabled`, `proactive`, `thresholds`, `evidence`) are written to the `settings`
table by `dashboard.update_settings()` and read back by `store.enabled()`, `store.proactive()`,
`store.thresholds()` and `store.evidence()`. They are **not** read inside `store.cfg()`:
`cfg()` feeds `db_path()` -> `conn()`, so a `cfg()` that queried the settings table would need the
database in order to locate the database. A stored-but-never-read setting is the bug this split
exists to prevent - it made the Enabled toggle and every threshold edit decorative.

`enabled: false` disables all learning writes (reads still work). `proactive: false` means the
assistant never volunteers a learned suggestion or a "should I remember that?" question unless the
owner asks (flipped in the dashboard or with "start learning proactively").

## 4. HTTP API (`interface/web_ui/server.py`, same-origin, token-guarded by the existing middleware)

| Method & path | Body / query | Response |
|---|---|---|
| GET `/api/learning/overview` | – | `{enabled, db, counts{lessons,active,candidates_open,feedback,mistakes,interactions,tasks,workflows,dataset}, loops{memory,skill,learning}, confidence_buckets, thresholds, settings{enabled,proactive}, recent[]}` |
| GET `/api/learning/lessons` | `?status=&kind=&q=&limit=200` | `{lessons:[Lesson]}` |
| POST `/api/learning/lessons/{id}` | `{status?, statement?, confidence?, scope?, context?, note?}` | `{lesson, changed:[..]}` |
| POST `/api/learning/lessons/{id}/confirm` | – | `{lesson, action:"confirmed"}` |
| POST `/api/learning/lessons/{id}/reject` | – | `{lesson, action:"rejected"}` |
| DELETE `/api/learning/lessons/{id}?forget=1` | – | `{deleted:true, memory_forgotten:bool}` |
| GET `/api/learning/candidates` | `?status=open` | `{candidates:[{id,ts,kind,signature,question,status,source,payload}]}` |
| POST `/api/learning/candidates/{id}` | `{keep:true, edits?}` | `{candidate, lesson\|null}` |
| GET `/api/learning/feedback` | `?limit=100` | `{feedback:[{id,ts,kind,polarity,error_type,target,incorrect,correct,message,source,confidence,applied}]}` |
| GET `/api/learning/mistakes` | `?limit=200` | `{mistakes:[{error_type,wrong,right,count,signature,domain,last_ts}]}` |
| GET `/api/learning/interactions` | `?limit=50` | `{interactions:[{id,ts,request,skill,status,reward,domain,task_id,step_no}]}` |
| GET `/api/learning/tasks` | `?limit=50` | `{tasks:[{task_id,ts,goal,final_status,reward,steps[],correction,lessons[]}]}` |
| GET `/api/learning/workflows` | – | `{workflows:[{id,name,steps[],count,confidence,status,last_seen}]}` |
| POST `/api/learning/workflows/{id}` | `{status:"active"\|"retired"}` | `{workflow}` |
| GET `/api/learning/people` | – | `{people:[{entity,org,relationship,email,phone,confidence,mentions,last_seen,aliases[]}]}` |
| GET `/api/learning/sources` | – | `{sources:[{source,lessons,last_ts}]}` |
| GET `/api/learning/dataset` | `?limit=200` | `{dataset:[Row], stats{total,by_quality,by_skill,exported}}` |
| POST `/api/learning/dataset` | `{instruction, correct?, reason?, quality?, skill?}` | `{example}` |
| DELETE `/api/learning/dataset/{id}` | – | `{deleted:true}` |
| POST `/api/learning/dataset/export` | – | `{path, count, skipped{}}` |
| POST `/api/learning/export` | `{what:"lessons"\|"feedback"\|"mistakes"\|"dataset"\|"all"}` | `{path,count}` |
| GET `/api/learning/settings` | – | `{settings, thresholds, evidence}` |
| POST `/api/learning/settings` | `{enabled?, proactive?, thresholds?, evidence?}` | `{settings, thresholds, evidence}` |
| GET `/api/learning/ask` | – | `{question, candidate_id}` or `{question:null}` |
| GET `/api/learning/reflections` | `?limit=100` | `{reflections:[{lesson_id,statement,context,note,confidence,bucket,status,updated,kind_of:"reflection"\|"insight"}], mined:[{statement,context,provenance{interaction_id,request,skill,ts}}], counts{reflections,insights,pending}}` |
| POST `/api/learning/reflections/mine` | `{limit?}` | `{promoted:[Lesson], error?}` |

`Lesson` = `{lesson_id, created, updated, kind, scope, context, statement, incorrect, correct,
evidence, positive, negative, confidence, bucket, status, source, priority, action, expires, entity,
occasions, confirmed, note, decayed}` (`bucket` is one of weak/possible/probable/strong/confirmed;
`decayed` is the last timestamp `decay()` faded this lesson and is `null` until it has faded once -
`updated` stays the evidence time and is never touched by decay).

## 5. Dashboard page

`interface/app/src/sections/Learning.tsx` exported as `Learning({ onToggleNav })`, registered in
`App.tsx` NAV between `memory` and `skills` with key `learning`, label `Learning`. Uses `Page`,
`getJSON`/`postJSON`/`del` from the existing libs and the same Tailwind design tokens as the Memory
page. Panels: Overview (three loops + confidence buckets + reward summary), Lessons
(confirm/reject/pause/delete/edit + confidence bar + source + evidence), Reflections (the verbal
one-per-sentence records plus mined-but-unpromoted insights, with a **Mine now** action),
Candidates (answer yes/no), Corrections & feedback, Repeated mistakes, People, Workflows, Training
data (stats + export + delete), Settings (enabled, proactive, thresholds, evidence minimums).
Tab keys are `overview | lessons | reflections | candidates | feedback | mistakes | people |
workflows | training | settings`.

## 6. Verification gates (must pass before "done")

1. `python -m ishti.learning.<module>` — self-check per module.
2. `python -m ishti.skills.learning.skill` — skill self-check.
3. `tests/test_learning.py` — full cycle: correction → lesson → policy; repeated mistakes →
   candidate rule; HITL confirm/reject; entity separation; self-eval ambiguity; workflow mining;
   dataset curation (dedupe/contradiction/privacy); reward table; temporary-instruction expiry.
4. `tests/test_learning_wiring.py` — the cross-module loop: lessons reach `chat.build_context`,
   the dispatcher turn hook runs, attribution is gated on `CONTEXT_INJECTING_SKILLS`, the
   reflections payload, supersede + reflection recall, the confidence floor, `maybe_decay()`
   throttling without touching `updated`, and `promote_insights()`.
5. `python -m ishti.check` — routing eval (labelled set) + core loop, unchanged or better.
6. `python tools/check_skills.py` — the new skill's manifest matches its behaviour, no matcher steals.
7. `python tools/verify_all.py --quick --only learning` then the full quick pass.
8. `cd interface/app && npm run build` — TypeScript + Vite build clean.
9. Live check: start the server, GET/POST each `/api/learning/*` endpoint against a temp DB.
