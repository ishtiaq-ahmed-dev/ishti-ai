"""Evaluate attributed process RAM; system-wide drift is context, not leak proof.

python tools/qa/ram_gate.py samples.jsonl --pid 1234 --warmup 60 --min-seconds 120
Exit: 0 PASS, 1 FAIL, 2 UNMEASURED. Model reloads and unrelated tenants are not app leaks.
"""
import argparse
import json
from pathlib import Path


def slope(points):
    xs = [ts - points[0][0] for ts, _ in points]
    ys = [value for _, value in points]
    xm, ym = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x - xm) ** 2 for x in xs)
    return sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / den * 60 if den else 0.0


def evaluate(rows, pids=(), warmup=60, min_seconds=120, max_mb_min=20):
    samples = sorted((row['s'] for row in rows if isinstance(row.get('s'), dict)), key=lambda s: s['ts'])
    if len(samples) < 3:
        return {'status': 'UNMEASURED', 'reason': 'fewer than three attributed samples', 'processes': []}
    expected = set(pids) or {p['pid'] for s in samples for p in s.get('procs', []) if p.get('role') == 'app'}
    observed = {}
    for sample in samples:
        for proc in sample.get('procs', []):
            if proc['pid'] in expected:
                key = (proc['pid'], proc.get('created'))
                observed.setdefault(key, []).append((sample['ts'], proc))
    results = []
    for (pid, created), points in observed.items():
        start = points[0][0]
        stable = [(ts, p['rss_mb']) for ts, p in points if ts >= start + warmup]
        duration = stable[-1][0] - stable[0][0] if stable else 0
        gaps = any(b[0] - a[0] > 5 for a, b in zip(stable, stable[1:]))
        missing_identity = created is None
        ended_early = not any(p['pid'] == pid and p.get('created') == created
                              for p in samples[-1].get('procs', []))
        if len(stable) < 3 or duration < min_seconds or missing_identity or ended_early or gaps:
            results.append({'pid': pid, 'created': created, 'status': 'UNMEASURED',
                            'reason': 'insufficient stable window, missing identity, exited process, or sampling gaps',
                            'duration_s': duration})
            continue
        mb_min = slope(stable)
        results.append({'pid': pid, 'created': created, 'status': 'FAIL' if mb_min > max_mb_min else 'PASS',
                        'rss_mb_min': round(mb_min, 3), 'duration_s': duration,
                        'rss_delta_mb': stable[-1][1] - stable[0][1]})
    absent = expected - {result['pid'] for result in results}
    for pid in sorted(absent):
        results.append({'pid': pid, 'status': 'UNMEASURED', 'reason': 'target process not sampled'})
    system = [s.get('sys', {}).get('used_mb') for s in samples]
    system = [value for value in system if value is not None]
    size = max(1, len(system) // 4)
    drift = (sum(system[-size:]) - sum(system[:size])) / size if system else None
    status = 'FAIL' if any(r['status'] == 'FAIL' for r in results) else \
        'PASS' if results and all(r['status'] == 'PASS' for r in results) else 'UNMEASURED'
    return {'status': status, 'processes': results, 'threshold_mb_min': max_mb_min,
            'warmup_s': warmup, 'minimum_measured_s': min_seconds,
            'system_quarter_drift_mb': drift,
            'system_context_warning': drift is not None and drift > 500,
            'attribution': 'Only selected app processes decide PASS/FAIL; system RAM is context.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('samples')
    parser.add_argument('--pid', action='append', type=int, default=[])
    parser.add_argument('--warmup', type=float, default=60)
    parser.add_argument('--min-seconds', type=float, default=120)
    parser.add_argument('--max-mb-min', type=float, default=20)
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.warmup < 0 or args.min_seconds <= 0 or args.max_mb_min <= 0:
        parser.error('warmup must be nonnegative and measurement/threshold positive')
    rows = []
    with open(args.samples, encoding='utf-8') as stream:
        for line in stream:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    result = evaluate(rows, args.pid, args.warmup, args.min_seconds, args.max_mb_min)
    print(json.dumps(result, indent=2))
    if args.output:
        with Path(args.output).open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
    return {'PASS': 0, 'FAIL': 1, 'UNMEASURED': 2}[result['status']]


if __name__ == '__main__':
    raise SystemExit(main())
