# Obscura Headless Browser Engine

ISHTI uses [Obscura](https://github.com/h4ckf0r0day/obscura) (v0.2.3, Apache-2.0) as its default headless web reader for fast, lightweight page scraping and markdown extraction without GPU overhead.

## Setup Instructions

1. Download the release binary for Windows from:
   `https://github.com/h4ckf0r0day/obscura/releases/tag/v0.2.3`
2. Extract the archive into this directory:
   - `tools/obscura/obscura.exe`
   - `tools/obscura/obscura-worker.exe`
3. Verify installation:
   ```bash
   tools/obscura/obscura.exe --version
   python -m ishti.obscura
   ```

*Note: Binaries are excluded from Git via `.gitignore` to keep the repository lightweight.*
