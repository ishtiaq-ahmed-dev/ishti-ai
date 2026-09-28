"""ARCH2: the user's two-pass architecture (spec 2026-09-28).

Pipeline: STT -> intent router -> qwen3-14B (think / plan / call tools / converse)
-> tools (browser, PC, email, ...) -> validation -> qwen3-14B final -> TTS.
On top of it runs the four-layer memory system (memory.py): semantic, episodic,
procedural memories + the relationship graph, with observations that promote to
beliefs only after configurable confidence thresholds.

ARCH1 is the existing dispatcher (ishti/dispatcher.py): reflex -> rules -> Laya
-> skills. The active one is chosen in ishti/config.json ("arch"."active"), from
the dashboard (Settings -> Architecture), or by asking ISHTI ("use arch 2").
ishti/arch2/registry.py is the single owner of that state.
"""
