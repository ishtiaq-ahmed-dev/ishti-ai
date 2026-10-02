"""HTTP-level benchmark for the web UI (Cycle 4): the serving cost bench_opt does not cover.

    venv\\Scripts\\python -m tools.bench_http [--json _work/bench_http.json] [--runs N]

Measures, through the real ASGI app (in-process, no network noise):
  index_html      GET /            (the built React app)
  asset_js        GET /assets/<biggest .js>   (repeat-load cost with cache headers)
  asset_css       GET /assets/<biggest .css>
  static_img      GET /ishti-logo.png
  api_health      GET /api/health  (watchdog poll)
  api_skills      GET /api/skills  (122 manifests)
  api_facts       GET /api/facts
  api_system      GET /api/system  (psutil + nvidia-smi path)

Each entry reports the median wall time (ms), the payload bytes and the negotiated
Content-Encoding, so a "before/after" diff shows both latency and transfer size.
"""
import argparse
import glob
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _pick_assets():
    """(biggest js, biggest css) in dist/assets - the real first-paint payload."""
    assets = os.path.join(ROOT, "interface", "app", "dist", "assets")
    js = sorted(glob.glob(os.path.join(assets, "*.js")), key=os.path.getsize, reverse=True)
    css = sorted(glob.glob(os.path.join(assets, "*.css")), key=os.path.getsize, reverse=True)
    rel = lambda p: "/assets/" + os.path.basename(p) if p else None
    return rel(js[0]) if js else None, rel(css[0]) if css else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None, help="write the measurements here")
    ap.add_argument("--runs", type=int, default=12, help="requests per endpoint (median reported)")
    args = ap.parse_args()

    from fastapi.testclient import TestClient
    import interface.web_ui.server as srv  # imports the dispatcher: skills load once, like the real boot

    js, css = _pick_assets()
    paths = [("index_html", "/"), ("asset_js", js), ("asset_css", css), ("static_img", "/ishti-logo.png"),
             ("api_health", "/api/health"), ("api_skills", "/api/skills"), ("api_facts", "/api/facts"),
             ("api_system", "/api/system")]
    paths = [(k, p) for k, p in paths if p]

    client = TestClient(srv.app, headers={"authorization": f"Bearer {srv.API_TOKEN}"})
    out = {}
    for name, path in paths:
        xs, size, enc, status, wire = [], 0, "", 0, 0
        for i in range(args.runs):
            t0 = time.perf_counter()
            r = client.get(path, headers={"accept-encoding": "gzip"})
            xs.append((time.perf_counter() - t0) * 1000)
            if i == 0:  # first response: what actually crosses the wire
                size, enc, status = len(r.content), r.headers.get("content-encoding", "identity"), r.status_code
                wire = int(r.headers.get("content-length") or len(r.content))
        out[name] = {"median_ms": round(statistics.median(xs), 3), "min_ms": round(min(xs), 3),
                     "bytes": wire, "decoded_bytes": size, "encoding": enc, "status": status, "path": path}
        print(f"[bench_http] {name:12s} {out[name]['median_ms']:9.3f} ms  wire {wire:>9} B "
              f"(decoded {size:>9} B) {enc:8s} {status}  {path}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
        print(f"[bench_http] written {args.json}")


if __name__ == "__main__":
    main()
