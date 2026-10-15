"""Bounded isolated soak: python tools/load_ramp.py 4 480 tag --profile mixed.

skills: dispatcher workload. inference: direct planner calls. mixed: alternating paths.
Each run owns unique artifacts and a scratch worker with a finite cleanup deadline.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

WORKLOAD = [
    "what's the weather in Peshawar today", "remind me in 40 minutes to qa-stretch",
    "Reply with the exact word: PONG", "find files named readme in {ROOT}/tests/fixtures",
    "what do you remember about me", "note: qa ramp marker {n}",
    "show the top level of {ROOT}/tests/fixtures", "bayes: prior 1% sensitivity 99% specificity 95%",
    "how much RAM is free", "check the last 24 hours of all my chats and give me a report",
]
INFERENCE_WORKLOAD = [
    "Explain why a business should reconcile invoices in three short sentences.",
    "Describe three ways a shop can reduce wasted inventory without using outside facts.",
    "Explain revenue and profit using a small fictional example.",
]


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('level', type=int, nargs='?', default=4)
    parser.add_argument('window_s', type=float, nargs='?', default=480)
    parser.add_argument('tag', nargs='?', default='soak')
    parser.add_argument('--profile', choices=('skills', 'inference', 'mixed'), default='skills')
    parser.add_argument('--output-dir', default=str(Path(ROOT) / 'metrics'))
    parser.add_argument('--startup-timeout', type=float, default=60)
    parser.add_argument('--cleanup-grace', type=float, default=5)
    parser.add_argument('--worker', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 1 <= args.level <= 32 or not 0.1 <= args.window_s <= 3600:
        parser.error('level must be 1-32 and window 0.1-3600 seconds')
    if not 0.1 <= args.startup_timeout <= 300 or not 0 <= args.cleanup_grace <= 30:
        parser.error('startup timeout must be 0.1-300s and cleanup grace 0-30s')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', args.tag):
        parser.error('tag must be 1-64 letters, digits, underscores or hyphens')
    return args


def reserve_run(args):
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    tag = f'{args.tag}_{time.strftime("%Y%m%dT%H%M%S")}_{uuid.uuid4().hex[:8]}'
    config = dict(level=args.level, window_s=args.window_s, tag=tag, profile=args.profile,
                  cleanup_grace=args.cleanup_grace, output_dir=str(output),
                  metrics_path=str(output / f'ramp_level_{tag}.jsonl'),
                  turns_path=str(output / f'ramp_turns_{tag}.jsonl'),
                  summary_path=str(output / f'ramp_summary_level_{args.level}_{tag}.json'),
                  log_path=str(output / f'ramp_worker_{tag}.log'),
                  scratch_dir=str(output / f'ramp_scratch_{tag}'))
    path = output / f'ramp_run_{tag}.json'
    with path.open('x', encoding='utf-8') as stream:
        json.dump(config, stream, indent=2)
    return config, path


def stop_worker(proc):
    """Terminate only this supervisor's worker tree, never processes selected by name."""
    import psutil
    try:
        child = psutil.Process(proc.pid)
        owned = child.children(recursive=True)
        owned.append(child)
        for item in reversed(owned):
            try:
                item.kill()
            except psutil.NoSuchProcess:
                continue
        _, alive = psutil.wait_procs(owned, timeout=3)
        if alive:
            raise RuntimeError('Task-owned soak processes did not terminate')
    except psutil.NoSuchProcess:
        proc.wait(timeout=5)
        return
    proc.wait(timeout=5)


def supervise(config, path, args):
    env = dict(os.environ, ISHTI_TEST_MODE='1', ISHTI_SCRATCH='1',
               ISHTI_SCRATCH_DIR=config['scratch_dir'], PYTHONDONTWRITEBYTECODE='1',
               PYTHONIOENCODING='utf-8')
    started = time.monotonic()
    timed_out = forced = False
    with open(config['log_path'], 'x', encoding='utf-8') as log:
        proc = subprocess.Popen([sys.executable, '-B', '-u', __file__, '--worker', str(path)],
                                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            deadline = started + args.startup_timeout + args.window_s + args.cleanup_grace
            while proc.poll() is None:
                if Path(config['summary_path']).exists():
                    try:
                        proc.wait(timeout=args.cleanup_grace)
                    except subprocess.TimeoutExpired:
                        forced = True
                        stop_worker(proc)
                    break
                if time.monotonic() >= deadline:
                    timed_out = forced = True
                    stop_worker(proc)
                    break
                time.sleep(0.05)
        finally:
            if proc.poll() is None:
                stop_worker(proc)
    try:
        summary = json.loads(Path(config['summary_path']).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        summary = dict(level=args.level, window_s=args.window_s, tag=config['tag'],
                       profile=args.profile, turns=0, errors=1, incomplete_requests=None)
    summary.update(worker_pid=proc.pid, worker_exit=proc.returncode,
                   supervisor_wall_s=round(time.monotonic() - started, 3),
                   timed_out=timed_out, forced_cleanup=forced,
                   status='completed' if proc.returncode == 0 and not forced
                   and not summary.get('incomplete_requests') else 'incomplete',
                   metrics_path=config['metrics_path'], turns_path=config['turns_path'],
                   log_path=config['log_path'])
    summary_path = Path(config['summary_path'])
    summary_path.write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return summary


def run_window(config, invoke, sample):
    """Stream turns; retain counters and three errors instead of every token/turn."""
    stop = threading.Event()
    lock = threading.Lock()
    counters = dict(turns=0, errors=0, secs_sum=0.0, secs_max=0.0,
                    first_sum=0.0, first_count=0, inference_turns=0, err_samples=[], active=0)
    started = time.monotonic()
    with open(config['metrics_path'], 'x', encoding='utf-8') as metrics, \
            open(config['turns_path'], 'x', encoding='utf-8') as turns:
        metrics.write(json.dumps({'_meta': {'pid': os.getpid(), 'started': time.time(),
                                           'tag': config['tag'], 'profile': config['profile']}}) + '\n')
        metrics.flush()

        def worker(wid):
            index = 0
            while not stop.is_set():
                inference = config['profile'] == 'inference' or (config['profile'] == 'mixed' and index % 2 == 0)
                workload = INFERENCE_WORKLOAD if inference else WORKLOAD
                text = workload[(wid * 3 + index) % len(workload)]
                text = text.replace('{n}', f'{wid}-{index}').replace('{ROOT}', ROOT.replace('\\', '/'))
                first = []
                def token(_text):
                    if not first:
                        first.append(time.monotonic())
                t0 = time.monotonic()
                error = None
                with lock:
                    if stop.is_set():
                        break
                    counters['active'] += 1
                try:
                    result = invoke(text, f"qa_{config['tag']}_w{wid}", token, inference)
                    if not str(result.get('final_answer') or result.get('reply') or '').strip():
                        error = 'empty reply'
                except Exception as exc:
                    result = {}
                    error = f'{type(exc).__name__}: {str(exc)[:200]}'
                elapsed = time.monotonic() - t0
                ft = first[0] - t0 if first else None
                with lock:
                    counters['active'] -= 1
                    if stop.is_set():
                        break
                    counters['turns'] += 1
                    counters['secs_sum'] += elapsed
                    counters['secs_max'] = max(counters['secs_max'], elapsed)
                    counters['inference_turns'] += int(inference)
                    if ft is not None:
                        counters['first_count'] += 1
                        counters['first_sum'] += ft
                    if error:
                        counters['errors'] += 1
                        if len(counters['err_samples']) < 3:
                            counters['err_samples'].append(error)
                    turns.write(json.dumps(dict(worker=wid, turn=index, secs=round(elapsed, 3),
                                                first_token=ft, err=error,
                                                path='direct_planner' if inference else 'dispatcher',
                                                skill=result.get('skill'), mode=result.get('mode'))) + '\n')
                    turns.flush()
                index += 1
                stop.wait(config.get('interval_s', 2))

        def monitor():
            while not stop.is_set():
                try:
                    row = sample()
                except Exception as exc:
                    row = {'sampler_error': type(exc).__name__, 'ts': time.time()}
                with lock:
                    if stop.is_set():
                        break
                    metrics.write(json.dumps(row) + '\n')
                    metrics.flush()
                stop.wait(1)

        workers = [threading.Thread(target=worker, args=(wid,), daemon=True) for wid in range(config['level'])]
        monitor_thread = threading.Thread(target=monitor, daemon=True)
        for thread in workers:
            thread.start()
        monitor_thread.start()
        stop.wait(config['window_s'])
        with lock:
            stop.set()
            incomplete_at_window = counters['active']
        deadline = time.monotonic() + config['cleanup_grace']
        for thread in [*workers, monitor_thread]:
            thread.join(max(0, deadline - time.monotonic()))
        incomplete = sum(thread.is_alive() for thread in workers)
        count = counters['turns']
        return dict(level=config['level'], window_s=config['window_s'], tag=config['tag'],
                    profile=config['profile'], turns=count, errors=counters['errors'],
                    err_samples=counters['err_samples'], incomplete_requests=incomplete_at_window,
                    remaining_workers=incomplete,
                    inference_turns=counters['inference_turns'],
                    latency_avg_s=round(counters['secs_sum'] / count, 3) if count else None,
                    latency_max_s=round(counters['secs_max'], 3) if count else None,
                    first_token_avg_s=round(counters['first_sum'] / counters['first_count'], 3)
                    if counters['first_count'] else None,
                    worker_wall_s=round(time.monotonic() - started, 3))


def worker_entry(path):
    config = json.loads(Path(path).read_text(encoding='utf-8'))
    from ishti import dispatcher, taskmgr
    from tools.monitor import sample

    def invoke(text, sid, token, inference):
        if inference:
            from ishti import llm
            answer = llm.chat([{'role': 'user', 'content': text}], role='planner', max_tokens=160)
            return {'reply': answer.content, 'mode': 'DIRECT_PLANNER'}
        return dispatcher.handle(text, sid, on_token=token)

    summary = run_window(config, invoke, sample)
    summary['background_tasks'] = sum(row.get('status') in ('queued', 'running', 'paused')
                                      for row in taskmgr.TASKS.values())
    with open(config['summary_path'], 'x', encoding='utf-8') as stream:
        json.dump(summary, stream, indent=2)
    return 2 if summary['errors'] or summary['incomplete_requests'] or not summary['turns'] else 0


def main(argv=None):
    args = arguments(argv)
    if args.worker:
        return worker_entry(args.worker)
    config, path = reserve_run(args)
    summary = supervise(config, path, args)
    print(json.dumps(summary), flush=True)
    return 0 if summary['status'] == 'completed' and not summary['errors'] and summary['turns'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
