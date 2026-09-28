"""NEEDS 16: enumerate every audio INPUT device on this PC and record hard evidence of which
can actually deliver audio (not just appear in a list). Writes a machine-readable verdict to
data/audio_devices.json and prints a human summary.

Run:  D:\\ISHTI\\venv\\Scripts\\python.exe D:\\ISHTI\\tools\\probe_audio_devices.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
import sounddevice as sd

OUT = os.path.join(ROOT, "data", "audio_devices.json")


def probe():
    devices = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] <= 0:
            continue
        entry = {"index": i, "name": d["name"].strip(), "hostapi": sd.query_hostapis(d["hostapi"])["name"],
                 "channels_in": d["max_input_channels"], "default_rate": int(d["default_samplerate"]),
                 "default_input": (i == sd.default.device[0])}
        # actually OPEN it at its advertised rate and measure real signal, not just a successful open
        try:
            frames = int(0.8 * d["default_samplerate"])
            rec = sd.rec(frames, samplerate=int(d["default_samplerate"]), channels=1,
                         dtype="int16", device=i, blocking=True)
            x = rec.astype(np.float32) / 32768.0
            peak = float(np.abs(x).max()) if x.size else 0.0
            rms = float(np.sqrt((x ** 2).mean())) if x.size else 0.0
            entry.update({"opens": True, "peak": round(peak, 4), "rms": round(rms, 5),
                          "signal": "live audio" if peak > 0.001 else "opened but silent (no mic or muted)"})
        except Exception as exc:
            entry.update({"opens": False, "signal": f"cannot open: {type(exc).__name__}: {str(exc)[:80]}"})
        devices.append(entry)
    return devices


def main():
    print(f"Host APIs: {', '.join(h['name'] for h in sd.query_hostapis())}")
    print(f"Default input: #{sd.default.device[0]}\n")
    devices = probe()
    for d in devices:
        mark = "*" if d["default_input"] else " "
        print(f" {mark} #{d['index']:>2} [{d['hostapi'][:9]:<9}] {d['name'][:52]:<52} "
              f"{d['signal']} (peak={d.get('peak', '-')})")
    usable = [d for d in devices if d.get("opens") and d.get("peak", 0) > 0.001]
    verdict = {"usable_for_voice": usable, "all_inputs": devices,
               "conclusion": ("live microphone(s) found: " + "; ".join(d["name"] for d in usable)) if usable
                             else "no input delivered live audio - voice input cannot be measured on this PC "
                                  "(barge-in / turn-taking stay code-verified only, NEEDS 15/16)"}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(verdict, f, indent=2, ensure_ascii=False)
    print(f"\n{verdict['conclusion']}")
    print(f"written: {OUT}")


if __name__ == "__main__":
    main()
