# ISHTI Verified Audit Findings & Issues

## Severity Ranking
- **P0 / Blocker**: Immediate crash, uncaught exception, or severe data loss/hang risk.
- **P1 / High**: Feature failure or contract violation when invoked outside expected narrow parameters.
- **P2 / Medium**: Degradation in performance, timeout sensitivity, or missing fallbacks.
- **P3 / Low**: Cosmetic warning, uncalibrated confidence warning, or minor encoding warning.

---

### Issue 1: `automation.skill.run` crashes with TypeError on mismatched input
- **Severity**: P1 / High
- **Component**: `ishti/skills/automation/skill.py`
- **Location**: Line 20-21
- **Root Cause**:
  In `ishti/skills/automation/skill.py`:
  ```python
  m = RULE.match(text)
  cond = parse_condition(m["cond"])
  ```
  If `RULE.match(text)` returns `None` (for example, if routed by semantic similarity or direct invocation with text not starting with "when/if"), `m["cond"]` raises `TypeError: 'NoneType' object is not subscriptable`.
- **Reproduction**:
  ```python
  from ishti.skills.automation.skill import run
  run("unmatched prompt", {})
  # Raises: TypeError: 'NoneType' object is not subscriptable
  ```
- **Fix**: Check `if not m: return None` before indexing `m["cond"]`.

---

### Issue 2: `extract.skill.run` crashes with AttributeError on missing URL
- **Severity**: P1 / High
- **Component**: `ishti/skills/extract/skill.py`
- **Location**: Line 24
- **Root Cause**:
  ```python
  m = URL.search(text)
  url = m.group(0).rstrip(".,)")
  ```
  If `text` does not contain a regex-matched URL, `URL.search(text)` returns `None`, causing `m.group(0)` to raise `AttributeError: 'NoneType' object has no attribute 'group'`.
- **Reproduction**:
  ```python
  from ishti.skills.extract.skill import run
  run("extract product data", {})
  # Raises: AttributeError: 'NoneType' object has no attribute 'group'
  ```
- **Fix**: Guard with `if not m: return None`.

---

### Issue 3: `web_fetch.skill.run` crashes with AttributeError on missing URL
- **Severity**: P1 / High
- **Component**: `ishti/skills/web_fetch/skill.py`
- **Location**: Line 20
- **Root Cause**:
  ```python
  found = URL.search(text).group(0).rstrip(".,;")
  ```
  Calling `.group(0)` directly on `URL.search(text)` without checking for `None` causes an uncaught `AttributeError` when dispatched without a full URL in the query string.
- **Reproduction**:
  ```python
  from ishti.skills.web_fetch.skill import run
  run("fetch information about python", {})
  # Raises: AttributeError: 'NoneType' object has no attribute 'group'
  ```
- **Fix**: Guard with `m = URL.search(text); if not m: return None; found = m.group(0).rstrip(".,;")`.

---

### Issue 4: Console UnicodeEncodeError on Windows CP1252 codepage
- **Severity**: P2 / Medium
- **Component**: CLI / Logging / Console output
- **Location**: Multiple skills logging Unicode arrows (`->`) or em-dashes to stdout without stream reconfiguration.
- **Root Cause**:
  Windows console defaults to `cp1252` encoding. Printing characters like `\u2192` directly causes:
  `UnicodeEncodeError: 'charmap' codec can't encode character '\u2192' in position ...`
- **Reproduction**:
  Running scripts in standard Windows cmd/powershell without `sys.stdout.reconfigure(encoding='utf-8')` throws `UnicodeEncodeError`.
- **Fix**: Ensure `sys.stdout.reconfigure(encoding="utf-8")` is placed at entry points or replace non-ASCII characters in log/trace strings with ASCII equivalents (`->`).

---

### Issue 5: Laya Checkpoint Temperature Calibration Warning
- **Severity**: P3 / Low
- **Component**: `ishti/router.py` / `laya/router.py`
- **Location**: `laya/router.py:260`
- **Root Cause**:
  `RuntimeWarning: laya: this checkpoint ships invalid temperatures or values outside [0.5, 5]; using choice:11+=0.10058280825614929 -> 0.5. Treat confidence from the affected entries as uncalibrated.`
- **Impact**: Emits a noisy runtime warning to stderr on every router initialization. Functional routing still succeeds.
- **Fix**: Suppress or filter the specific `RuntimeWarning` from `laya.router` in `ishti/router.py`.