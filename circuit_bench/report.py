"""Aggregate every attempt into a shareable, allowlisted JSON and HTML report."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import html
import itertools
import json
import math
from pathlib import Path
import re

from .review import AXES, load_evidence, review_coverage, validate_review
from .visual import digest, write_json

SCHEMA = 'circuit-bench.report/v1'
METRICS = ('input', 'output', 'cachedInput', 'reasoningOutput', 'requests', 'requestsWithUsage',
           'httpErrors', 'requestsWithoutUsage', 'toolCalls', 'failedCalls', 'commandFailures', 'elapsedMs',
           'circuitToolCalls', 'visualObservationCalls', 'verificationToolCalls',
           'peakRequestInputTokens', 'batchSuboperations', 'retries')
OUTCOMES = ('completed', 'timeout', 'error', 'interrupted', 'unknown')
FUNCTIONAL = ('passed', 'failed', 'invalid_artifact', 'evaluator_error', 'unknown')


def _identity(value, prefix):
    if value is None or value == {}:
        return 'unknown'
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    return prefix + '-' + hashlib.sha256(encoded).hexdigest()[:16]


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def _label(value, prefix):
    # Task/family identifiers are benchmark contracts, never arbitrary text.
    if isinstance(value, str) and re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', value):
        return value
    return _identity(value, prefix)


def _read(path, problems, label):
    if not path.is_file():
        problems.append(label + '_missing')
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('not_an_object')
        return data
    except (ValueError, OSError):
        problems.append(label + '_unreadable')
        return {}


def _metric_summary(rows):
    result = {}
    for metric in METRICS:
        values = [r['metrics'][metric] for r in rows if r['metrics'][metric] is not None]
        result[metric] = {'knownSum': sum(values) if values else None, 'knownAttempts': len(values),
                          'unknownAttempts': len(rows)-len(values)}
    return result


def _group(rows, fields):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[k] for k in fields)].append(row)
    result = []
    for key, items in sorted(groups.items()):
        functional = Counter(r['functional'] for r in items)
        result.append({**dict(zip(fields, key)), 'attempts': len(items),
                       'functional': {s: functional[s] for s in FUNCTIONAL},
                       'passFractionAllAttempts': functional['passed']/len(items),
                       'assessmentExcludedAttempts': sum(r['excludedFromCapabilityComparison'] for r in items),
                       'outcomes': dict(Counter(r['outcome'] for r in items)), 'metrics': _metric_summary(items),
                       'visualAxes': {axis: dict(Counter(r['visual']['axes'][axis] for r in items)) for axis in AXES}})
    return result


def _functional(grade):
    status = grade.get('status')
    # No completion flag or scalar reward substitutes for independent grading.
    if status == 'passed' and grade.get('passed') is True:
        return 'passed'
    if status in ('failed', 'invalid_artifact') and grade.get('passed') is False:
        return status
    if status in ('error', 'evaluator_error'):
        return 'evaluator_error'
    return 'unknown'


def _visual(run_dir, problems, grade, evidence_path=None):
    result = {'collection': 'unknown', 'review': 'unknown', 'axes': {a: 'unknown' for a in AXES},
              'capturedViews': None, 'viewedViews': None, 'unviewedViews': None, 'omittedTiles': None,
              'humanMinutes': None, 'collectionMs': None, 'unresolvedDependencies': None, 'observationUnknowns': None}
    path = evidence_path or run_dir / 'visual' / 'evidence.json'
    if not path.is_file():
        return result
    try:
        evidence = load_evidence(path)
        result.update(collection=evidence['status'] if evidence['status'] in ('complete', 'partial', 'error') else 'unknown',
                      capturedViews=_number(evidence['coverage'].get('capturedViews')),
                      omittedTiles=_number(evidence['coverage'].get('omittedTiles')),
                      collectionMs=_number(evidence.get('elapsedMs')),
                      unresolvedDependencies=len(evidence.get('unresolvedDependencies', [])),
                      observationUnknowns=sum(len(d.get('observationUnknowns', [])) for d in evidence['definitions']))
        provenance = grade.get('provenance') if isinstance(grade.get('provenance'), dict) else {}
        expected = provenance.get('artifact_sha256')
        if expected and expected != evidence['provenance']['artifactSha256']:
            raise ValueError('grade_visual_artifact_mismatch')
        if evidence['provenance'].get('sourceUnchanged') is not True or evidence['provenance'].get('packageUnchanged') is not True:
            raise ValueError('visual_source_not_verified')
        result['viewedViews'] = 0
        result['unviewedViews'] = result['capturedViews']
        review_path = path.parent / 'review.json'
        if not review_path.is_file():
            return result
        review = validate_review(json.loads(review_path.read_text()), evidence, digest(path))
        coverage = review_coverage(review, evidence)
        result.update(review='human', viewedViews=coverage['viewed'], unviewedViews=len(coverage['unviewedViewIds']),
                      humanMinutes=_number(review.get('humanMinutes')),
                      axes={a: review['rubric'][a]['state'] for a in AXES})
        result['readerProbes'] = dict(Counter(p['outcome'] for p in review.get('readerProbes', [])))
        result['correctProbeSeconds'] = [p['seconds'] for p in review.get('readerProbes', [])
                                         if p['outcome'] == 'correct' and _number(p.get('seconds')) is not None]
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        problems.append('visual_evidence_or_review_invalid')
        result.update(review='invalid', axes={a: 'unknown' for a in AXES})
    return result


def observed_usage(run_dir, meter, problems):
    keys = ('input', 'output', 'cachedInput', 'reasoningOutput')
    observed = meter.get('observedTokens') if isinstance(meter.get('observedTokens'), dict) else {}
    values = {key: _number(observed.get(key)) for key in keys}
    if any(value is not None for value in values.values()):
        return values, 'meter'
    # Older interrupted runs may have only per-request observations. Normalize
    # each independently through the meter; never overwrite incomplete totals.
    path = run_dir / 'requests.jsonl'
    if not path.is_file():
        return values, 'unknown'
    from .meter import usage_totals
    parts = {key: [] for key in keys}
    try:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            normalized = usage_totals([row])
            for key in keys:
                value = _number(normalized.get(key))
                if value is not None:
                    parts[key].append(value)
    except (OSError, ValueError, TypeError, AttributeError):
        problems.append('requests_unreadable_lower_bound_partial')
    return {key: sum(items) if items else None for key, items in parts.items()}, 'requests'


def read_attempt(run_dir, ordinal, tasks=None, context=None):
    run_dir = Path(run_dir)
    context = context or {}
    problems = list(context.get('problems', []))
    manifest = _read(run_dir / 'manifest.json', problems, 'manifest')
    run = _read(run_dir / 'run.json', problems, 'run')
    grade = _read(context.get('gradePath', run_dir / 'grade.json'), problems, 'grade')
    task = context.get('task') or manifest.get('task') or grade.get('task_id')
    if isinstance(task, dict):
        task = task.get('id')
    metadata = (tasks or {}).get(task, {}) if isinstance(task, str) else {}
    meter = run.get('meter') if isinstance(run.get('meter'), dict) else {}
    ledger = run.get('ledger') if isinstance(run.get('ledger'), dict) else {}
    metrics = {k: _number(meter.get(k)) for k in METRICS}
    metrics.update({k: _number(ledger.get(k)) for k in ('toolCalls', 'failedCalls', 'commandFailures', 'circuitToolCalls',
                    'visualObservationCalls', 'verificationToolCalls', 'peakRequestInputTokens', 'batchSuboperations', 'retries')})
    metrics['elapsedMs'] = _number(run.get('elapsedMs'))
    observed_tokens, observed_source = observed_usage(run_dir, meter, problems)
    if metrics['requestsWithoutUsage'] is None and metrics['requests'] is not None and metrics['requestsWithUsage'] is not None:
        metrics['requestsWithoutUsage'] = _number(metrics['requests']-metrics['requestsWithUsage'])
    model = manifest.get('model') if isinstance(manifest.get('model'), dict) else {}
    harness = manifest.get('harness') if isinstance(manifest.get('harness'), dict) else {}
    tool_counts = Counter()
    calls_path = run_dir / 'tool-calls.jsonl'
    if calls_path.is_file():
        try:
            for line in calls_path.read_text(encoding='utf-8').splitlines():
                call = json.loads(line)
                # Tool names are identifiers; commands, arguments and outputs never enter this report.
                name = _label(call.get('tool'), 'tool')
                tool_counts[name] += 1
        except (OSError, ValueError, AttributeError):
            problems.append('tool_ledger_unreadable')
    row = {'attempt': f'attempt-{ordinal:04}', 'task': _label(task, 'task'),
           'family': _label(manifest.get('family') or context.get('family') or metadata.get('family'), 'family'),
           'model': _identity(model, 'model'), 'harness': _identity(harness, 'harness'),
           'outcome': run.get('outcome') if run.get('outcome') in OUTCOMES else 'unknown',
           'functional': _functional(grade), 'metrics': metrics, 'observedTokens': observed_tokens, 'observedTokensSource': observed_source, 'toolsByName': dict(sorted(tool_counts.items())),
           'execution': manifest.get('execution') if manifest.get('execution') in ('real-model', 'scripted', 'synthetic') else 'unknown',
           'harnessUnchanged': manifest.get('harnessUnchanged') if isinstance(manifest.get('harnessUnchanged'), bool) else None,
           'visual': _visual(run_dir, problems, grade, context.get('evidencePath')), 'unknowns': problems,
           'experiment': context.get('experiment', 'unknown'), 'job': _label(context.get('job'), 'job'),
           'candidate': _label(context.get('candidate'), 'candidate'), 'repeat': _number(context.get('repeat')),
           'trial': _label(context.get('trial'), 'trial'), 'infrastructure': context.get('infrastructure', 'not_recorded'),
           'launchExitCode': context.get('launchExitCode')}
    if row['outcome'] == 'unknown' and row['infrastructure'] in ('harbor_exception', 'launcher_failed'):
        row['outcome'] = 'error'
    provenance = grade.get('provenance') if isinstance(grade.get('provenance'), dict) else {}
    evaluator = {k: provenance.get(k) for k in ('product', 'grader_sha256', 'oracle_sha256', 'task_sha256')}
    row['evaluator'] = _identity(evaluator, 'evaluator') if all(evaluator.values()) else 'unknown'
    row['pair'] = _identity(manifest.get('pairId'), 'pair')
    # Pair only preregistered repetition IDs with matching full conditions.
    conditions = {'task': task, 'model': model, 'input': manifest.get('inputSha256'),
                  'instruction': manifest.get('instructionSha256'), 'contract': manifest.get('taskContractSha256'),
                  'isolation': manifest.get('isolation'), 'limits': manifest.get('limits'),
                  'evaluator': evaluator, 'execution': row['execution']}
    missing = [k for k in ('task', 'input', 'instruction', 'contract', 'isolation', 'limits') if not conditions[k]]
    if not all(k in model for k in ('requestedId', 'reportedIds', 'api', 'effort', 'contextWindow')) or not model.get('requestedId') or not model.get('reportedIds'):
        missing.append('model_identity')
    if row['evaluator'] == 'unknown':
        missing.append('evaluator_identity')
    if not harness or not harness.get('binarySha256') or not harness.get('sourceSha256'):
        missing.append('harness_identity')
    if row['pair'] == 'unknown':
        missing.append('pair_id')
    if row['harnessUnchanged'] is not True:
        missing.append('harness_stability')
    if row['execution'] != 'real-model':
        missing.append('real_model_execution')
    if context.get('experimentData'):
        experiment_pairing(row, manifest, context, conditions, missing)
    assessments = context.get('assessments', [])
    row['assessments'] = assessments
    row['excludedFromCapabilityComparison'] = any(a['excludeFromCapabilityComparison'] for a in assessments)
    row['rawInfrastructure'] = row['infrastructure']
    if row['excludedFromCapabilityComparison']:
        missing.append('assessment_excluded')
        row['infrastructure'] = 'assessment_infrastructure_error' if any(a['status'] == 'infrastructure_error' for a in assessments) else 'assessment_excluded'
    row['executionCondition'] = ('planned-real-model' if context.get('experimentData') and row['execution'] in ('real-model', 'unknown') else row['execution'])
    row['pairingEligible'] = not missing
    row['pairingLimits'] = missing
    row['conditions'] = _identity(conditions, 'conditions')
    return row


def paired_comparisons(rows):
    groups = defaultdict(list)
    for row in rows:
        if row['pairingEligible']:
            groups[(row['pair'], row['conditions'])].append(row)
    comparisons, ambiguous, unmatched = defaultdict(list), 0, 0
    for (_, _), group in groups.items():
        by_harness = defaultdict(list)
        for row in group:
            by_harness[row['harness']].append(row)
        for left, right in itertools.combinations(sorted(by_harness), 2):
            if len(by_harness[left]) != 1 or len(by_harness[right]) != 1:
                ambiguous += 1
                continue
            a, b = by_harness[left][0], by_harness[right][0]
            comparisons[(a['model'], left, right)].append((a, b))
        if len(by_harness) < 2:
            unmatched += len(group)
    results = []
    for (model, left, right), pairs in sorted(comparisons.items()):
        both = a_only = b_only = neither = unknown = 0
        for a, b in pairs:
            if a['functional'] in ('unknown', 'evaluator_error') or b['functional'] in ('unknown', 'evaluator_error'):
                unknown += 1
            elif a['functional'] == 'passed' and b['functional'] == 'passed':
                both += 1
            elif a['functional'] == 'passed':
                a_only += 1
            elif b['functional'] == 'passed':
                b_only += 1
            else:
                neither += 1
        deltas = {}
        for metric in METRICS:
            values = [b['metrics'][metric]-a['metrics'][metric] for a, b in pairs
                      if a['metrics'][metric] is not None and b['metrics'][metric] is not None]
            deltas[metric] = {'knownPairs': len(values), 'unknownPairs': len(pairs)-len(values),
                              'sumBMinusA': sum(values) if values else None, 'meanBMinusA': sum(values)/len(values) if values else None}
        results.append({'model': model, 'A': left, 'B': right, 'pairs': len(pairs),
                        'bothPass': both, 'aOnlyPass': a_only, 'bOnlyPass': b_only,
                        'neitherPass': neither, 'unknownFunctionalPairs': unknown, 'metricDeltas': deltas,
                        'attemptPairs': [[a['attempt'], b['attempt']] for a, b in pairs]})
    return {'comparisons': results, 'ineligibleAttempts': sum(not r['pairingEligible'] for r in rows),
            'unmatchedEligibleAttempts': unmatched, 'ambiguousHarnessPairGroups': ambiguous,
            'interpretation': 'Descriptive paired outcomes for matched fixed-model conditions. Whole harnesses may differ in many ways. No component-level causal attribution or best-of-k selection.'}


def experiment_pairing(row, manifest, context, conditions, missing):
    """Bind the planned job even when Harbor never created an episode."""
    experiment = context['experimentData']
    planned = experiment.get('model', {})
    planned_model = {'requestedId': planned.get('id'), 'api': planned.get('api', 'openai-completions'),
                     'effort': planned.get('effort'), 'contextWindow': planned.get('context_window')}
    row['observedModel'] = row['model']
    row['model'] = _identity(planned_model, 'model')
    candidate = next((c for c in experiment.get('candidates', []) if c.get('id') == context.get('candidate')), {})
    images = experiment.get('images', {})
    image_id = images.get(candidate.get('image'))
    row['observedHarness'] = row['harness']
    row['harness'] = _identity(image_id, 'harness')
    row['pair'] = _identity([context['experiment'], context.get('task'), context.get('repeat')], 'pair')
    task_digest = experiment.get('tasks', {}).get(context.get('task'))
    verifier_image = images.get(experiment.get('verifier_image'))
    if task_digest:
        for key in ('input', 'instruction', 'contract'):
            conditions[key] = task_digest
            if key in missing:
                missing.remove(key)
    conditions['isolation'] = 'harbor-separate-verifier'
    if 'isolation' in missing:
        missing.remove('isolation')
    conditions['limits'] = context.get('limits')
    if context.get('limits') and 'limits' in missing:
        missing.remove('limits')
    if 'pair_id' in missing:
        missing.remove('pair_id')
    if not image_id:
        missing.append('candidate_image_identity')
    if not verifier_image:
        missing.append('verifier_image_identity')
    conditions['verifierImage'] = verifier_image
    observed = manifest.get('model', {})
    if not isinstance(observed, dict) or any(observed.get(k) != v for k, v in planned_model.items()):
        missing.append('planned_observed_model_mismatch')


def _harbor_job(root, job, experiment, experiment_id, launch_code=None):
    name = job.get('name')
    if not isinstance(name, str) or Path(name).name != name or name in ('.', '..'):
        raise ValueError('invalid_job_basename')
    job_root = root / 'jobs' / name
    # Every trial is retained, even if a retry produced more than one.
    trials = sorted(p for p in job_root.iterdir() if p.is_dir() and not p.name.startswith('.')) if job_root.is_dir() else []
    limits = None
    config = root / 'packages' / name / 'task.toml'
    if config.is_file():
        import tomllib
        try:
            parsed = tomllib.loads(config.read_text())
            limits = {k: parsed.get(k) for k in ('agent', 'environment', 'verifier')}
        except (ValueError, OSError):
            pass
    task_meta = _read(root / 'packages' / name / 'environment/task.json', [], 'task')
    if not trials:
        trials = [None]
    for trial in trials:
        problems = []
        infra = 'missing_trial' if trial is None else 'not_recorded'
        if launch_code is not None and launch_code != 0:
            infra = 'launcher_failed'
        metadata_path = (trial or job_root) / 'result.json'
        if metadata_path.is_file():
            metadata = _read(metadata_path, problems, 'harbor_result')
            if metadata.get('exception_info') or metadata.get('exception') or metadata.get('error'):
                infra = 'harbor_exception'
            elif trial is not None:
                infra = 'trial_recorded'
        if trial and (trial / 'exception.txt').is_file():
            infra = 'harbor_exception'
        if trial is None:
            problems.append('scheduled_trial_missing')
        episode = (trial or job_root) / 'agent' / 'episode'
        if not episode.is_dir():
            problems.append('episode_missing')
        grade_path = (trial or job_root) / 'verifier' / 'grade.json'
        evidence_candidates = [episode / 'visual/evidence.json', (trial or job_root) / 'verifier/visual/evidence.json']
        yield episode, {'experiment': experiment_id, 'experimentData': experiment,
            'job': name, 'task': job.get('task'), 'family': task_meta.get('family'), 'candidate': job.get('candidate'), 'repeat': job.get('repeat'),
            'trial': trial.name if trial else None, 'gradePath': grade_path,
            'evidencePath': next((p for p in evidence_candidates if p.is_file()), evidence_candidates[0]),
            'infrastructure': infra, 'launchExitCode': launch_code, 'problems': problems, 'limits': limits}


def discover_attempts(roots):
    """Experiment jobs define planned denominators; filesystem scans add orphan attempts."""
    found = {}
    covered_experiments = []
    for supplied in roots:
        supplied = Path(supplied).resolve()
        if supplied.name == 'experiment.json' and supplied.is_file():
            supplied = supplied.parent
        experiment_paths = [supplied / 'experiment.json'] if (supplied / 'experiment.json').is_file() else sorted(supplied.rglob('experiment.json'))
        for experiment_path in experiment_paths:
            root = experiment_path.parent
            if root in covered_experiments:
                continue
            covered_experiments.append(root)
            problems = []
            experiment = _read(experiment_path, problems, 'experiment')
            experiment_id = 'experiment-' + digest(experiment_path)[:16]
            launch_codes = {}
            launches = root / 'launches.jsonl'
            if launches.is_file():
                for line in launches.read_text().splitlines():
                    try:
                        row = json.loads(line)
                        if isinstance(row.get('job'), str) and isinstance(row.get('exit_code'), int):
                            launch_codes[row['job']] = row['exit_code']
                    except (ValueError, AttributeError):
                        pass
            jobs = experiment.get('jobs', [])
            if not isinstance(jobs, list):
                jobs = []
            planned_names = {j.get('name') for j in jobs if isinstance(j, dict)}
            extra = [p for p in (root / 'jobs').iterdir() if p.is_dir() and p.name not in planned_names] if (root / 'jobs').is_dir() else []
            for job in [*jobs, *[{'name': p.name} for p in sorted(extra)]]:
                for episode, context in _harbor_job(root, job, experiment, experiment_id, launch_codes.get(job['name'])):
                    context['problems'].extend(problems)
                    if job['name'] not in planned_names:
                        context['problems'].append('unplanned_job')
                    found[episode] = context
            if not jobs and not extra:
                found[root] = {'problems': problems + ['experiment_has_no_jobs']}
        if any(supplied == root or supplied.is_relative_to(root) for root in covered_experiments):
            continue
        candidates = set()
        if any((supplied / name).is_file() for name in ('manifest.json', 'run.json')):
            candidates.add(supplied)
        else:
            for filename in ('manifest.json', 'run.json'):
                candidates.update(p.parent for p in supplied.rglob(filename)
                                  if not any(p.is_relative_to(root) for root in covered_experiments))
            candidates.update(p for p in supplied.glob('**/agent/episode') if p.is_dir()
                              and not any(p.is_relative_to(root) for root in covered_experiments))
            candidates.update(p.parent / 'agent/episode' for p in supplied.rglob('verifier') if p.is_dir()
                              and not any(p.is_relative_to(root) for root in covered_experiments))
            # Trial failures may have only result.json or exception.txt.
            for filename in ('exception.txt', 'result.json'):
                for p in supplied.rglob(filename):
                    if any(p.is_relative_to(root) for root in covered_experiments):
                        continue
                    if filename == 'exception.txt' or (p.parent / 'config.json').is_file():
                        candidates.add(p.parent / 'agent/episode')
        for episode in sorted(candidates):
            if episode.name == 'episode' and episode.parent.name == 'agent':
                trial = episode.parent.parent
                infrastructure = 'harbor_exception' if (trial / 'exception.txt').is_file() else 'not_recorded'
                metadata = _read(trial / 'result.json', [], 'harbor_result')
                if metadata.get('exception_info'):
                    infrastructure = 'harbor_exception'
                found.setdefault(episode, {'gradePath': trial / 'verifier/grade.json', 'trial': trial.name,
                                          'infrastructure': infrastructure})
            else:
                found.setdefault(episode, {})
        if not candidates and not experiment_paths:
            # An explicitly supplied empty attempt is still a row.
            found.setdefault(supplied, {})
    assessment_paths = set()
    for supplied in roots:
        supplied = Path(supplied).resolve()
        supplied = supplied.parent if supplied.is_file() else supplied
        assessment_paths.update(supplied.rglob('assessment.json'))
        # Direct episode inputs still honor their enclosing experiment assessment.
        for ancestor in (supplied, *supplied.parents):
            if (ancestor / 'experiment.json').is_file():
                assessment_paths.update(p / 'assessment.json' for p in (supplied, *supplied.parents)
                                        if p.is_relative_to(ancestor) and (p / 'assessment.json').is_file())
                break
    for path in sorted(assessment_paths):
        assessment = _read(path, [], 'assessment')
        public = {'id': 'assessment-' + digest(path)[:16],
                  'status': assessment.get('status') if assessment.get('status') in ('infrastructure_error', 'valid', 'invalid') else 'unknown',
                  'excludeFromCapabilityComparison': assessment.get('exclude_from_capability_comparison') is True,
                  'retainUsage': True}
        for episode, context in found.items():
            if episode.is_relative_to(path.parent):
                context.setdefault('assessments', []).append(public)
    return list(found.items())


def aggregate(run_dirs, tasks=None):
    discovered = discover_attempts(run_dirs)
    rows = [read_attempt(path, i+1, tasks, context) for i, (path, context) in enumerate(discovered)]
    included = [r for r in rows if not r['excludedFromCapabilityComparison']]
    observed = {key: {'knownLowerBound': sum(r['observedTokens'][key] for r in rows if r['observedTokens'][key] is not None)
                        if any(r['observedTokens'][key] is not None for r in rows) else None,
                       'attemptsWithObservation': sum(r['observedTokens'][key] is not None for r in rows),
                       'attemptsWithoutObservation': sum(r['observedTokens'][key] is None for r in rows)}
                for key in ('input', 'output', 'cachedInput', 'reasoningOutput')}
    return {'schema': SCHEMA, 'attempts': rows, 'attemptCount': len(rows),
            'assessmentExcludedAttempts': len(rows)-len(included),
            'capabilityByTask': _group(included, ('task', 'model', 'candidate', 'harness', 'executionCondition')),
            'capabilityByFamily': _group(included, ('family', 'model', 'candidate', 'harness', 'executionCondition')),
            'observedTokenLowerBounds': observed,
            'executions': dict(Counter(r['execution'] for r in rows)),
            'byTask': _group(rows, ('task', 'model', 'candidate', 'harness', 'executionCondition')),
            'byFamily': _group(rows, ('family', 'model', 'candidate', 'harness', 'executionCondition')),
            'metrics': _metric_summary(rows), 'paired': paired_comparisons(rows),
            'limits': [
                'All supplied attempts remain in raw tables and cost totals. Explicit assessment exclusions are removed from capability tables and pairs, with their counts retained. Pass fractions are observed pass / the displayed denominator, not estimates for missing grades.',
                'Unknown token fields are not zero. Cached input and reasoning output are subcategories, not extra tokens to add to input and output.',
                'No monetary estimate without an explicit price schedule. Evaluation time and human review time are separate from construction costs.',
                'Visual states are human judgments over recorded viewed evidence; missing reviews remain unknown. Geometry and coverage are not aesthetic scores.',
                'Identifiers for models, harnesses, pair IDs and conditions are hashed aliases. Exact identities remain in private raw manifests.',
                'This report excludes prompts, response text, error messages, commands, provider IDs, reviewer prose, images and local paths. It cannot recover attempts never supplied to it.',
            ]}


def render_html(report):
    esc = lambda value: html.escape(str(value))
    def table(headers, rows):
        return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+esc(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>'
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Circuit benchmark report</title><style>body{font:15px/1.5 system-ui,sans-serif;max-width:1400px;margin:32px auto;padding:0 24px;color:#1b2b3d;background:#fafbfd}h1{font-size:28px}h2{font-size:20px;margin-top:32px}.scroll{overflow:auto}table{border-collapse:collapse;background:white;width:100%}th,td{text-align:left;padding:10px 12px;border-bottom:1px solid #d5dce4;vertical-align:top}th{background:#eaf0f6}code{overflow-wrap:anywhere}.muted{color:#536476}li{margin-bottom:8px}</style>',
             '<h1>Circuit construction benchmark</h1>', f'<p>{report["attemptCount"]} attempts · Functional results, human visual judgments, and resource use are reported separately.</p>',
             '<p>Execution kinds: '+esc(json.dumps(report['executions']))+'. Synthetic and scripted rows test infrastructure, not model capability.</p>',
             f'<p>{report["assessmentExcludedAttempts"]} attempts excluded from capability comparison by explicit assessment; their raw outcomes and all usage remain below.</p>',
             '<p class="muted">No best-of-k selection. Unknown is not zero. Models and harnesses use stable hashed aliases.</p>']
    for title, field, key in [('Capability comparison by task', 'capabilityByTask', 'task'), ('Capability comparison by family', 'capabilityByFamily', 'family')]:
        parts.append('<h2>'+title+'</h2>')
        parts.append(table([key.title(), 'Model', 'Candidate', 'Harness', 'Execution condition', 'Attempts', 'Passed / all', 'Failed / invalid', 'Evaluator error / unknown'],
                           [[r[key], r['model'], r['candidate'], r['harness'], r['executionCondition'], r['attempts'], f'{r["functional"]["passed"]}/{r["attempts"]}',
                             f'{r["functional"]["failed"]}/{r["functional"]["invalid_artifact"]}',
                             f'{r["functional"]["evaluator_error"]}/{r["functional"]["unknown"]}'] for r in report[field]]))
    parts.append('<h2>Every attempt</h2>')
    parts.append(table(['Attempt / task / job / candidate / repeat', 'Execution / outcome / function / infrastructure', 'Input / output', 'Cache / reasoning', 'Requests with usage / total', 'Tools / failed / HTTP errors', 'Seconds', 'Assessment exclusion'],
                       [[r['attempt']+' / '+r['task']+' / '+r['job']+' / '+r['candidate']+' / '+str(r['repeat']), r['execution']+' / '+r['outcome']+' / '+r['functional']+' / '+r['infrastructure'],
                         f'{r["metrics"]["input"]} / {r["metrics"]["output"]}', f'{r["metrics"]["cachedInput"]} / {r["metrics"]["reasoningOutput"]}',
                         f'{r["metrics"]["requestsWithUsage"]} / {r["metrics"]["requests"]}',
                         f'{r["metrics"]["toolCalls"]} / {r["metrics"]["failedCalls"]} / {r["metrics"]["httpErrors"]}',
                         r['metrics']['elapsedMs']/1000 if r['metrics']['elapsedMs'] is not None else 'unknown', r['excludedFromCapabilityComparison']] for r in report['attempts']]).replace('None','unknown'))
    parts.append('<h2>Observed tokens: lower bounds only</h2><p>Interrupted or unmetered requests keep full totals unknown. These are sums actually observed, not complete cost estimates. Cache and reasoning are subcategories.</p>')
    parts.append(table(['Attempt', 'Observed input', 'Observed output', 'Observed cache', 'Observed reasoning', 'Requests with / without / total usage'],
                       [[r['attempt'], *[r['observedTokens'][k] if r['observedTokens'][k] is not None else 'unknown' for k in ('input', 'output', 'cachedInput', 'reasoningOutput')],
                         f'{r["metrics"]["requestsWithUsage"]} / {r["metrics"]["requestsWithoutUsage"]} / {r["metrics"]["requests"]}'] for r in report['attempts']]).replace('None','unknown'))
    parts.append('<h2>Human visual review</h2>')
    parts.append(table(['Attempt', 'Collection / review', 'Inspected / captured / omitted tiles / unresolved dependencies / native unknowns', *[v[0] for v in AXES.values()], 'Human minutes / render seconds'],
                       [[r['attempt'], r['visual']['collection']+' / '+r['visual']['review'],
                         f'{r["visual"]["viewedViews"]} / {r["visual"]["capturedViews"]} / {r["visual"]["omittedTiles"]} / {r["visual"]["unresolvedDependencies"]} / {r["visual"]["observationUnknowns"]}',
                         *[r['visual']['axes'][a] for a in AXES],
                         f'{r["visual"]["humanMinutes"]} / {r["visual"]["collectionMs"]/1000 if r["visual"]["collectionMs"] is not None else "unknown"}']
                        for r in report['attempts']]).replace('None','unknown'))
    parts.append('<h2>Fixed-model paired harness comparison</h2><p>'+esc(report['paired']['interpretation'])+'</p>')
    parts.append(table(['Model', 'A / B', 'Pairs', 'Both pass', 'A only', 'B only', 'Neither', 'Unknown'],
                       [[p['model'], p['A']+' / '+p['B'], p['pairs'], p['bothPass'], p['aOnlyPass'], p['bOnlyPass'], p['neitherPass'], p['unknownFunctionalPairs']]
                        for p in report['paired']['comparisons']]))
    parts.append('<p>'+esc(f'{report["paired"]["ineligibleAttempts"]} ineligible attempts; {report["paired"]["unmatchedEligibleAttempts"]} unmatched eligible attempts; {report["paired"]["ambiguousHarnessPairGroups"]} ambiguous harness pair groups.')+'</p>')
    parts.append('<h2>Usage totals and missingness</h2>')
    parts.append(table(['Metric', 'Sum of known values', 'Known attempts', 'Unknown attempts'], [[k, v['knownSum'] if v['knownSum'] is not None else 'unknown', v['knownAttempts'], v['unknownAttempts']] for k, v in report['metrics'].items()]))
    parts.append('<h2>Tool calls and evidence gaps</h2>')
    parts.append(table(['Attempt', 'Tools by name', 'Evidence gaps', 'Pairing limits'], [[r['attempt'], json.dumps(r['toolsByName']), ', '.join(r['unknowns']) or 'none recorded', ', '.join(r['pairingLimits']) or 'none recorded'] for r in report['attempts']]))
    parts.append('<h2>Interpretation limits</h2><ul>'+''.join('<li>'+esc(s)+'</li>' for s in report['limits'])+'</ul></html>')
    return ''.join(parts)


def write_report(run_dirs, out, tasks=None):
    report = aggregate(run_dirs, tasks)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / 'report.json', report)
    (out / 'index.html').write_text(render_html(report), encoding='utf-8')
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', nargs='?', type=Path, help='Recursively discover an experiment or results root')
    p.add_argument('--runs', type=Path, nargs='+', help='Additional roots or explicit attempts, including incomplete attempts')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--tasks', type=Path, default=Path(__file__).resolve().parents[1] / 'tasks', help='Task catalog for family metadata')
    args = p.parse_args(argv)
    tasks = {}
    if args.tasks:
        for path in args.tasks.glob('*/task.json'):
            metadata = json.loads(path.read_text())
            tasks[metadata['id']] = metadata
    roots = ([args.root] if args.root else []) + (args.runs or [])
    if not roots:
        p.error('supply a root or --runs')
    result = write_report(roots, args.out, tasks)
    print(json.dumps({'attempts': result['attemptCount'], 'pairedComparisons': len(result['paired']['comparisons'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
