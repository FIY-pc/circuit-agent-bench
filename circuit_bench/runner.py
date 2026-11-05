"""One episode on the vibe-logisim host: a model works on a copy of a task's
fixture through the product's own AI turn.

    run_episode(task, fixture, run_dir, tree, arm=..., model=..., script=...)

Arms:
  tool   the scripted model calls arrange_circuit once (bench/lib/arrange-once.mts):
         the static arranger alone, through the product's real tool path
  bare   the model has only the file tools (read_file, write_file, list_files,
         exec_command) and edits the .circ itself
  full   the model with every tool

A model service comes from VIBE_BENCH_BASE_URL / VIBE_BENCH_API_KEY /
VIBE_BENCH_MODEL (or --model); `script` replaces it with the scripted model
of the vibe-logisim tree running that script. The key is written only into
the episode's temporary profile.

run_dir receives:
  setup.json         task, arm, model, effort, the implementation (commit, ref, dirty)
  result/            the folder as the turn left it
  events.jsonl       every host event of the episode, with its arrival time
  run.json           outcome, elapsedMs, error, final, ledger
  tool-calls.jsonl   one row per tool call; commands.jsonl one per shell command
  host.log           the host's and the sidecar's output (model.log: scripted model)
  last-request.json  scripted model only: the newest request it received
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH / 'scoring'))
from circuit_bench.host import Host, Tree   # noqa: E402

FILE_TOOLS = {'read_file', 'write_file', 'list_files', 'exec_command'}
ARMS = ('tool', 'bare', 'full')


def bare_model(name):
    """The model id without a service's vendor prefix."""
    return (name or '').rsplit('/', 1)[-1] or None


def _profile(state: Path, service: dict):
    profile = state / 'profile'
    profile.mkdir(parents=True, exist_ok=True)
    f = profile / 'builtin-provider.json'
    f.write_text(json.dumps({'version': 2, 'defaultId': service['id'], 'services': [service]}, ensure_ascii=False), encoding='utf-8')
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass


def _service(model, effort, api):
    base = os.environ.get('VIBE_BENCH_BASE_URL')
    key = os.environ.get('VIBE_BENCH_API_KEY')
    model = model or os.environ.get('VIBE_BENCH_MODEL')
    if not (base and key and model):
        raise SystemExit('真实模型需要 VIBE_BENCH_BASE_URL、VIBE_BENCH_API_KEY 和 VIBE_BENCH_MODEL（或 --model）；'
                         '只想试通路用 --script')
    return {'id': 'bench', 'name': 'bench', 'baseUrl': base.rstrip('/'), 'apiKey': key, 'model': model, 'models': [model],
            'effort': effort or 'medium', 'contextWindow': 256000, 'api': api or 'openai-completions', 'vision': True}


class ScriptedModel:
    """scripts/workbench/scripted-model.mts of the tree, on a free port."""

    LINE = re.compile(r'scripted model on (http://\S+/v1)')

    def __init__(self, tree: Tree, script: Path, log: Path, env: dict):
        node = shutil.which('node') or 'node'
        self.log = log.open('a', encoding='utf-8')
        self.proc = subprocess.Popen([node, str(tree.root / 'scripts/workbench/scripted-model.mts'), '--port', '0', '--script', str(script)],
                                     cwd=str(tree.root), env={**os.environ, **env}, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        found = threading.Event()
        self.url = None

        def pump():
            for line in self.proc.stdout:
                self.log.write(line)
                self.log.flush()
                m = self.LINE.search(line)
                if m and not self.url:
                    self.url = m.group(1)
                    found.set()
            found.set()
        threading.Thread(target=pump, daemon=True).start()
        found.wait(60)
        if not self.url:
            self.close()
            raise RuntimeError(f'脚本模型没有起来，见 {log}')

    def last_request(self):
        with urllib.request.urlopen(self.url.rsplit('/v1', 1)[0] + '/fixture/last-request', timeout=30) as r:
            return json.loads(r.read().decode('utf-8'))

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.log.close()


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


def run_episode(task, fixture: Path, run_dir: Path, tree: Tree, *, arm='full', model=None, effort=None, api=None,
                script=None, timeout_min=None, service=None):
    if arm not in ARMS:
        raise SystemExit(f'--arm 只能是 {", ".join(ARMS)}')
    if arm == 'tool' and not script:
        script = BENCH / 'lib' / 'arrange-once.mts'
    run_dir.mkdir(parents=True, exist_ok=False)
    work = Path(tempfile.mkdtemp(prefix='vibe-bench-'))
    folder, state = work / 'project', work / 'state'
    shutil.copytree(fixture, folder)
    panel = (task.get('correctness') or {}).get('panel_fixed')
    script_env = {'VIBE_BENCH_ARTIFACT': task['artifact'], 'VIBE_BENCH_CIRCUIT': task['circuit']}
    if panel:
        script_env['VIBE_BENCH_PANEL_BELOW_Y'] = str(panel['below_y'])
    scripted = None
    if script:
        scripted = ScriptedModel(tree, Path(script).resolve(), run_dir / 'model.log', script_env)
        service = {'id': 'scripted', 'name': 'scripted', 'baseUrl': scripted.url, 'apiKey': 'fixture', 'model': 'fixture/tool-model',
                   'models': ['fixture/tool-model'], 'effort': 'none', 'contextWindow': 256000, 'api': 'openai-completions', 'vision': True}
        model_name = 'scripted:' + Path(script).stem
    else:
        service = service or _service(model, effort, api)
        model_name = bare_model(service['model'])
    _profile(state, service)
    setup = {'task': task['id'], 'arm': arm, 'model': model_name, 'effort': None if script else service['effort'],
             'api': service['api'], 'script': Path(script).name if script else None, 'vibeLogisim': tree.describe(),
             'tree': str(tree.root), 'started': time.strftime('%Y-%m-%dT%H:%M:%S')}
    (run_dir / 'setup.json').write_text(json.dumps(setup, ensure_ascii=False, indent=1), encoding='utf-8')
    env = {'VIBE_LOGISIM_TOOLSET': 'files'} if arm == 'bare' else {}
    timeout = 60 * (timeout_min or task.get('timeout_minutes') or 30)
    outcome, error, final, usage = 'error', None, None, None
    t0 = time.time()
    host, stream = Host(tree, state=state, env=env, log=run_dir / 'host.log'), None
    try:
        host.start()
        stream = host.events()
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
        if scripted:
            try:
                last = scripted.last_request()
                (run_dir / 'last-request.json').write_text(json.dumps(last, ensure_ascii=False), encoding='utf-8')
                setup['requestTools'] = [t.get('function', {}).get('name') or t.get('name') for t in (last or {}).get('tools') or []]
            except Exception as e:  # noqa: BLE001 - recorded, not fatal
                setup['requestToolsError'] = str(e)[:200]
    except Exception as e:  # noqa: BLE001 - the episode failed; record why
        error = f'{type(e).__name__}: {e}'[:600]
    finally:
        elapsed = int((time.time() - t0) * 1000)
        host.close()
        if scripted:
            scripted.close()
    stamped = [{'t': t, 'event': ev} for t, ev in zip(stream.times, stream.events)] if stream else []
    with (run_dir / 'events.jsonl').open('w', encoding='utf-8') as f:
        for row in stamped:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    ledger, rows = _ledger(stamped, usage)
    with (run_dir / 'tool-calls.jsonl').open('w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    with (run_dir / 'commands.jsonl').open('w', encoding='utf-8') as f:
        for r in rows:
            if r['tool'] == 'exec_command':
                f.write(json.dumps({'command': r.get('target'), 'exitCode': r.get('exitCode'), 'status': r.get('status')}, ensure_ascii=False) + '\n')
    shutil.copytree(folder, run_dir / 'result')
    shutil.rmtree(work, ignore_errors=True)
    run = {'outcome': outcome, 'elapsedMs': elapsed, 'error': error, 'final': final, 'ledger': ledger}
    (run_dir / 'run.json').write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding='utf-8')
    (run_dir / 'setup.json').write_text(json.dumps(setup, ensure_ascii=False, indent=1), encoding='utf-8')
    return run
