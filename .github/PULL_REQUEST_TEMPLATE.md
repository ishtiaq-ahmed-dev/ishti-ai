## What this changes

<!-- One or two sentences. Link the issue: "Fixes #123". -->

## How I checked it

- [ ] `venv\Scripts\python -m pytest -q` passes (offline, with `ISHTI_TEST_MODE=1` and `ISHTI_SCRATCH=1`)
- [ ] I added or updated a test for the behaviour I changed
- [ ] Anything that sends, deletes, pays or installs still asks the owner first
- [ ] No API keys, phone numbers, personal chats or real names in code, tests or screenshots
- [ ] For a UI change: screenshot attached, and `interface/app` builds (`npm run build`)
