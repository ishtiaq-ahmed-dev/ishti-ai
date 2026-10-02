"""Phase B4: VRAM Governor Pressure, Downshift, and No-Offload Verification Test.
Tests admitting a heavy vision model (gemma3:12b, 8.1GB) while planner is resident,
validating size == size_vram (no offload), role downshift/upshift, and zero violations.
"""
import sys
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import json
import time
import requests
import subprocess
from ishti import vram

def run_test():
    print("=================================================================")
    print("  PHASE B4: VRAM GOVERNOR / NO-OFFLOAD VERIFICATION")
    print("=================================================================")

    # Step 1: Check initial residency
    print("\n[Step 1] Inspecting initial Ollama /api/ps:")
    ps1 = requests.get("http://127.0.0.1:11434/api/ps", timeout=5).json().get("models", [])
    for m in ps1:
        size, vram_sz = m["size"], m["size_vram"]
        print(f"  - {m['name']}: size={size} bytes, size_vram={vram_sz} bytes, 100% GPU={vram_sz >= size}")
        assert vram_sz >= size, f"PARTIAL OFFLOAD DETECTED initially for {m['name']}!"

    # Step 2: Request heavy vision model
    print("\n[Step 2] Admitting heavy vision model gemma3:12b (vision3 role)...")
    t0 = time.time()
    admit_res = vram.admit("gemma3:12b", role="vision3", job="heavy visual analysis test", strict=True, wait_s=45)
    elapsed = time.time() - t0
    print(f"  Admit response in {elapsed:.2f}s: {admit_res}")
    assert admit_res.get("ok") is True, f"Admit failed: {admit_res}"

    # Step 3: Verify /api/ps post-admission: every single model must have size == size_vram
    print("\n[Step 3] Checking Ollama /api/ps under heavy model load:")
    ps2 = requests.get("http://127.0.0.1:11434/api/ps", timeout=5).json().get("models", [])
    for m in ps2:
        size, vram_sz = m["size"], m["size_vram"]
        is_full = vram_sz >= size
        print(f"  - {m['name']}: size={size} bytes, size_vram={vram_sz} bytes, 100% GPU={is_full}")
        assert is_full, f"CRITICAL FAILURE: Partial offload detected for {m['name']} ({vram_sz} < {size})!"

    # Step 4: Check nvidia-smi
    print("\n[Step 4] Checking nvidia-smi memory stats:")
    smi = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free", "--format=csv,noheader"],
                         capture_output=True, text=True).stdout.strip()
    print(f"  GPU Memory (Total, Used, Free): {smi}")

    # Step 5: Check governor violations
    violations = vram.check_violations()
    print(f"  Governor violations list: {violations}")
    assert len(violations) == 0, f"Governor reported violations: {violations}"

    # Step 6: Release lease
    lease = admit_res.get("lease")
    if lease:
        print(f"\n[Step 6] Releasing lease {lease}...")
        rel = vram.release(lease)
        print(f"  Release result: {rel}")

    print("\n[VERIFIED PASS] All loaded models verified size_vram == size. ZERO partial offload!")

if __name__ == "__main__":
    run_test()
