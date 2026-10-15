# docs/arch — subsystem deep-dives

| File | Subsystem | Key measured facts |
|---|---|---|
| [gpu-vram-governor.md](gpu-vram-governor.md) | VRAM governor + model residency | never accepts partial offload (size_vram==size verified); budget 80% of 16 GB; pinned planner+Laya; voice priority |
| [voice-pipeline.md](voice-pipeline.md) | wake → STT → dispatch → TTS | whisper turbo warm decode 167 ms; Piper sentence 373–426 ms; barge-in stop ≤200 ms; one STT thread (CTranslate2 constraint) |
| [boot-and-imports.md](boot-and-imports.md) | what the process imports, and what the optimization changed | skills_load 1174 → 358 ms (K1 lazy openai client, K2 lazy mcp hub) |

Boot overview: [architecture](../ARCHITECTURE.md). Startup and resource changes: [current audit fixes](../AUDIT_FIXES_2026-10-13.md).
