"""Live probe: prove qwen3:14b now answers through ISHTI's arch2 path.

Switches the architecture profile to memory_augmented (arch2 pipeline: its main +
validation model is qwen3:14b per config), routes one question through the real
dispatcher, then ALWAYS restores the previous profile. Run:

    venv\\Scripts\\python -X utf8 tools\\probe_arch2_qwen3.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ishti.core.architectures import get_registry

reg = get_registry()
before = reg.active()
print("active before:", before)
try:
    sw = reg.set_active("memory_augmented", by="qwen3-probe", run_health_check=True)
    print("switch -> memory_augmented:", sw.get("switched"), sw.get("reason", ""))
    if sw.get("switched"):
        import ishti.dispatcher as D
        res = D.handle("what is 17 times 23? reply with just the number", session_id="qwen3probe")
        keys = {k: res[k] for k in res if k in ("skill", "arch", "model", "profile")}
        print("meta:", keys)
        ans = str(res.get("final_answer") or res.get("reply") or "")
        print("answer:", ans[:150].replace("\n", " "))
        ok = "391" in ans
        print("PROBE", "PASS" if ok else "FAIL", "- qwen3:14b served the arch2 answer" if ok else "")
finally:
    back = reg.set_active(before, by="qwen3-probe-restore", run_health_check=False)
    print("restored:", back.get("switched"), "-> active:", reg.active())
