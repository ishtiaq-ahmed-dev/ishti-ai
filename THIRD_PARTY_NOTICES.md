# THIRD_PARTY_NOTICES — vendored/reused material from the 5-repo integration

Pins and licenses: `docs/VENDOR_PINS.md` (clone date 2026-10-01). `vendor/` is git-ignored; nothing from
these repos is redistributed by ISHTI except where explicitly stated below (nothing is).

## 1. open-dots — MIT, (c) 2026 Anil-matcha
Upstream: https://github.com/Anil-matcha/open-dots @ 5abe1b3d65d5176af936ed92de1b731634d2bcb5
Use: design reference for approval-gateway receipts, workspace confinement, governed `/search`,
auth/session posture, redaction. ISHTI implementations are original code; no open-dots source text
is copied. MIT license text preserved in `vendor/open-dots/LICENSE` (read-only pin).

## 2. ECC — MIT, (c) 2026 Affaan Mustafa
Upstream: https://github.com/affaan-m/ecc @ c70874fae9eb0e5ad0365beb7e2955899fd1d30f (tag v2.2.2)
Use: design reference for agent roster/roles, quality gate, harness audit, model routing, session
schema, doctor/repair, security-scan rule ideas. Rule *ideas* are re-derived and re-written for
ISHTI's own surfaces (skill manifests, MCP configs); no AgentShield rule text and no npm package
(`ecc-universal`, `ecc-agentshield`) are installed or vendored. License text in `vendor/ecc/LICENSE`.
Supply-chain note: ECC itself warns to install only from its official channels — ISHTI installs nothing.

## 3. hey-jev — NO LICENSE (all rights reserved by default)
Upstream: https://github.com/henryklunaris/hey-jev @ 24e4b378350d999d2b45ff2549ea039c2bc7863d
Use: **behaviour reference only** (wake/PTT/dictation/compound/timers/vocabulary flows). No source text,
assets, or names are copied. Owner outreach for a license was tracked in the retired `NEEDS.md` (N36);
that record is no longer in this tree, so it is not re-verifiable here — carry it into the commercial
review in [docs/COMMERCIAL_READINESS.md](docs/COMMERCIAL_READINESS.md).

## 4. agent-reach — MIT, (c) 2025 Agent Eyes
Upstream: https://github.com/Panniantong/agent-reach @ a19a171fa980a0785849596492e0af4db800c82f (tag v1.5.0)
Use: design reference for the channel/backend registry, doctor probes, transcribe SSRF guards,
safe-by-default install, skill trigger aliases. ISHTI's `internet_reach` skill is original code that
calls the same *class* of upstream tools (yt-dlp, gh, feedparser, Jina Reader) per channel; those
upstream tools keep their own licenses and are only invoked, never vendored. License text in
`vendor/agent-reach/LICENSE`.

## 5. coucou — MIT code, (c) 2026 Louis Raillé; ASSETS all-rights-reserved
Upstream: https://github.com/Louis-CFM/coucou @ f6f7c93cc4395940f02264c4c7d01f2422532e9e
Use: design reference for the overlay FSM, hook relay budgets, named-pipe pattern, diff-and-backup
installer discipline, pill lifecycle, log scrubbing. ISHTI's overlay code is original Python/TS;
**the name "Coucou", the Mochi character, icons, the 28 sounds, and media are NOT used or imitated**
(`LICENSE-ASSETS.md`): ISHTI ships its own original mascot and sounds. License text in
`vendor/coucou/LICENSE`; asset restrictions in `vendor/coucou/LICENSE-ASSETS.md`.

## Third-party tools invoked (not vendored) by planned channels
yt-dlp, gh CLI, feedparser, Jina Reader (r.jina.ai), bili-cli/OpenCLI/twitter-cli/rdt-cli (OPT-IN
login-state backends) — each keeps its own license; ISHTI only shells out to locally installed
binaries after a doctor probe, and none is auto-installed (installs are T2-gated).

## Implementation addendum (2026-10-02, WP-OD/ECC/JEV/COUCOU builds)

- All five repos remain design references ONLY. No source text, assets, names, rule text or config
  is copied or redistributed; `vendor/` stays a read-only pin.
- The overlay mascot, its SVG art and its chime are ISHTI originals (`desktop/overlay/mascot.py`,
  marker `data-ishti-mascot="pebble-v1"`); coucou's Mochi character, icons and 28 sounds are NOT
  imitated (enforced by assertions in `tests/test_overlay_mascot.py`).
- `orchestrator/quality_gate.py` uses the ruff already present in the project venv; no npm package
  (`ecc-universal`, `ecc-agentshield`, hookify) is installed or invoked.
- hey-jev has no license: only generic behaviour patterns (PTT, dictation chunking, duration
  phrasing) were re-implemented from the public README/description; owner outreach was tracked in the
  now-retired `NEEDS.md` N36 (see section 3 above).
- Local runtimes already owned by ISHTI remain the only STT/TTS/LLM: faster-whisper, Fish Speech
  (if enabled) and Ollama. No cloud STT/LLM/TTS call exists in any new module (gateway refuses
  non-loopback endpoints outright).

---

# Bundled third-party material used at runtime (merged 2026-10-02 from the former `md files/THIRD_PARTY_NOTICES.md`)

The components below ARE shipped or used by ISHTI (unlike the design-reference repos above). This section was merged on 2026-10-02 from a former `md files/THIRD_PARTY_NOTICES.md`; that source copy and its `docs/build-history/` archive have both been retired, so this section is now the record.

## R1. Rare UI (https://rareui.com)
Components in `interface/app/src/components/rare/` are from Rare UI (https://github.com/swamimalode07/rare-ui). They are used unmodified, except for small adaptations (Next.js Link replaced with a button), under this license:

```
MIT + Commons Clause License Condition v1.0 + Attribution

Copyright (c) 2026 Swami Malode

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, and distribute the Software as part of an
application, website, or product, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

Attribution Requirement

Any project that ships any part of the Software must credit Rare UI with a
visible link to https://rareui.com, placed where a visitor or user can find it,
such as a site footer, an about page, a credits screen or a README. The credit
and the copyright notice above must not be removed from the source you copied.

Commons Clause Restriction

You may use this Software, including for any commercial purpose, so long as you
do not sell, sublicense, or redistribute the components themselves, whether
alone, in a bundle, or as a ported version.

No Warranty

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## R2. Chatterbox (https://github.com/resemble-ai/chatterbox)

Used as a library (the voice-conversion half, `chatterbox.vc`) to turn ISHTI's speech into the owner's own voice for
voice notes he sends; weights `ResembleAI/chatterbox` (s3gen.safetensors, conds.pt) in `data/clone`. MIT License,
Copyright (c) 2025 Resemble AI. Its package pins torch 2.6, which can't run on this GPU (RTX 5070 Ti, sm_120), so it is
installed with `pip install --no-deps chatterbox-tts` plus librosa, s3tokenizer, diffusers, resemble-perth,
conformer==0.3.2, omegaconf and einops; ISHTI keeps torch 2.11+cu128. Output carries Resemble's Perth watermark.
