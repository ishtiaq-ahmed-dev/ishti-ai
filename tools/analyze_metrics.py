"""Read tagged ramp summaries with their matching samples; preserve legacy records.

python tools/analyze_metrics.py --metrics-dir metrics --output report.csv
The default output gets a unique filename. Importing this module does not write files.
"""
import argparse
import csv
import json
from pathlib import Path
import re
import time
import uuid

METRICS = Path(__file__).resolve().parents[1] / 'metrics'


def collect(metrics):
    metrics = Path(metrics).resolve()
    rows = []
    for path in sorted(metrics.glob('ramp_summary_level_*.json')):
        summary = json.loads(path.read_text(encoding='utf-8'))
        level = summary.get('level', int(re.search(r'level_(\d+)', path.name).group(1)))
        tag = summary.get('tag', str(level))
        samples_path = metrics / f'ramp_level_{tag}.jsonl'
        if not samples_path.exists():
            rows.append(dict(level=level, tag=tag, status='UNMEASURED', reason='matching samples missing'))
            continue
        peak_vram = partial = samples = switches = 0
        peak_cpu = peak_ram = 0
        previous = None
        with samples_path.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    sample = json.loads(line)
                except ValueError:
                    continue
                if '_meta' in sample:
                    continue
                samples += 1
                peak_vram = max(peak_vram, (sample.get('gpu') or {}).get('used_mb', 0))
                peak_cpu = max(peak_cpu, sample.get('cpu_pct') or 0)
                peak_ram = max(peak_ram, (sample.get('ram') or {}).get('used_mb', 0))
                partial += sample.get('partial_offloads', 0)
                names = tuple(sorted(str(m['name']) for m in sample.get('loaded_models', [])))
                if names != previous:
                    switches += int(previous is not None)
                    previous = names
        rows.append(dict(level=level, tag=tag, status=summary.get('status', 'historical'),
                         profile=summary.get('profile', 'skills'), turns=summary['turns'],
                         errors=summary['errors'], latency_avg_s=summary['latency_avg_s'],
                         latency_max_s=summary['latency_max_s'], first_token_avg_s=summary['first_token_avg_s'],
                         vram_peak_mb=peak_vram, partial_offload_samples=partial,
                         cpu_peak_pct=round(peak_cpu, 1), system_ram_peak_mb=int(peak_ram),
                         model_switches_observed=switches, monitor_samples=samples))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metrics-dir', default=str(METRICS))
    parser.add_argument('--output')
    args = parser.parse_args()
    rows = collect(args.metrics_dir)
    output = Path(args.output) if args.output else Path(args.metrics_dir) / \
        f'ramp_table_{time.strftime("%Y%m%dT%H%M%S")}_{uuid.uuid4().hex[:8]}.csv'
    with output.open('x', newline='', encoding='utf-8') as stream:
        fields = list(dict.fromkeys(key for row in rows for key in row)) or ['status']
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({'records': rows, 'csv': str(output)}, indent=2))


if __name__ == '__main__':
    main()
