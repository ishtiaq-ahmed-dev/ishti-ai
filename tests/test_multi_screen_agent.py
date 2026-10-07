"""
Unit and Integration Tests for ISHTI Multi-Screen OS Automation Agent.
Verifies:
1. DisplayManager hardware monitor detection and global coordinate mapping.
2. VisualCaptureEngine stitched multi-screen frame creation and cursor overlay.
3. ActionExecutor execution of all schema actions (move_to, click, double_click, drag_to, type, press_key, wait, done).
4. MultiScreenOSAgent continuous Perceive-Plan-Act loop, system prompt formatting, and multi-monitor dragging.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from ishti.automation.multi_screen_agent import (
    DisplayManager,
    VisualCaptureEngine,
    ActionExecutor,
    MultiScreenOSAgent,
    ActionCommand,
    MonitorInfo,
    get_multi_screen_agent,
)


class TestDisplayManager(unittest.TestCase):
    def setUp(self):
        self.dm = DisplayManager()

    def test_monitors_detected(self):
        self.assertGreaterEqual(len(self.dm.monitors), 1)
        self.assertGreater(self.dm.virtual_width, 0)
        self.assertGreater(self.dm.virtual_height, 0)

    def test_which_screen_mapping(self):
        # First monitor
        m1 = self.dm.monitors[0]
        screen1 = self.dm.which_screen(m1.left + 50, m1.top + 50)
        self.assertEqual(screen1, m1.id)

        # Second monitor if present
        if len(self.dm.monitors) > 1:
            m2 = self.dm.monitors[1]
            screen2 = self.dm.which_screen(m2.left + 50, m2.top + 50)
            self.assertEqual(screen2, m2.id)

    def test_clamp_coordinates(self):
        cx, cy = self.dm.clamp(-100, -50)
        self.assertEqual(cx, self.dm.virtual_left)
        self.assertEqual(cy, self.dm.virtual_top)

        max_x = self.dm.virtual_left + self.dm.virtual_width - 1
        max_y = self.dm.virtual_top + self.dm.virtual_height - 1
        cx, cy = self.dm.clamp(999999, 888888)
        self.assertEqual(cx, max_x)
        self.assertEqual(cy, max_y)

    def test_environment_config_text(self):
        txt = self.dm.get_environment_config_text()
        self.assertIn("### ENVIRONMENT CONFIGURATION", txt)
        self.assertIn("Total Combined Resolution:", txt)
        self.assertIn("Screen 1", txt)


class TestVisualCaptureEngine(unittest.TestCase):
    def setUp(self):
        self.dm = DisplayManager()
        self.vce = VisualCaptureEngine(self.dm)

    def test_cursor_position_query(self):
        pos = self.vce.get_cursor_position()
        self.assertIsInstance(pos, tuple)
        self.assertEqual(len(pos), 2)
        self.assertIsInstance(pos[0], int)
        self.assertIsInstance(pos[1], int)

    def test_stitched_frame_generation(self):
        img, (cx, cy), out_path = self.vce.capture_stitched(highlight_cursor=True)
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (self.dm.virtual_width, self.dm.virtual_height))
        self.assertTrue(os.path.isfile(out_path))


class TestActionExecutor(unittest.TestCase):
    def setUp(self):
        self.dm = DisplayManager()
        self.executor = ActionExecutor(self.dm, dry_run=True)

    def test_action_move_to(self):
        cmd = ActionCommand(thought="Move cursor to button", action="move_to", x=500, y=300)
        res = self.executor.execute(cmd)
        self.assertTrue(res["success"])
        self.assertEqual(res["action"], "move_to")
        self.assertEqual(res["executed_coords"], (500, 300))

    def test_action_click(self):
        cmd = ActionCommand(thought="Click target", action="click", x=1200, y=700, button="right")
        res = self.executor.execute(cmd)
        self.assertTrue(res["success"])
        self.assertEqual(res["action"], "click")

    def test_action_drag_to(self):
        # Drag from screen 1 to screen 2
        cmd = ActionCommand(thought="Drag window to screen 2", action="drag_to", x=2800, y=400, button="left")
        res = self.executor.execute(cmd)
        self.assertTrue(res["success"])
        self.assertEqual(res["action"], "drag_to")

    def test_action_type_and_key(self):
        cmd_type = ActionCommand(thought="Type note", action="type", text="Hello ISHTI")
        res_type = self.executor.execute(cmd_type)
        self.assertTrue(res_type["success"])

        cmd_key = ActionCommand(thought="Submit", action="press_key", key="ctrl+enter")
        res_key = self.executor.execute(cmd_key)
        self.assertTrue(res_key["success"])

    def test_action_done(self):
        cmd = ActionCommand(thought="Finished task", action="done")
        res = self.executor.execute(cmd)
        self.assertTrue(res["success"])
        self.assertIn("complete", res["details"])


class TestMultiScreenOSAgent(unittest.TestCase):
    def setUp(self):
        self.agent = MultiScreenOSAgent(dry_run=True, max_steps=5)
        # Parsing tests use a fixed two-monitor layout, independent of the
        # developer machine's currently connected displays.
        self.agent.display_mgr.monitors = [
            MonitorInfo(id=1, name="Synthetic 1", left=0, top=0, right=1920, bottom=1080,
                        width=1920, height=1080, is_primary=True),
            MonitorInfo(id=2, name="Synthetic 2", left=1920, top=0, right=3840, bottom=1080,
                        width=1920, height=1080, is_primary=False),
        ]
        self.agent.display_mgr.virtual_left = 0
        self.agent.display_mgr.virtual_top = 0
        self.agent.display_mgr.virtual_width = 3840
        self.agent.display_mgr.virtual_height = 1080

    def test_system_prompt_matches_schema(self):
        prompt = self.agent.get_system_prompt()
        self.assertIn("You are an advanced Multi-Screen OS Automation Agent", prompt)
        self.assertIn("Perceive-Plan-Act", prompt)
        self.assertIn("### ENVIRONMENT CONFIGURATION", prompt)
        self.assertIn("### AVAILABLE ACTIONS", prompt)
        self.assertIn('"action": "move_to" | "click" | "double_click" | "drag_to" | "type" | "press_key" | "wait" | "done"', prompt)

    def test_parse_action_valid_json(self):
        raw_json = """
        {
          "thought": "I see the application window on Screen 1. I will drag it to Screen 2.",
          "action": "drag_to",
          "x": 3000,
          "y": 500,
          "button": "left"
        }
        """
        cmd = self.agent.parse_action(raw_json)
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd.action, "drag_to")
        self.assertEqual(cmd.x, 3000)
        self.assertEqual(cmd.y, 500)
        self.assertEqual(cmd.button, "left")

    def test_parse_action_inside_markdown(self):
        raw_md = """
        Here is my planned action:
        ```json
        {
          "thought": "Clicking the confirm button on Screen 2",
          "action": "click",
          "x": 2700,
          "y": 650
        }
        ```
        """
        cmd = self.agent.parse_action(raw_md)
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd.action, "click")
        self.assertEqual(cmd.x, 2700)
        self.assertEqual(cmd.y, 650)

    def test_parse_action_rejects_coordinates_outside_real_monitors(self):
        self.assertIsNone(self.agent.parse_action('{"action":"click","x":5000,"y":650}'))

    def test_perceive_plan_act_step(self):
        env = {"ISHTI_TEST_MODE": "1", "ISHTI_INTEL_OFFLINE_VISUAL_GUARD": "1"}
        with patch.dict(os.environ, env), patch("ishti.llm.chat") as chat:
            cmd, record = self.agent.step("Drag the window from Screen 1 to Screen 2")
        self.assertIsNotNone(cmd)
        self.assertIn("step", record)
        self.assertIn("cursor_before", record)
        self.assertIn("execution", record)
        self.assertTrue(record["execution"]["success"])
        chat.assert_not_called()

    def test_run_loop_completes_objective(self):
        with patch.object(self.agent, "_call_reasoning_engine", return_value='{"thought": "Task is complete.", "action": "done"}'):
            res = self.agent.run_loop("Finish and complete the task", max_steps=2)
            self.assertTrue(res["completed"])
            self.assertGreaterEqual(res["total_steps"], 1)
            self.assertIn("elapsed_seconds", res)
            self.assertIn("monitors", res)


def test_live_action_requires_approval_before_execution(monkeypatch):
    from ishti import test_mode

    monkeypatch.setattr(test_mode, "enabled", lambda: False)
    monkeypatch.setenv("ISHTI_TEST_MODE", "0")
    executor = ActionExecutor(DisplayManager(), dry_run=False)
    result = executor.execute(ActionCommand(thought="Unapproved test action", action="click", x=50, y=50))
    assert result["success"] is False
    assert result["error"] == "A specific approved screen action is required"
    assert executor.history == []


if __name__ == "__main__":
    unittest.main()
