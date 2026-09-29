# CHANGELOG

## 2026-09-29 — Prompt 4 verification pass

### Fixed
- **Test suite was unrunnable**: `tests/test_lifecycle.py` (standalone script) called
  `sys.exit(0)` at import → pytest `INTERNALERROR` at collection. Excluded from collection
  via `tests/conftest.py`; the script still runs directly as designed.
- **Live e2e contract tests polluted the offline run**: `pytest.ini` now defaults to
  `-m "not e2e"`; run them explicitly with `-m e2e`.
- **STT latency gate false-failed under GPU contention**: whisper's documented CPU int8
  fallback (busy VRAM budget) tripped the absolute 3 s assert. Budgets are now device-aware
  (CUDA 3 s EN / 6 s UR; CPU fallback bounded 15 s / 10 s); accuracy asserts unchanged.
- **Wake echo self-test race**: GPU-busy echo-decision transcribe exceeded the 20 s wait →
  `ishti/wake.py` now waits up to 60 s for the verdict.
- **"What do you remember about me" omitted the owner's identity**: the arch2 consolidate
  `WHAT_RE` branch read only the last 12 memory rows and never the owner profile.
  It now prepends name/city/role from the profile (graceful fallback if unavailable);
  live e2e test B passes ("You are Ishtiaq Ahmed, in Peshawar - AI student.").

### Docs
- docs/TESTING.md (how to run every test type, incl. the new defaults),
  docs/TASKS.md, docs/PROGRESS.md, docs/REQUIREMENTS_MATRIX_PROMPT4.md,
  docs/FINAL_REPORT.md.

### Notes
- No behavior changes outside the fixes above; other threads' concurrent work (arch2,
  VRAM governor, PyInstaller packaging) was left untouched. Nothing committed.
