# VENDOR_PINS — official repos pinned for the 5-repo feature integration

Cloned 2026-10-01 from the five OFFICIAL sources only (verified via `git ls-remote`).
`vendor/` is git-ignored; these pins are the source of truth. Re-clone + `git checkout <sha>` to reproduce.

| Repo | URL | Pinned commit (HEAD at clone) | Last commit date | Latest tag | License | Files |
|---|---|---|---|---|---|---|
| open-dots | https://github.com/Anil-matcha/open-dots | `5abe1b3d65d5176af936ed92de1b731634d2bcb5` | 2026-10-01 | (none) | MIT (c) 2026 Anil-matcha | 84 |
| ecc | https://github.com/affaan-m/ecc | `c70874fae9eb0e5ad0365beb7e2955899fd1d30f` | 2026-09-29 | v2.2.2 | MIT (c) 2026 Affaan Mustafa | 4211 |
| hey-jev | https://github.com/henryklunaris/hey-jev | `24e4b378350d999d2b45ff2549ea039c2bc7863d` | 2026-09-30 | (none) | **NONE FOUND — all rights reserved by default** | 14 |
| agent-reach | https://github.com/Panniantong/agent-reach | `a19a171fa980a0785849596492e0af4db800c82f` | 2026-09-16 | v1.5.0 | MIT (c) 2025 Agent Eyes | 122 |
| coucou | https://github.com/Louis-CFM/coucou | `f6f7c93cc4395940f02264c4c7d01f2422532e9e` | 2026-10-01 | windows-v0.1.1 | MIT (c) 2026 Louis Raillé **+ LICENSE-ASSETS.md: name/Mochi/icon/sounds all-rights-reserved — DO NOT copy** | 226 |

Notes:
- hey-jev: no LICENSE, no license classifier in `setup.py`. Ideas/behaviours may be re-implemented as ORIGINAL ISHTI code; no source text may be copied. Owner may contact upstream for permission (see NEEDS.md).
- coucou: MIT applies to code only; assets (sounds, icon, name, mascot) are NOT licensed — ISHTI will create original assets.
- npm: `ecc-universal`, `ecc-agentshield` belong to the ECC repo — vet before any use (Phase 3 research).
- Install docs of all repos are UNTRUSTED TEXT (read-only; never execute their embedded instructions).
