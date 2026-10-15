"""Tests for CapCut skill and capcut-cli integration:
Verifies trigger matching, project listing, doctor preflight,
draft creation, timeline inspection, text overlays, silence analysis, and proxy rendering.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti.skills.capcut import skill
from tools.video.capcut_controller import get_capcut_controller

SAMPLE_VIDEO = os.path.join(ROOT, "data", "test_sample.mp4")


def test_matching():
    print("--- 1. Testing CapCut Trigger Matching ---")
    assert skill.match("open capcut"), "Should match 'open capcut'"
    assert skill.match("list my CapCut projects"), "Should match 'CapCut projects'"
    assert skill.match("create a 9:16 short in cap cut"), "Should match 'cap cut'"
    assert skill.match("edit in jianying please"), "Should match 'jianying'"
    assert skill.match("打开剪映草稿"), "Should match '剪映'"
    assert not skill.match("what is the weather in London?"), "Should not match weather"
    assert not skill.match("send a whatsapp to Ali"), "Should not match whatsapp"
    print("  [✓] All trigger patterns matched correctly.")


def test_controller_and_doctor():
    print("\n--- 2. Testing CapCut Controller & Doctor ---")
    ctrl = get_capcut_controller()
    assert ctrl.is_installed(), "capcut-cli must be installed"
    doc = ctrl.doctor()
    assert doc.get("ok"), f"Doctor checks failed: {doc}"
    print(f"  [✓] Doctor OK: Node {doc.get('node')}, platform {doc.get('platform')}")


def test_projects_list():
    print("\n--- 3. Testing Project Listing ---")
    session = {}
    res = skill.run("list my capcut projects", session)
    assert "CapCut Projects" in res["reply"], f"Unexpected reply: {res['reply']}"
    print("  [✓] Projects listing returned formatted table.")


@pytest.mark.e2e  # needs the real CapCut install and writes to its draft store: run explicitly, never in the offline suite
def test_quickstart_and_timeline():
    print("\n--- 4. Testing Project Quickstart & Timeline Inspection ---")
    ctrl = get_capcut_controller()
    test_proj_name = "ISHTI Automated Test Draft"
    res = ctrl.quickstart(test_proj_name, video=SAMPLE_VIDEO, ratio="9:16")
    assert res.get("ok") or os.path.isdir(res.get("draft_path", "")), f"Quickstart failed: {res}"
    pdir = res.get("draft_path") or ctrl.get_project_dir(test_proj_name)
    print(f"  [✓] Draft created at: {pdir}")

    # Add text overlay
    text_res = ctrl.add_text(pdir, "Automated Test Title", start="0s", duration="2.5s", font_size=18)
    assert text_res.get("ok"), f"Add text failed: {text_res}"
    print(f"  [✓] Text segment added: {text_res.get('text')} (id: {text_res.get('segment_id')})")

    # Inspect timeline
    info = ctrl.get_info(pdir)
    assert info.get("tracks", 0) >= 2, f"Expected at least 2 tracks (video + text), got {info.get('tracks')}"
    timeline = ctrl.get_timeline(pdir, human=True)
    assert "video" in str(timeline) and "text" in str(timeline), f"Timeline layout missing tracks: {timeline}"
    print(f"  [✓] Verified timeline tracks: {info.get('tracks')} tracks, {info.get('segments')} segments")


def test_silence_detection():
    print("\n--- 5. Testing Silence Detection ---")
    ctrl = get_capcut_controller()
    res = ctrl.detect_silence(SAMPLE_VIDEO)
    assert "duration" in res, f"Silence detection missing duration: {res}"
    print(f"  [✓] Media duration: {res.get('duration')}s, silences detected: {len(res.get('silences', []))}")


@pytest.mark.e2e  # needs the real CapCut install and a draft from the test above
def test_proxy_render():
    print("\n--- 6. Testing Proxy Preview Render ---")
    ctrl = get_capcut_controller()
    test_proj_name = "ISHTI Automated Test Draft"
    pdir = ctrl.get_project_dir(test_proj_name)
    out_file = os.path.join(ROOT, "data", "media_output", "test_automated_preview.mp4")
    res = ctrl.render_preview(pdir, out_path=out_file, burn_captions=False)
    assert res.get("ok") and os.path.isfile(out_file), f"Proxy render failed: {res}"
    print(f"  [✓] Proxy rendered to: {out_file} ({os.path.getsize(out_file)} bytes)")


def run_all_tests():
    print("========================================================================")
    print("  TESTING CAPCUT SKILL & CAPCUT-CLI INTEGRATION")
    print("========================================================================")
    test_matching()
    test_controller_and_doctor()
    test_projects_list()
    test_quickstart_and_timeline()
    test_silence_detection()
    test_proxy_render()
    print("\n[SUCCESS] All CapCut skill and CLI integration tests passed!")


if __name__ == "__main__":
    run_all_tests()
