"""RAM retention and bounded soak regressions; scratch data and owned process fixtures only."""
import asyncio
import gc
import errno
import json
import os
from pathlib import Path
import sys
import threading
import time
import weakref
import socket

import psutil
import pytest


def test_offline_qualification_cannot_connect_to_real_model_or_provider():
    if os.environ.get('ISHTI_OFFLINE_EVAL') != '1':
        pytest.skip('Offline runner flag is required for the network boundary fixture')
    with socket.socket() as sock:
        for target in [('127.0.0.1', 11434), ('203.0.113.1', 443)]:
            with pytest.raises(ConnectionRefusedError, match='Offline evaluation blocked'):
                sock.connect(target)
            assert sock.connect_ex(target) == errno.ECONNREFUSED


@pytest.fixture
def sessions(monkeypatch):
    from ishti import dispatcher as d
    monkeypatch.setattr(d, 'SESSIONS', {})
    monkeypatch.setattr(d, 'INFLIGHT', {})
    monkeypatch.setattr(d, 'SESSION_CACHE_MAX', 3)
    monkeypatch.setattr(d, 'SESSION_CACHE_TTL_S', 86400)
    return d


class Conversation:
    active_session_id = 'current'
    def __init__(self):
        self.reads = []
    def get_messages(self, sid):
        self.reads.append(sid)
        return [{'role': 'user', 'content': f'saved history of {sid}'}]


def test_session_lru_evicts_idle_state_and_condition_then_rehydrates(sessions):
    d = sessions
    conv = Conversation()
    for sid in ('a', 'b', 'c'):
        d._runtime_session(sid, conv)
        d.INFLIGHT[sid] = threading.Condition()
    d._runtime_session('a', conv)
    d._runtime_session('d', conv)
    assert set(d.SESSIONS) == {'a', 'c', 'd'} and 'b' not in d.INFLIGHT
    assert d._runtime_session('b', conv)['history'][0]['content'] == 'saved history of b'
    assert conv.reads.count('b') == 2 and len(d.SESSIONS) == 3


def test_session_expiry_preserves_active_waiters(sessions):
    d = sessions
    conv = Conversation()
    active = d._runtime_session('active', conv, lease=True)
    idle = d._runtime_session('idle', conv)
    active['_last_used'] = idle['_last_used'] = 1
    with d._SESSIONS_LOCK:
        d._prune_sessions(100000)
    assert set(d.SESSIONS) == {'active'}
    second = d._runtime_session('active', conv, lease=True)
    assert second is active and active['_leases'] == 2
    d._release_session(second)
    assert active['_leases'] == 1
    d._release_session(active)
    assert active['_leases'] == 0


def test_active_sessions_share_state_and_trim_after_release(sessions):
    d = sessions
    conv = Conversation()
    held = [d._runtime_session(str(index), conv, lease=True) for index in range(8)]
    assert len(d.SESSIONS) == 8
    assert d._runtime_session('0', conv, lease=True) is held[0]
    d._release_session(held[0])
    for row in held:
        d._release_session(row)
    assert len(d.SESSIONS) <= 3


def test_handle_releases_lease_on_early_error(sessions, monkeypatch):
    d = sessions
    monkeypatch.setattr(d, 'get_conversation_manager', Conversation)
    def fail(*_args):
        raise ValueError('fixture before normal request finally')
    monkeypatch.setattr(d, '_handle_session', fail)
    with pytest.raises(ValueError):
        d.handle('fixture', 'errors')
    assert d.SESSIONS['errors']['_leases'] == 0


def test_concurrent_requests_keep_one_shared_session_until_both_release(sessions, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    d = sessions
    conv = Conversation()
    monkeypatch.setattr(d, 'get_conversation_manager', lambda: conv)
    entered = threading.Barrier(3)
    release = threading.Event()
    references = []
    def fake_handle(_text, _sid, _channel, _token, _event, shared, _conv):
        references.append(shared)
        entered.wait(timeout=3)
        assert release.wait(3)
        return {'final_answer': 'fixture'}
    monkeypatch.setattr(d, '_handle_session', fake_handle)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(d.handle, 'fixture', 'same') for _ in range(2)]
        entered.wait(timeout=3)
        try:
            for index in range(20):
                d._runtime_session(f'idle-{index}', conv)
            assert references[0] is references[1] is d.SESSIONS['same']
            assert d.SESSIONS['same']['_leases'] == 2
            d._drop_questions()
        finally:
            release.set()
        assert all(future.result(timeout=3)['final_answer'] == 'fixture' for future in futures)
    assert len(d.SESSIONS) <= 3


def test_session_churn_releases_evicted_payloads(sessions):
    class Payload:
        pass
    d = sessions
    refs = []
    conv = Conversation()
    for index in range(1000):
        sid = f'synthetic-{index}'
        row = d._runtime_session(sid, conv)
        payload = Payload()
        payload.data = 'x' * 10000
        row['test_payload'] = payload
        refs.append(weakref.ref(payload))
        d.INFLIGHT[sid] = threading.Condition()
    del row, payload
    gc.collect()
    assert len(d.SESSIONS) == len(d.INFLIGHT) == 3
    assert sum(ref() is not None for ref in refs) == 3


def test_vault_sweeps_expired_and_caps_live_approvals(monkeypatch):
    from interface.web_ui import server
    monkeypatch.setattr(server, '_VAULT_APPROVALS', {})
    monkeypatch.setattr(server, '_VAULT_APPROVAL_MAX', 3)
    server._VAULT_APPROVALS['expired'] = {'target': 'all', 'expires': time.monotonic() - 1}
    tokens = [server._vault_prepare('all')['approval_token'] for _ in range(10)]
    assert len(server._VAULT_APPROVALS) == 3 and 'expired' not in server._VAULT_APPROVALS
    assert not server._vault_consume('all', tokens[0])
    assert server._vault_consume('all', tokens[-1])
    assert not server._vault_consume('all', tokens[-1])


def test_vault_exact_expiry_is_invalid(monkeypatch):
    from interface.web_ui import server
    monkeypatch.setattr(server, '_VAULT_APPROVALS', {'token': {'target': 'all', 'expires': 100}})
    monkeypatch.setattr(server.time, 'monotonic', lambda: 100)
    assert not server._vault_consume('all', 'token')


@pytest.mark.parametrize('cancel', [False, True])
def test_websocket_disconnect_and_cancellation_release_all_resources(monkeypatch, cancel):
    from interface.web_ui import server
    from ishti import events
    monkeypatch.setattr(server, 'authorized', lambda _socket: True)
    monkeypatch.setattr(server, 'active_websockets', [])
    monkeypatch.setattr(events, '_subs', [])
    monkeypatch.setattr(events, 'replay', lambda: [])
    class Socket:
        def __init__(self):
            self.receiving = asyncio.Event()
        async def accept(self):
            return None
        async def send_text(self, _message):
            return None
        async def receive_text(self):
            self.receiving.set()
            if cancel:
                await asyncio.Event().wait()
            raise server.WebSocketDisconnect()
    async def exercise():
        before = set(asyncio.all_tasks())
        socket = Socket()
        endpoint = asyncio.create_task(server.websocket_endpoint(socket))
        await asyncio.wait_for(socket.receiving.wait(), 2)
        if cancel:
            endpoint.cancel()
            with pytest.raises(asyncio.CancelledError):
                await endpoint
        else:
            await endpoint
        await asyncio.sleep(0)
        assert set(asyncio.all_tasks()) == before
        assert not server.active_websockets and not events._subs
    asyncio.run(exercise())


def test_tagged_runs_preserve_legacy_and_each_other(tmp_path):
    from tools import load_ramp as ramp
    legacy = tmp_path / 'ramp_summary_level_4.json'
    legacy.write_bytes(b'owner historical data')
    args = ramp.arguments(['4', '1', 'repeat', '--output-dir', str(tmp_path)])
    first, _ = ramp.reserve_run(args)
    second, _ = ramp.reserve_run(args)
    assert first['summary_path'] != second['summary_path']
    assert first['metrics_path'] != second['metrics_path']
    assert legacy.read_bytes() == b'owner historical data'


@pytest.mark.parametrize('profile, inference', [('skills', False), ('inference', True)])
def test_soak_records_profile_and_first_token_without_retaining_all_tokens(tmp_path, profile, inference):
    from tools import load_ramp as ramp
    args = ramp.arguments(['2', '0.1', 'fixture', '--profile', profile, '--output-dir', str(tmp_path)])
    config, _ = ramp.reserve_run(args)
    config['interval_s'] = 0.01
    seen = []
    def invoke(text, sid, token, is_model):
        seen.append(is_model)
        for _ in range(1000):
            token('chunk')
        return {'reply': 'fixture reply', 'mode': 'fixture'}
    summary = ramp.run_window(config, invoke, lambda: {'ts': time.time()})
    assert summary['turns'] > 4 and not summary['errors']
    assert all(flag == inference for flag in seen)
    assert summary['inference_turns'] == (summary['turns'] if inference else 0)
    rows = [json.loads(line) for line in Path(config['turns_path']).read_text().splitlines()]
    assert len(rows) == summary['turns'] and all(row['first_token'] is not None for row in rows)


def test_mixed_soak_exercises_both_paths(tmp_path):
    from tools import load_ramp as ramp
    args = ramp.arguments(['1', '0.1', 'mix', '--profile', 'mixed', '--output-dir', str(tmp_path)])
    config, _ = ramp.reserve_run(args)
    config['interval_s'] = 0.005
    seen = []
    def invoke(_text, _sid, _token, inference):
        seen.append(inference)
        return {'reply': 'fixture'}
    summary = ramp.run_window(config, invoke, lambda: {'ts': time.time()})
    assert set(seen) == {False, True} and 0 < summary['inference_turns'] < summary['turns']


@pytest.mark.parametrize('hung', [False, True])
def test_supervisor_exits_and_cleans_owned_executor_and_child(tmp_path, monkeypatch, hung):
    from tools import load_ramp as ramp
    helper = tmp_path / 'fixture_worker.py'
    helper.write_text('''import json,sys,time,subprocess,threading,os
from pathlib import Path
c=json.loads(Path(sys.argv[-1]).read_text())
child=None
if HUNG:
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    threading.Thread(target=lambda: time.sleep(30)).start()
Path(c['output_dir']).joinpath('fixture_pids.json').write_text(json.dumps({'worker':os.getpid(),'child':child.pid if child else None}))
Path(c['summary_path']).write_text(json.dumps({'turns':1,'errors':0,'incomplete_requests':0}))
'''.replace('HUNG', str(hung)), encoding='utf-8')
    monkeypatch.setattr(ramp, '__file__', str(helper))
    args = ramp.arguments(['1', '0.1', 'process', '--output-dir', str(tmp_path), '--cleanup-grace', '0.3'])
    config, path = ramp.reserve_run(args)
    started = time.monotonic()
    summary = ramp.supervise(config, path, args)
    assert time.monotonic() - started < 8
    assert summary['forced_cleanup'] == hung
    assert summary['status'] == ('incomplete' if hung else 'completed')
    pids = json.loads((tmp_path / 'fixture_pids.json').read_text())
    assert all(not psutil.pid_exists(pid) for pid in pids.values() if pid)


def test_supervisor_deadline_cleans_worker_without_summary(tmp_path, monkeypatch):
    from tools import load_ramp as ramp
    helper = tmp_path / 'never_ready.py'
    helper.write_text('import time;time.sleep(30)', encoding='utf-8')
    monkeypatch.setattr(ramp, '__file__', str(helper))
    args = ramp.arguments(['1', '0.1', 'timeout', '--output-dir', str(tmp_path),
                           '--cleanup-grace', '0.1', '--startup-timeout', '0.3'])
    config, path = ramp.reserve_run(args)
    summary = ramp.supervise(config, path, args)
    assert summary['timed_out'] and summary['status'] == 'incomplete'
    assert not psutil.pid_exists(summary['worker_pid'])


def gate_rows(growth=0, extra=False):
    return [{'s': {'ts': ts, 'sys': {'used_mb': 1000 + ts * 50},
                   'procs': [{'pid': 7, 'created': 10, 'role': 'app', 'rss_mb': 100 + growth * ts / 60}]
                   + ([{'pid': 9, 'created': 20, 'role': 'other', 'rss_mb': ts * 100}] if extra else [])}}
            for ts in range(0, 241, 2)]


def test_memory_gate_separates_app_from_unrelated_system_growth():
    from tools.qa.ram_gate import evaluate
    result = evaluate(gate_rows(extra=True))
    assert result['status'] == 'PASS' and result['system_context_warning']
    assert [row['pid'] for row in result['processes']] == [7]
    assert evaluate(gate_rows(growth=21))['status'] == 'FAIL'


def test_memory_gate_refuses_short_missing_and_recycled_pid_evidence():
    from tools.qa.ram_gate import evaluate
    assert evaluate([])['status'] == 'UNMEASURED'
    assert evaluate(gate_rows()[:5])['status'] == 'UNMEASURED'
    assert evaluate(gate_rows(), [888])['status'] == 'UNMEASURED'
    rows = gate_rows()
    for row in rows[60:]:
        row['s']['procs'][0]['created'] = 11
    assert evaluate(rows)['status'] == 'UNMEASURED'
    assert evaluate(gate_rows()[::10])['status'] == 'UNMEASURED'
    rows = gate_rows()
    rows[-1]['s']['procs'] = []
    assert evaluate(rows)['status'] == 'UNMEASURED'


def test_attribution_distinguishes_app_model_and_harness():
    from tools.ram_attrib import process_role, system_slopes
    assert process_role('python.exe', ['python', '-m', 'ishti', '8888']) == 'app'
    assert process_role('python.exe', ['python', 'tools/load_ramp.py']) == 'harness'
    assert process_role('python.exe', ['python', 'other/worker.py']) == 'other'
    assert process_role('llama-server.exe', []) == 'model_runtime'
    assert system_slopes([])['status'] == 'UNMEASURED'


def test_analyser_pairs_tags_and_preserves_each_run(tmp_path):
    from tools.analyze_metrics import collect
    for tag, vram in [('first', 111), ('second', 222)]:
        (tmp_path / f'ramp_summary_level_4_{tag}.json').write_text(json.dumps(
            dict(level=4, tag=tag, turns=1, errors=0, latency_avg_s=1, latency_max_s=1,
                 first_token_avg_s=None, status='completed')))
        (tmp_path / f'ramp_level_{tag}.jsonl').write_text(json.dumps(
            dict(ts=1, gpu={'used_mb': vram})) + '\n')
    rows = collect(tmp_path)
    assert {row['tag']: row['vram_peak_mb'] for row in rows} == {'first': 111, 'second': 222}
