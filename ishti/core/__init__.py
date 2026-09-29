"""ISHTI core: the Prompt-1 layer every architecture composes from.

state        - ConversationStateManager: per-chat topic/task/entities/searches (D1)
resolver     - resolves follow-ups into standalone requests before routing (D2)
context      - context-window assembly with topic anchoring (D4)
pipeline     - stage interfaces + trace events for every stage (B1-B7)
architectures- profile registry: 6 switchable architectures + health checks (C1-C3)
settings     - typed settings schema, storage, import/export (C4)
confirmation - config-driven confirmation policy (E3, used by the tool stage)
logutil      - structured JSON logs with secret/PII redaction (E1)

Everything here is importable without Ollama, a GPU or the network, so the full
test suite runs fast and deterministic (mock LLM / mock tools in tests/mocks.py).
"""
