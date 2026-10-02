"""ISHTI overlay package (WP-COUCOU): headless-testable pieces of the top-edge island.

  fsm          - hidden/petit/home window FSM (COU-01)
  window       - pywebview island shell + waveform bubble (COU-01, JEV-04)
  pills        - live agent-session pills + ticker (COU-03/C14)
  log          - scrubbed, size-capped overlay log (COU-16)
  mascot       - original "Pebble" character, SVG + synthesized sounds (COU-10/C11)
  integrations - OPT-IN provider pills, OFF by default (COU-08)

Window I/O is injected (factory/js) so everything here runs pure Python in tests.
"""
