"""Run one unmodified product turn on a fresh task copy.

This adapter supplies neither tools nor an agent loop. The product owns its
prompt, tools, retries and compaction. Model credentials never enter profiles.
"""
from __future__ import annotations
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from circuit_bench.host import Host, Tree

FILE_TOOLS = {'read_file', 'write_file', 'list_files', 'exec_command'}

def _ledger(events, usage):
    """Tool calls from the activity events: the last state of each call."""
    calls, order = {}, []
    for row in events:
        e = (row['event'].get('data') or {}).get('event') or {}
        if row['event'].get('topic') != 'agent' or e.get('type') != 'activity' or e.get('kind') != 'tool':
            continue
        cid = e.get('itemId') or e.get('id')
        if cid not in calls:
            order.append(cid)
            calls[cid] = {'tool': (e.get('activityKey') or '').removeprefix('tool:'), 'started': row['t']}
        c = calls[cid]
        c['status'] = e.get('status')
        out = e.get('toolOutput') or {}
        c['target'] = out.get('target') or c.get('target')
        if out.get('exitCode') is not None:
            c['exitCode'] = out['exitCode']
        if e.get('status') != 'running':
            c['ms'] = round((row['t'] - c['started']) * 1000)
    rows = [calls[i] for i in order]
    for r in rows:
        r.pop('started', None)
    names = [r['tool'] for r in rows]
    ledger = {
        'toolCalls': len(rows),
        'circuitToolCalls': sum(1 for n in names if n not in FILE_TOOLS),
        'failedCalls': sum(1 for r in rows if r.get('status') == 'failed'),
        'commandFailures': sum(1 for r in rows if r['tool'] == 'exec_command' and r.get('status') == 'failed'),
        'visualObservationCalls': names.count('render_circuit'),
        'verificationToolCalls': names.count('simulate_circuit') + names.count('trace_circuit'),
        'tokenUsage': usage,
        'peakRequestInputTokens': (usage or {}).get('peakInput'),
    }
    return ledger, rows


def run_episode(task, fixture: Path, run_dir: Path, tree: Tree, *, service):
    run_dir.mkdir(parents=True, exist_ok=True)
    if (run_dir / 'run.json').exists():
        raise ValueError('Trial output already contains a run')
    work = Path(tempfile.mkdtemp(prefix='circuit-trial-'))
    folder, state = work / 'project', work / 'state'
    shutil.copytree(fixture, folder)
    profile = state / 'profile'
    profile.mkdir(parents=True)
    if service['apiKey'] != 'local-proxy':
        raise ValueError('Product profile must use the in-memory proxy')
    profile_file = profile / 'builtin-provider.json'
    profile_file.write_text(json.dumps({'version': 2, 'defaultId': service['id'], 'services': [service]}))
    profile_file.chmod(0o600)
    setup = {'task': task['id'], 'model': service['model'], 'effort': service['effort'],
             'api': service['api'], 'vibeLogisim': tree.describe(),
             'started': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    (run_dir / 'setup.json').write_text(json.dumps(setup, ensure_ascii=False, indent=2))
    timeout = 60 * task['timeout_minutes']
    outcome, error, final, usage = 'error', None, None, None
    t0 = time.time()
    host, stream = Host(tree, state=state, log=run_dir / 'host.log'), None
    try:
        host.start()
        stream = host.events(run_dir / 'events.jsonl')
        if not stream.wait(lambda e: e.get('topic') == 'resync', 30):
            raise RuntimeError('宿主事件流没有就绪')
        host.call('folderOpen', path=str(folder))
        doc = host.call('open', path=str(folder / task['artifact']))
        host.agent('bind', documentId=doc['id'])
        mark = len(stream.events)
        t0 = time.time()
        turn = host.agent('ask', question=task['prompt'], references=[], documentId=doc['id'],
                          circuit=task['circuit'], selection=[])['turnId']

        def done(e):
            x = (e.get('data') or {}).get('event') or {}
            return e.get('topic') == 'agent' and x.get('type') == 'turn-completed' and x.get('turnId') == turn
        end = stream.wait(done, timeout, mark)
        if end is None:
            outcome = 'timeout'
            try:
                host.agent('stop', timeout=60)
            except Exception:  # noqa: BLE001 - the turn may have just ended
                pass
            end = stream.wait(done, 120, mark)
        if end is not None:
            x = end['data']['event']
            usage = x.get('usage')
            if outcome != 'timeout':
                outcome = {'completed': 'completed', 'interrupted': 'interrupted'}.get(x.get('status'), 'error')
            error = x.get('error') or None
        for ev in stream.events[mark:]:
            x = (ev.get('data') or {}).get('event') or {}
            if ev.get('topic') != 'agent':
                continue
            if x.get('type') == 'assistant-completed' and x.get('phase') == 'final' and x.get('text'):
                final = x['text']
            if x.get('type') == 'runtime-error':
                error = error or x.get('message')
    except Exception as e:  # noqa: BLE001 - the episode failed; record why
        error = f'{type(e).__name__}: {e}'[:600]
    finally:
        elapsed = int((time.time() - t0) * 1000)
        host.close()
    stamped = [{'t': t, 'event': ev} for t, ev in zip(stream.times, stream.events)] if stream else []
    ledger, rows = _ledger(stamped, usage)
    with (run_dir / 'tool-calls.jsonl').open('w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    with (run_dir / 'commands.jsonl').open('w', encoding='utf-8') as f:
        for r in rows:
            if r['tool'] == 'exec_command':
                f.write(json.dumps({'command': r.get('target'), 'exitCode': r.get('exitCode'), 'status': r.get('status')}, ensure_ascii=False) + '\n')
    shutil.copytree(folder, run_dir / 'result', symlinks=True)
    shutil.rmtree(work, ignore_errors=True)
    run = {'outcome': outcome, 'elapsedMs': elapsed, 'error': error, 'final': final, 'ledger': ledger}
    (run_dir / 'run.json').write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding='utf-8')
    (run_dir / 'setup.json').write_text(json.dumps(setup, ensure_ascii=False, indent=1), encoding='utf-8')
    return run
