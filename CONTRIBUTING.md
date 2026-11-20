# Contributing to ISHTI

Thank you for helping. ISHTI is MIT-licensed (see [LICENSE](LICENSE)); contributions are accepted
under the same license.

## Where to talk

- **Bugs and feature requests:** open an [Issue](https://github.com/ishtiaq-ahmed-dev/ishti-ai/issues). Include your Windows
  version, whether you use Local or API mode, and the relevant lines from `%LOCALAPPDATA%\ISHTI\logs`. Never paste API keys
  or personal chats.
- **Questions, ideas, and skill proposals:** open a [Discussion](https://github.com/ishtiaq-ahmed-dev/ishti-ai/discussions).
- **Security problems:** do not open a public issue. Message the maintainer on GitHub (<https://github.com/ishtiaq-ahmed-dev>).
- **Maintainer:** Ishtiaq Ahmed ([@ishtiaq-ahmed-dev](https://github.com/ishtiaq-ahmed-dev)).

Look for issues labelled `good first issue` or `help wanted`. Every contributor is credited in the README with
[All Contributors](https://allcontributors.org): code, docs, tests, design, ideas and bug reports all count.

## Set up for development

1. Install Python 3.11 on Windows.
2. Create a virtual environment in the project folder: `python -m venv venv`, then `venv\Scripts\pip install -r requirements.txt`.
3. Run the offline tests (no model, no network):

   ```bat
   set ISHTI_TEST_MODE=1
   set ISHTI_SCRATCH=1
   venv\Scripts\python -m pytest -q
   ```

4. Lint: `venv\Scripts\ruff check ishti tests`.
5. Frontend (only if you change `interface/app`): `cd interface\app`, `npm install`, `npm run build`.

## Rules for changes

- Keep changes small and focused, one logical change per commit.
- Every new skill needs a `manifest.json` (name, description, tier, scope) and a test under `tests/skills/`.
- A skill that acts on the outside world (sends, deletes, spends, controls the PC) must be tier T2 and wait for approval.
- Do not add a network call, a cloud dependency, or a new third-party package without saying why in the pull request.
- Do not commit personal data, API keys, chat logs, or runtime databases. The `data/` folder and `owner_profile.yaml` are
  local-only.
- Tests must run offline with synthetic data.

## Pull requests

Open the pull request against `main`. Describe what changed and how you tested it. Keep the commit author your own name;
the project does not require any specific attribution line.
