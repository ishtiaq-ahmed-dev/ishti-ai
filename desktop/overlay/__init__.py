"""ISHTI overlay package (WP-COUCOU): headless-testable pieces of the top-edge island.

  fsm    - hidden/petit/home window FSM (COU-01)
  pills  - live agent-session pills + ticker (COU-03/C14)
  log    - scrubbed, size-capped overlay log (COU-16)

The pywebview window that renders these is wired separately; everything here is
pure Python so it runs in tests without a GUI.
"""
