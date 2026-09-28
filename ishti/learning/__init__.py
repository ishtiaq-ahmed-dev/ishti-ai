"""Reinforcement-like learning for ISHTI (see docs/LEARNING_SYSTEM.md).

The loop this package implements (never touching model weights at runtime):

    interaction -> action -> result -> user feedback -> learning signal
        -> memory / lesson / dataset update -> better future decisions

Three separate feedback loops share these stores:

  * memory loop  - "what do I know about my user?"   (lessons + entities + facts)
  * skill loop   - "which capability should I use?"  (policy overrides + routing hints)
  * learning loop- "did my decision actually work?"  (reward, mistakes, dataset)

Offline only: accumulated high-quality examples are exported (dataset.py) for the
LoRA/QLoRA job in orchestrator/finetune.py, which deploys nothing without passing
its eval gate. Normal operation changes stores, never weights.

Modules are deliberately fail-soft: a learning write must never break a reply, and
every module can be run as a self-check (`python -m ishti.learning.<module>`).
"""
