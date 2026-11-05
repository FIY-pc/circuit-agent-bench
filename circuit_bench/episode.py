"""Invoke the product's own turn; no benchmark-specific tool loop or prompting."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from circuit_bench.runner import run_episode
from circuit_bench.host import Tree
from circuit_bench.meter import MeterProxy, usage_totals


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest_tree(root):
    """Input/output package identity, including filenames and bytes."""
    root = Path(root)
    h = hashlib.sha256()
    for p in sorted(root.rglob('*')):
        if p.is_symlink():
            raise ValueError('Benchmark input/output packages must contain regular files, not symlinks')
        if p.is_file() and '__pycache__' not in p.parts:
            h.update(p.relative_to(root).as_posix().encode() + b'\0')
            h.update(bytes.fromhex(sha(p)))
    return h.hexdigest()


def source_identity(tree):
    """Record actual tracked bytes, not just HEAD and a dirty boolean."""
    root = tree.root
    proc = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                          capture_output=True)
    if proc.returncode:
        # Read-only source snapshots in sandboxes need no .git history. Their
        # externally prepared identity accompanies the snapshot, not the answer.
        path = os.environ.get('VIBE_BENCH_SOURCE_MANIFEST')
        if not path:
            raise ValueError('A source snapshot requires VIBE_BENCH_SOURCE_MANIFEST')
        identity = json.loads(Path(path).read_text())
        for name, expected in identity.get('files', {}).items():
            path = root / name
            if path.is_symlink() or not path.is_file() or sha(path) != expected:
                raise ValueError('Product snapshot changed: ' + name)
        if identity['binarySha256'] != sha(tree.host_binary()):
            raise ValueError('Snapshot host binary differs from its manifest')
        return identity
    h = hashlib.sha256()
    for name in sorted(set(proc.stdout.split(b'\0')) - {b''}):
        p = root / os.fsdecode(name)
        if p.is_file():
            h.update(name + b'\0' + bytes.fromhex(sha(p)))
    identity = {**tree.describe(), 'sourceSha256': h.hexdigest(),
                'binarySha256': sha(tree.host_binary())}
    for name in ('packages/agent-runtime/src/worker.mjs', 'config/runtime-lock.json', 'package-lock.json'):
        if (root / name).is_file():
            identity[name] = sha(root / name)
    return identity


def execute(task, fixture, instruction, tree, output, *, model, effort, context_window,
            api='openai-completions', max_output=None, isolation='local-development'):
    if api not in ('openai-completions', 'openai-responses', 'anthropic-messages'):
        raise ValueError('Unsupported API format')
    if context_window <= 0:
        raise ValueError('Specify the chosen model context window; no benchmark default')
    if max_output is not None and (type(max_output) is not int or max_output <= 0):
        raise ValueError('max_output must be the model-supported positive output capacity')
    if api == 'anthropic-messages' and max_output is None:
        raise ValueError('Messages requires an explicit model-supported max_output capacity')
    # Pop credentials before starting children. The profile gets only a
    # loopback address and a placeholder. The proxy owns the actual secret.
    base = os.environ.pop('VIBE_BENCH_BASE_URL')
    key = os.environ.pop('VIBE_BENCH_API_KEY')
    for name in list(os.environ):
        if name.startswith('VIBE_TEST_'):
            os.environ.pop(name)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    identity = source_identity(tree)
    frozen_task = {**task, 'prompt': instruction}
    manifest = {'schema': 'circuit-agent-bench.trial/v1', 'task': task['id'],
                'execution': 'real-model', 'isolation': isolation,
                'model': {'requestedId': model, 'effort': effort, 'api': api, 'contextWindow': context_window},
                'harness': identity, 'inputSha256': digest_tree(fixture),
                'taskContractSha256': hashlib.sha256(json.dumps(task, sort_keys=True).encode()).hexdigest(),
                'instructionSha256': hashlib.sha256(instruction.encode()).hexdigest(),
                'adapterSha256': sha(__file__),
                'adapterFiles': {name: sha(Path(__file__).parent / name)
                    for name in ('episode.py','runner.py','host.py','meter.py')},
                'serviceFingerprint': hashlib.sha256(base.rstrip('/').encode()).hexdigest()}
    if max_output is not None:
        manifest['model']['maxOutput'] = max_output
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with MeterProxy(base, key, output / 'request-events.jsonl', api=api) as proxy:
        service = {'id': 'bench', 'name': 'bench', 'baseUrl': proxy.url, 'apiKey': 'local-proxy',
                   'model': model, 'models': [model], 'effort': effort,
                   'contextWindow': context_window, 'api': api, 'vision': True}
        if max_output is not None:
            service['maxOutput'] = max_output
        result = run_episode(frozen_task, Path(fixture), Path(output), tree, service=service)
    output = Path(output)
    rows = sorted(proxy.rows, key=lambda r: r['request'])
    (output / 'requests.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    manifest['model']['reportedIds'] = sorted({r['modelReported'] for r in rows if r.get('modelReported')})
    try:
        manifest['resultSha256'] = digest_tree(output / 'result')
    except ValueError:
        manifest['resultSha256'] = None
        manifest['artifactWarning'] = 'Artifact contains symlinks; verifier must reject it'
    try:
        manifest['harnessUnchanged'] = source_identity(tree) == identity
    except ValueError:
        manifest['harnessUnchanged'] = False
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    result['meter'] = usage_totals(rows)
    # Distinguish agent failures from requests that failed at the provider.
    result['meter']['httpErrors'] = sum(r['status'] is None or r['status'] >= 400 for r in rows)
    result['meter']['transportErrors'] = sum(bool(r.get('transportError')) for r in rows)
    (output / 'run.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task', type=Path, required=True, help='Agent-visible task.json only')
    p.add_argument('--instruction', type=Path, required=True)
    p.add_argument('--input', type=Path, required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--tree')
    group.add_argument('--ref')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--model', default=os.environ.get('VIBE_BENCH_MODEL'), required=not bool(os.environ.get('VIBE_BENCH_MODEL')))
    p.add_argument('--effort', required=True)
    p.add_argument('--context-window', type=int, required=True)
    p.add_argument('--api', default='openai-completions')
    p.add_argument('--max-output', type=int, help='Actual model output capacity; required by Messages, otherwise omitted')
    p.add_argument('--isolation', choices=['local-development', 'sandbox-pilot', 'harbor'], default='local-development')
    a = p.parse_args()
    result = execute(json.loads(a.task.read_text()), a.input, a.instruction.read_text(),
                     Tree.resolve(a.tree, a.ref), a.output, model=a.model, effort=a.effort,
                     context_window=a.context_window, api=a.api, max_output=a.max_output, isolation=a.isolation)
    print(json.dumps({'outcome': result['outcome'], 'cost': result['meter']}, ensure_ascii=False))
    return 0 if result['outcome'] in ('completed', 'timeout', 'interrupted') else 1


if __name__ == '__main__':
    raise SystemExit(main())
