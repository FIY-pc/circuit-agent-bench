"""Supervise the stock CLI and collect evidence, without changing its agent loop."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

from . import configuration
from circuit_bench.identity import sha, digest_tree
from circuit_bench.meter import MeterProxy, usage_totals

CLI = Path('/opt/pi/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js')


def write_json(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temp.replace(path)


def records(path):
    if path.exists():
        with path.open(encoding='utf-8') as stream:
            for line in stream:  # JSONL splits on LF, not Unicode line separators
                try:
                    yield json.loads(line)
                except ValueError:
                    continue  # interrupted final record; original bytes stay available


def model_registry(config, url):
    model = {'id': config['id'], 'name': config['id'],
             'contextWindow': config['context_window'], 'maxTokens': config['max_output'],
             'reasoning': config['effort'] not in ('off', 'none'),
             'input': ['text', 'image'] if config['vision'] else ['text']}
    for public, native in [('compat', 'compat'), ('thinking_level_map', 'thinkingLevelMap')]:
        if public in config:
            model[native] = config[public]
    return {'providers': {'benchmark': {'api': config['api'], 'baseUrl': url,
            'apiKey': 'local-proxy', 'models': [model]}}}


def identity(runtime=Path('/opt/pi'), manifest=Path('/opt/pi-source.json')):
    data = json.loads(manifest.read_text())
    for name, expected in data['files'].items():
        path = runtime / name
        if not path.is_file() or path.is_symlink() or sha(path) != expected:
            raise ValueError('Pi installation differs from the prepared image')
    if sha(Path(shutil.which('node'))) != data['nodeSha256']:
        raise ValueError('Node differs from the prepared image')
    for name, expected in data.get('runtimeFiles', {}).items():
        if sha(Path(name)) != expected:
            raise ValueError('Simulator differs from the prepared image')
    return {k: v for k, v in data.items() if k != 'files'}


def process_identity(pid):
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return None if stat[0] == 'Z' else stat[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def stop(process):
    # Pi's bash descendants inherit the process group; stop those too.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def summarize(output):
    calls, final, error, settled, retries, compactions = {}, '', None, False, 0, 0
    for event in records(output / 'events.jsonl'):
        kind = event.get('type')
        if kind == 'tool_execution_start':
            calls[event['toolCallId']] = {'id': event['toolCallId'], 'tool': event['toolName'],
                                         'args': event.get('args'), 'status': 'interrupted'}
        elif kind == 'tool_execution_end':
            call = calls.setdefault(event['toolCallId'], {'id': event['toolCallId'], 'tool': event['toolName']})
            call.update(status='failed' if event.get('isError') else 'completed', durationMs=event.get('durationMs'))
        elif kind == 'message_end' and event.get('message', {}).get('role') == 'assistant':
            message = event['message']
            final = ''.join(c.get('text', '') for c in message.get('content', []) if c.get('type') == 'text')
            error = message.get('errorMessage') if message.get('stopReason') in ('error', 'aborted') else None
        elif kind == 'agent_settled':
            settled = not event.get('aborted')
        elif kind in ('auto_retry_start', 'summarization_retry_attempt_start'):
            retries += 1
        elif kind == 'compaction_start':
            compactions += 1
    (output / 'tool-calls.jsonl').write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in calls.values()))
    ledger = {'toolCalls': len(calls), 'failedCalls': sum(c['status'] == 'failed' for c in calls.values()),
              'commandFailures': sum(c['tool'] == 'bash' and c['status'] == 'failed' for c in calls.values()),
              'interruptedCalls': sum(c['status'] == 'interrupted' for c in calls.values()),
              'retries': retries, 'compactions': compactions}
    return ledger, final, error, settled


def finalize(output, outcome, *, error=None, exit_code=None):
    state = json.loads((output / 'process.json').read_text())
    manifest = json.loads((output / 'manifest.json').read_text())
    rows = {}
    for event in records(output / 'request-events.jsonl'):
        if isinstance(event.get('request'), int):
            rows[event['request']] = {k: v for k, v in event.items() if k != 'event'}
    requests = sorted(rows.values(), key=lambda r: r['request'])
    (output / 'requests.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in requests))
    meter = usage_totals(requests)
    meter.update(httpErrors=sum(r['status'] is None or r['status'] >= 400 for r in requests),
                 transportErrors=sum(bool(r.get('transportError')) for r in requests))
    ledger, final, agent_error, settled = summarize(output)
    if outcome == 'completed' and (exit_code != 0 or agent_error or not settled):
        outcome = 'error'
    retention_errors = []
    try:
        manifest['harnessUnchanged'] = identity() == manifest['harness']
    except (OSError, ValueError):
        manifest['harnessUnchanged'] = False
    manifest['model']['reportedIds'] = sorted({r['modelReported'] for r in requests if r.get('modelReported')})
    project = Path(state['project'])
    try:
        manifest['resultSha256'] = digest_tree(project)
        if not (output / 'result').exists():
            shutil.copytree(project, output / 'result', symlinks=True)
    except (OSError, ValueError):
        manifest['resultSha256'] = None
        retention_errors.append('artifact_copy_or_digest_failed')
    write_json(output / 'manifest.json', manifest)
    result = {'outcome': outcome, 'finalized': True, 'elapsedMs': round((time.monotonic() - state['started']) * 1000),
              'final': final, 'error': error or agent_error, 'exitCode': exit_code,
              'ledger': ledger, 'meter': meter, 'retentionErrors': retention_errors}
    write_json(output / 'run.json', result)
    return result


def interrupt(output):
    if not (output / 'process.json').exists():
        return  # startup failed before a process was launched
    run = output / 'run.json'
    if run.exists() and json.loads(run.read_text()).get('finalized'):
        return
    state = json.loads((output / 'process.json').read_text())
    if state['start'] and process_identity(state['pid']) == state['start']:
        os.kill(state['pid'], signal.SIGTERM)
        deadline = time.monotonic() + 25
        while process_identity(state['pid']) == state['start'] and time.monotonic() < deadline:
            time.sleep(.2)
        if process_identity(state['pid']) == state['start']:
            os.kill(state['pid'], signal.SIGKILL)
    child = state.get('child')
    if child and child['start'] and process_identity(child['pid']) == child['start']:
        try:
            os.killpg(child['pid'], signal.SIGKILL)
        except ProcessLookupError:
            pass
    if not run.exists() or not json.loads(run.read_text()).get('finalized'):
        finalize(output, 'interrupted', error='Supervisor interrupted; request usage may be incomplete')


def execute(task, instruction, fixture, output, project, config):
    config = configuration(config)
    base = os.environ.pop('CIRCUIT_BENCH_BASE_URL')
    key = os.environ.pop('CIRCUIT_BENCH_API_KEY')
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(fixture, project)  # live artifacts survive even a forced kill
    native = output / 'trajectory'; native.mkdir()
    agent_dir = output / 'pi-config'; agent_dir.mkdir()
    write_json(agent_dir / 'settings.json', {'enableInstallTelemetry': False})
    manifest = {'schema': 'circuit-agent-bench.trial/v1', 'task': task['id'], 'execution': 'real-model',
        'adapter': 'pi', 'isolation': 'harbor', 'harness': identity(),
        'model': {'requestedId': config['id'], 'effort': config['effort'], 'api': config['api'],
                  'contextWindow': config['context_window'], 'maxOutput': config['max_output'], 'vision': config['vision']},
        'limits': {'timeoutMinutes': task['timeout_minutes']}, 'inputSha256': digest_tree(fixture),
        'instructionSha256': hashlib.sha256(instruction.encode()).hexdigest(),
        'taskContractSha256': hashlib.sha256(json.dumps(task, sort_keys=True).encode()).hexdigest(),
        'serviceFingerprint': hashlib.sha256(base.rstrip('/').encode()).hexdigest(),
        'adapterFiles': {name: sha(Path(__file__).parent / name) for name in ('__init__.py', 'episode.py')},
        'meterSha256': sha(Path(__file__).parents[2] / 'meter.py'), 'configuration': config}
    write_json(output / 'manifest.json', manifest)
    state = {'pid': os.getpid(), 'start': process_identity(os.getpid()), 'started': time.monotonic(), 'project': str(project)}
    write_json(output / 'process.json', state)
    def cancelled(*_):
        raise InterruptedError()
    old_signals = {s: signal.signal(s, cancelled) for s in (signal.SIGTERM, signal.SIGINT)}
    outcome, error, process, exit_code = 'completed', None, None, None
    try:
        with MeterProxy(base, key, output / 'request-events.jsonl', api=config['api']) as proxy:
            write_json(agent_dir / 'models.json', model_registry(config, proxy.url))
            command = ['node', str(CLI), '--offline', '--mode', 'json', '--provider', 'benchmark',
                       '--model', config['id'], '--thinking', 'off' if config['effort'] == 'none' else config['effort'], '--session-dir', str(native),
                       '--no-extensions', '--no-mcp', '--no-skills', '--no-prompt-templates', '--no-themes',
                       '--no-context-files', '--no-approve', '--', instruction]
            # No provider credential or user HOME/config is inherited by Pi or its tools.
            env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'LC_ALL', 'TERM')}
            home = output / 'home'; home.mkdir()
            env.update(HOME=str(home), PI_CODING_AGENT_DIR=str(agent_dir), PI_OFFLINE='1')
            with (output / 'events.jsonl').open('wb') as events, (output / 'stderr.log').open('wb') as stderr:
                process = subprocess.Popen(command, cwd=project, env=env, stdin=subprocess.DEVNULL,
                                           stdout=events, stderr=stderr, start_new_session=True)
                state['child'] = {'pid': process.pid, 'start': process_identity(process.pid)}
                write_json(output / 'process.json', state)
                try:
                    exit_code = process.wait(timeout=task['timeout_minutes'] * 60)
                except subprocess.TimeoutExpired:
                    outcome = 'timeout'
                except InterruptedError:
                    outcome = 'interrupted'
                finally:
                    for sig in old_signals:
                        signal.signal(sig, signal.SIG_IGN)
                    stop(process)
    except InterruptedError:
        outcome = 'interrupted'
    except Exception as exc:
        outcome, error = 'error', type(exc).__name__
    finally:
        for sig in old_signals:
            signal.signal(sig, signal.SIG_IGN)
        if process is not None and process.poll() is None:
            stop(process)
        result = finalize(output, outcome, error=error, exit_code=exit_code)
        for sig, handler in old_signals.items():
            signal.signal(sig, handler)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interrupt', type=Path)
    for option in ('task', 'instruction', 'input', 'output', 'project', 'model-config'):
        parser.add_argument('--' + option, type=Path)
    args = parser.parse_args()
    if args.interrupt:
        interrupt(args.interrupt)
        return
    if any(getattr(args, name) is None for name in ('task', 'instruction', 'input', 'output', 'project', 'model_config')):
        parser.error('Supply all episode paths, or --interrupt')
    result = execute(json.loads(args.task.read_text()), args.instruction.read_text(), args.input,
                     args.output.resolve(), args.project.resolve(), json.loads(args.model_config.read_text()))
    print(json.dumps({'outcome': result['outcome'], 'meter': result['meter']}))


if __name__ == '__main__':
    main()
