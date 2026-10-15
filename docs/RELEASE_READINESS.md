# Release readiness and rollback

This working tree implements an initial engineering hardening pass. It is not a signed, commercially cleared or fully live-validated international release.

## Implemented mechanisms

- Writing no longer returns fixed citations, a fixed Titanic study, a fixed owner name or a fabricated PDF conversion. Missing source files are explicitly requested.
- Word-to-PDF conversion reopens the produced PDF before atomic no-overwrite publication. Microsoft Word remains a runtime dependency of that conversion; real Word execution requires separate validation.
- Cloud inference uses a grant for exactly the selected host/port, preserves the Internet-off rule, and disables HTTP redirects. Clients use explicit zero automatic retries rather than hidden repeated billable attempts.
- `install.bat --provider api` selects `requirements-api.txt` and skips Ollama/local model/voice downloads. The Windows launcher still bootstraps Python where absent; this is not a standalone signed installer.
- API-mode memory uses a versioned encrypted SQLite lexical index, separate from existing Ollama/Chroma vectors. API requirements exclude Chroma and its neural/index-service dependency chain. It requires no embedding-model download. It matches words/subwords and does not offer neural semantic equivalence.
- Native-script keyword retrieval/extraction and legitimate Chinese/Thai output are preserved. Keyword/graph retrieval no longer excludes all records after a fixed preselection cap. Large-store performance still needs benchmarking/index work.
- Tool plans retain up to 12 steps, execute at most three per continuation, persist completed results and explicit `{{step:1}}` bindings, and retain steps through approvals. Journal payloads are authenticated/encrypted with a separate OS-keystore key; missing keys fail closed. Test keys stay in scratch storage. Say `continue workflow` or `cancel workflow`. An interrupted executing action becomes unknown and is never automatically replayed.
- Background learning/diagnostic work uses two workers and a 32-job queue. Overflow and incomplete shutdown are observable. This queue is best-effort, not a durable action engine.
- Call accounting includes request IDs, deadlines, usage where the provider supplies it and evolving background totals. Missing token usage is unknown. Diagnostics retain no raw prompt. `/api/diagnostics/calls` exposes local metadata.
- Setup can use the OS credential store. Keys are never saved to JSON, browser storage or diagnostics. The configured environment variable remains supported.
- Optional UI sections load on demand; existing precompression and immutable asset caching remain intact.

## Reversible settings

| Setting | Default | Purpose / rollback |
|---|---|---|
| `ISHTI_BOUNDED_BACKGROUND` | `1` | `0` restores legacy post-turn threads for diagnosis; not recommended for release |
| `ISHTI_MIGRATE_PLAINTEXT_WORKFLOWS` | off | `1` temporarily permits importing the earlier unsealed workflow format; leave off outside a reviewed migration |
| `ISHTI_TURN_TIMEOUT_S` | `90` | Set an explicit 1–600 second foreground budget; client/read deadlines remain subject to transport behavior |
| `ISHTI_LEGACY_SCRIPT_FILTER` | off | `1` restores destructive legacy script cleanup for diagnosis; do not enable for multilingual release |
| `memory.embedding_backend` | provider-derived | Set `ollama` or `lexical` explicitly; each uses a separate store. Returning to Ollama does not automatically merge/reindex lexical history |
| `ISHTI_INTEL_SAFE_FILE_OUTPUTS` | existing default on | Existing document publication flag; new Word-to-PDF publication always validates and avoids replacing owner files |

Do not roll back a workflow by automatically replaying an uncertain remote action. Cancel/inspect/reconcile first. Back up journals and keys before migrations. Incognito workflows remain in memory and are not persisted.

## Required outstanding gates

1. Fresh full offline regression on the final working tree, plus three consecutive focused/full passes after the last relevant edit; build and browser results separately.
2. Clean Windows API and local installations on declared hardware, real provider contracts, actual Word/PDF output review, multilingual voice/RTL/accessibility and soak measurements.
3. Complete all-store encryption/retention/deletion/export/restore and parser OS isolation. Uploads/chats/learning/state are not all encrypted by this pass; optional memory encryption is not a blanket privacy guarantee.
4. Commercial terms and rights clearance for code, Rare UI components, fonts, models and voices. Do not reinterpret third-party redistribution restrictions as permission.
5. Signed/versioned installer and authenticated update/rollback pipeline, payments/refunds/tax review, selected-market privacy/AI disclosures and independent security review.
6. Actual customer discovery and a four-week overseas pilot. No conversion, retention, willingness-to-pay or profitability result has been measured.

CI runs only its named curated offline core and frontend build. A generated `tools/release_inventory.py` report supplies source fingerprints and package/license inputs; it is not commercial or security approval. No real messages, trades or purchases are part of these regression checks.

Two generated reports are the evidence form for gate 1, and neither replaces the gate:

- `venv\Scripts\python tools\evidence.py --markdown docs\EVIDENCE.md` runs the curated offline set, the
  offline eval suites and the per-skill suites, and writes one report in which every metric is either
  measured with a numerator and denominator or `unmeasured` with a stated reason. It covers the offline
  suite and the ~120 per-skill suites that `pytest.ini` excludes from normal collection. It does not
  cover the live backend, voice, the 300-task suite or anything on hardware other than the author's PC.
- `venv\Scripts\python tools\privacy_report.py --markdown docs\PRIVACY_REPORT.md` audits egress for the
  revision in the tree. It is not an independent security review (gate 5), and the encryption,
  retention, deletion and export work in gate 3 is untouched by it.

Anthropic's compatibility endpoint omits sampling overrides that current Claude models reject. An optional workspace is read from `llm.api.workspace_id` or `ISHTI_ANTHROPIC_WORKSPACE`; it is sent only to the selected Anthropic endpoint. This remains a compatibility adapter, not qualification of the native Claude API or every model. [Anthropic compatibility documentation](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk).
