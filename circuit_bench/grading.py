"""Grade a saved construction artifact using an explicitly selected product tree.

python -m circuit_bench.grading --task tasks/full-adder --artifact result/design.circ \
    --tree /path/to/fixed/product --out grade.json

No agent or model is started. The submitted directory is copied and reopened in
the verifier host. Expectations come from arithmetic/state transitions, never
from the reference circuits used by the grader's regression tests.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stdout
import hashlib
import itertools
import json
import re
from pathlib import Path
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

from circuit_bench.host import Host, HostError, Tree
from circuit_bench import task_oracles, interrupt_oracle


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def initial_report(task_id=None):
    return {
        'schema_version': 1, 'task_id': task_id, 'status': 'error', 'passed': None,
        'interface': {'status': 'not_run'}, 'function': {'status': 'not_run'},
        'layout': {'status': 'not_evaluated'}, 'provenance': {},
    }


def candidate_load_failure(stage, error):
    """Known document refusals only; runtime/compiler/transport failures stay errors.

    Some native refusals currently have broad host codes. Match the loader's
    concrete diagnostic, not words like 'load', 'dependency', or 'exception'.
    This deliberately does not classify arbitrary Java null-pointer failures.
    """
    if stage not in ('open', 'interface') or not isinstance(error, HostError) or not isinstance(error.error, dict):
        return None
    code, message = error.error.get('code'), error.error.get('message', '')
    if code == 'INVALID_DOCUMENT':
        return 'native_invalid_document'
    if code == 'DEPENDENCY_CHANGED':
        if message.startswith('缺少组件库：') and message.endswith('。需与电路文件放在同一目录。'):
            return 'missing_dependency'
        if message.startswith('Logisim 无法载入这个文件：') and '加载后覆盖不完整：comp ' in message:
            return 'native_component_loss'
    if code == 'UNKNOWN':
        if re.match(r'^电路 "[^"]*" 的 wire #[0-9]+（from=', message) and '：导线必须水平或垂直；' in message:
            return 'invalid_wire_geometry'
    if code == 'INVALID_INPUT':
        if (re.match(r'^电路 "[^"]*" 的 (ROM|RAM) @ ', message)
                and message.endswith(' 的 contents 同时包含 val 属性和文本；原生格式应删除 val 属性并保留文本内容。')):
            return 'invalid_memory_contents'
    return None


def record_error(report, stage, error):
    reason = candidate_load_failure(stage, error)
    if reason:
        report.update(status='invalid_artifact', passed=False, reason=reason)
    else:
        report.update(status='error', passed=None)
    report['error'] = {'stage': stage, 'type': type(error).__name__, 'message': str(error)}
    if isinstance(error, HostError):
        report['error']['host_error'] = error.error
    return report


def full_adder_vectors():
    return [
        {'inputs': {'A': a, 'B': b, 'Cin': cin},
         'expected': {'Sum': (a + b + cin) % 2, 'Cout': (a + b + cin) // 2}}
        for a, b, cin in itertools.product(range(2), repeat=3)
    ]


def full_adder_transitions():
    """Exercise every ordered input pair and holds in one persistent run."""
    combinations = list(itertools.product(range(2), repeat=3))
    states = [(0, 0, 0)]
    for first, second in itertools.product(combinations, repeat=2):
        states.extend((first, second, second))
    events, expected = [], []
    previous = states[0]
    for tick, values in enumerate(states):
        for name, before, after in zip(('A', 'B', 'Cin'), previous, values):
            if before != after:
                events.append({'input': name, 'tick': tick, 'value': after})
        total = sum(values)
        expected.append({'tick': tick, 'values': {'Sum': total % 2, 'Cout': total // 2}})
        previous = values
    return ({'name': 'all-input-transitions-and-holds', 'ticks': len(states) - 1,
             'inputs': {'A': 0, 'B': 0, 'Cin': 0}, 'events': events}, expected)


def counter_expectations(scenario, width=4):
    """Synchronous reset, rising edge only; initial Q is deliberately unspecified.

    Each scenario must establish Q with a reset edge. At most one input changes
    per tick, avoiding dependence on a simulator's same-tick event ordering.
    """
    if width != 4 or set(scenario['inputs']) != {'CLK', 'RST'}:
        raise ValueError('counter4 requires CLK/RST and width=4')
    levels = dict(scenario['inputs'])
    if any(v not in (0, 1) for v in levels.values()):
        raise ValueError('counter stimulus must be binary')
    by_tick = {}
    for event in scenario['events']:
        t, name, value = event['tick'], event['input'], event['value']
        if not 1 <= t <= scenario['ticks'] or t in by_tick or name not in levels or value not in (0, 1):
            raise ValueError('invalid or ambiguous counter stimulus')
        by_tick[t] = event
    q, expected = None, []
    for tick in range(scenario['ticks'] + 1):
        before = levels['CLK']
        if tick in by_tick:
            event = by_tick[tick]
            levels[event['input']] = event['value']
        if before == 0 and levels['CLK'] == 1:
            if levels['RST']:
                q = 0
            elif q is not None:
                q = (q + 1) % (1 << width)
        if q is not None:
            expected.append({'tick': tick, 'values': {'Q': q}})
    if not expected:
        raise ValueError('counter oracle must contain a synchronous reset edge')
    return expected


def interface_check(components, spec):
    pins = []
    for c in components:
        if c.get('factoryName') != 'Pin':
            continue
        attrs = {a['name']: a.get('value', a.get('standard')) for a in c.get('attributes', [])}
        ends = c.get('ends') or []
        pins.append({
            'name': str(attrs.get('label') or ''),
            'direction': 'output' if str(attrs.get('output', 'false')).lower() == 'true' else 'input',
            'width': int(ends[0]['width']) if ends else int(attrs.get('width', 1)),
            'component': c['componentId'],
        })
    wanted = {p['name']: p for p in spec}
    if len(wanted) != len(spec):
        raise ValueError('oracle declares duplicate ports')
    counts = Counter(p['name'] for p in pins)
    failures = []
    for name, count in counts.items():
        if name not in wanted:
            failures.append({'kind': 'unexpected_port', 'port': name})
        if count != 1:
            failures.append({'kind': 'duplicate_port', 'port': name, 'count': count})
    for name, want in wanted.items():
        got = next((p for p in pins if p['name'] == name), None)
        if got is None:
            failures.append({'kind': 'missing_port', 'port': name})
        else:
            for field in ('direction', 'width'):
                if got[field] != want[field]:
                    failures.append({'kind': 'port_' + field, 'port': name, 'expected': want[field], 'actual': got[field]})
    return {'status': 'failed' if failures else 'passed', 'ports': pins, 'failures': failures}


def check_trace(report, scenario, expected, widths=None):
    """Fail closed on missing, short, duplicate, unknown or oscillating samples."""
    widths = widths or {'Q': 4}
    samples = report.get('samples', [])
    failures = []
    ticks = [row.get('tick') for row in samples]
    if report.get('completed') is not True or ticks != list(range(scenario['ticks'] + 1)):
        failures.append({'kind': 'incomplete_trace', 'requested_ticks': scenario['ticks'], 'received_samples': len(samples)})
    if any(row.get('oscillating') is not False for row in samples):
        failures.append({'kind': 'oscillating_or_unknown_trace_state'})
    rows = {row.get('tick'): row for row in samples}
    for point in expected:
        for name, want in point['values'].items():
            signal = (rows.get(point['tick'], {}).get('signals') or {}).get(name) or {}
            bits, value = signal.get('bits'), signal.get('value')
            # The native display may separate bit groups with spaces.
            binary = str(bits).replace(' ', '') if bits is not None else ''
            known = len(binary) == widths[name] and set(binary) <= {'0', '1'}
            if not known or value != want or int(binary, 2) != value:
                kind = 'missing_signal' if not signal else 'unknown_value' if not known else 'value'
                failures.append({'kind': kind, 'tick': point['tick'], 'signal': name,
                                 'expected': want, 'actual': value, 'bits': bits})
    return {'name': scenario['name'], 'status': 'failed' if failures else 'passed',
            'checks': sum(len(p['values']) for p in expected), 'failure_count': len(failures), 'failures': failures[:20]}


class Grader:
    """Reuse a fixed host across artifacts; each artifact gets a fresh copied path.

    with Grader(product_tree) as grader:
        report = grader.grade(task_dir, artifact_path)
    """

    def __init__(self, tree):
        self.tree = Tree.resolve(tree=Path(tree))
        self.host = Host(self.tree)
        self.identity = self.tree.describe()

    def __enter__(self):
        try:
            with redirect_stdout(sys.stderr):
                self.host.start()
            self.identity['host_sha256'] = digest(self.tree.host_binary())
            lock = self.tree.root / 'config/runtime-lock.json'
            self.identity['runtime_lock_sha256'] = digest(lock) if lock.is_file() else None
            self.identity['runtime_files'] = {
                str(p.relative_to(self.tree.root)): digest(p)
                for base in ('.workbench-cache', 'native')
                for p in (self.tree.root / base).glob('*')
                if p.is_file() and p.suffix.lower() in ('.jar', '.exe')
            }
            return self
        except BaseException:
            self.host.close()
            raise

    def __exit__(self, *exc):
        self.host.close()

    def grade(self, task, artifact):
        task, artifact = Path(task).resolve(), Path(artifact).absolute()
        report = initial_report()
        stage, doc = 'task', None
        try:
            metadata = json.loads((task / 'task.json').read_text())
            oracle_path = task / 'tests/oracle.json'
            oracle = json.loads(oracle_path.read_text())
            report['task_id'] = metadata['id']
            if oracle['schema_version'] != 1 or oracle['kind'] not in ('full_adder', 'counter4', 'cpu8_interrupts', *task_oracles.INTERFACES):
                raise ValueError('unsupported oracle')
            report['provenance'] = {
                'product': self.identity, 'task_sha256': digest(task / 'task.json'),
                'oracle_sha256': digest(oracle_path), 'grader_sha256': digest(__file__),
                'task_oracles_sha256': digest(task_oracles.__file__),
                'interrupt_oracle_sha256': digest(interrupt_oracle.__file__),
            }
            stage = 'artifact'
            if not artifact.is_file():
                report.update(status='invalid_artifact', passed=False, reason='artifact_missing')
                return report
            if artifact.is_symlink():
                report.update(status='invalid_artifact', passed=False, reason='artifact_symlink')
                return report
            before = digest(artifact)
            report['provenance']['artifact_sha256'] = before
            try:
                root = ET.parse(artifact).getroot()
            except ET.ParseError as error:
                report.update(status='invalid_artifact', passed=False, reason='invalid_xml', detail=str(error))
                return report
            if root.tag != 'project' or len(root.findall(f"circuit[@name='{metadata['circuit']}']")) != 1:
                report.update(status='invalid_artifact', passed=False, reason='circuit_missing_or_duplicate')
                return report
            # Evaluation owns its copy. Never allow an artifact link to import
            # a verifier-only file or a mutable file outside the saved project.
            if any(p.is_symlink() for p in artifact.parent.rglob('*')):
                report.update(status='invalid_artifact', passed=False, reason='project_contains_symlink')
                return report
            with tempfile.TemporaryDirectory(prefix='construction-grade-') as temp:
                folder = Path(temp) / 'project'
                shutil.copytree(artifact.parent, folder)
                stage = 'open'
                doc = self.host.call('open', path=str(folder / artifact.name))
                args = {'id': doc['id'], 'revision': doc['revision'], 'circuit': metadata['circuit']}
                try:
                    stage = 'interface'
                    focus = self.host.call('observe', **args)['focus']
                    interface = interface_check(focus['components'], oracle['interface'])
                    report['interface'] = interface
                    if interface['status'] == 'failed':
                        report.update(status='failed', passed=False)
                    else:
                        stage = 'function'
                        if oracle['kind'] == 'full_adder':
                            vectors = full_adder_vectors()
                            actual = self.host.call('simulate', **args, vectors=vectors)
                            rows = actual.get('rows', [])
                            if len(rows) != len(vectors):
                                raise ValueError('verifier returned incomplete vector rows')
                            failures = []
                            for index, (row, vector) in enumerate(zip(rows, vectors)):
                                if row.get('index') != index:
                                    raise ValueError('verifier returned out-of-order vector rows')
                                outputs = row.get('outputs') or {}
                                if row.get('oscillating') is not False or any(
                                    outputs.get(name, {}).get('value') != want
                                    for name, want in vector['expected'].items()
                                ):
                                    failures.append({'index': index, **vector, 'actual': outputs})
                            vector_result = {'status': 'failed' if failures else 'passed',
                                'cases': 8, 'checks': 16, 'failure_count': len(failures), 'failures': failures}
                            scenario, expected = full_adder_transitions()
                            actual = self.host.call('trace', **args, ticks=scenario['ticks'],
                                inputs=scenario['inputs'], events=scenario['events'], buttons=[],
                                watches=[{'name': p['name'], 'component': p['component'], 'port': 0}
                                         for p in interface['ports'] if p['direction'] == 'output'])
                            transitions = check_trace(actual, scenario, expected, {'Sum': 1, 'Cout': 1})
                            report['function'] = {
                                'status': 'passed' if not failures and transitions['status'] == 'passed' else 'failed',
                                'checks': 16 + transitions['checks'], 'vectors': vector_result, 'transitions': transitions,
                            }
                        elif oracle['kind'] == 'counter4':
                            output = next(p for p in interface['ports'] if p['name'] == 'Q')
                            cases = []
                            for scenario in oracle['scenarios']:
                                expected = counter_expectations(scenario)
                                actual = self.host.call('trace', **args, ticks=scenario['ticks'],
                                    inputs=scenario['inputs'], events=scenario['events'], buttons=[],
                                    watches=[{'name': 'Q', 'component': output['component'], 'port': 0}])
                                cases.append(check_trace(actual, scenario, expected))
                            if not cases:
                                raise ValueError('counter oracle has no scenarios')
                            report['function'] = {'status': 'passed' if all(c['status'] == 'passed' for c in cases) else 'failed',
                                'cases': cases, 'checks': sum(c['checks'] for c in cases)}
                        else:
                            outputs = [p for p in interface['ports'] if p['direction'] == 'output']
                            widths = {p['name']: p['width'] for p in outputs}
                            groups = []
                            for scenario, expected in task_oracles.cases(oracle):
                                actual = self.host.call('trace', **args, ticks=scenario['ticks'],
                                    inputs=scenario['inputs'], events=scenario['events'], buttons=[],
                                    watches=[{'name': p['name'], 'component': p['component'], 'port': 0}
                                             for p in outputs])
                                groups.append(check_trace(actual, scenario, expected, widths))
                            if not groups:
                                raise ValueError('oracle produced no functional groups')
                            report['function'] = {
                                'status': 'passed' if all(g['status'] == 'passed' for g in groups) else 'failed',
                                'groups': groups, 'checks': sum(g['checks'] for g in groups),
                                'passed_groups': sum(g['status'] == 'passed' for g in groups),
                                'total_groups': len(groups),
                            }
                        report['passed'] = report['function']['status'] == 'passed'
                        report['status'] = 'passed' if report['passed'] else 'failed'
                finally:
                    self.host.call('close', id=doc['id'], revision=doc['revision'], discard=True)
                    doc = None
            if digest(artifact) != before:
                raise RuntimeError('source artifact changed during grading')
            report['provenance']['source_unchanged'] = True
            return report
        except Exception as error:
            return record_error(report, stage, error)


def grade(task, artifact, tree):
    """One-shot API. Infrastructure exceptions become status=error, passed=null."""
    try:
        with Grader(tree) as grader:
            return grader.grade(task, artifact)
    except (Exception, SystemExit) as error:
        return record_error(initial_report(), 'host_start', error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True, type=Path)
    parser.add_argument('--artifact', required=True, type=Path)
    parser.add_argument('--tree', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    report = grade(args.task, args.artifact, args.tree)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    args.out.write_text(payload, encoding='utf-8')
    print(payload, end='')
    return 0 if report['status'] == 'passed' else 2 if report['status'] == 'error' else 1


if __name__ == '__main__':
    raise SystemExit(main())
