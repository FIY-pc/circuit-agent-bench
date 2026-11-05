"""Evidence integrity, failed-attempt accounting, and paired-comparison regressions."""
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from circuit_bench import report, review, visual
from circuit_bench.host import Tree

# A real, tiny PNG; fake host changes only its IHDR dimensions for unit fixtures.
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jKc0AAAAASUVORK5CYII=')
PROJECT = '''<?xml version="1.0" encoding="UTF-8"?>
<project source="2.15.0.2.exe" version="1.0"><lib desc="#Wiring" name="0"/><lib desc="#Base" name="6"/><main name="main"/>
<circuit name="main"><comp loc="(100,100)" name="child"/><comp loc="(300,100)" name="child"/>
<comp lib="6" loc="(9100,100)" name="Text"><a name="text" val="Remote label"/></comp></circuit>
<circuit name="child"><comp loc="(100,100)" name="leaf"/></circuit>
<circuit name="leaf"><comp lib="0" loc="(100,100)" name="Pin"/></circuit>
<circuit name="unused"/></project>'''


class FakeHost:
    fail_tile = False
    opened = None
    calls = []

    def __init__(self, tree):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def call(self, op, **args):
        self.calls.append((op, args))
        if op == 'open':
            type(self).opened = Path(args['path'])
            return {'id': 'doc', 'revision': 'rev'}
        if op == 'observe':
            return {'focus': {'bounds': {'x': 50, 'y': 50, 'width': 9500, 'height': 150},
                              'components': [{'visualBounds': {'x': 0, 'y': -50, 'width': 50, 'height': 50}}]},
                    'runtime': {'jarSha256': 'runtime', 'reportedVersion': 'test', 'javaVersion': 'test'}}
        if op == 'render':
            if self.fail_tile and args['scale'] == 1 and args['x'] > 1000:
                raise RuntimeError('sensitive path must not be emitted')
            png = PNG[:16] + struct.pack('>II', __import__('math').ceil(args['width']*args['scale']), __import__('math').ceil(args['height']*args['scale'])) + PNG[24:]
            return {'image': base64.b64encode(png).decode()}
        return None


class EvidenceCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / 'answer'
        self.package.mkdir()
        self.artifact = self.package / 'design.circ'
        self.artifact.write_text(PROJECT)
        self.binary = self.root / 'host'
        self.binary.write_bytes(b'fixed renderer')
        self.tree = Tree(self.root, 'tree', None, 'fixed-commit', False, self.binary)
        FakeHost.fail_tile, FakeHost.calls = False, []

    def collect(self, **kwargs):
        with patch.object(visual, 'Host', FakeHost), patch.object(visual, '_font_identity', return_value='fonts'):
            return visual.collect(self.artifact, self.tree, self.root / 'visual', **kwargs)

    def test_reachable_once_unreachable_explicit_wide_bounds_and_unchanged_source(self):
        before = visual.package_digest(self.package)
        evidence = self.collect()
        self.assertEqual(evidence['status'], 'complete')
        self.assertEqual([d['name'] for d in evidence['definitions']], ['main', 'child', 'leaf'])
        self.assertEqual(evidence['unreachableDefinitions'], ['unused'])
        self.assertEqual(before, visual.package_digest(self.package))
        self.assertTrue(evidence['provenance']['sourceUnchanged'])
        self.assertTrue(evidence['provenance']['packageUnchanged'])
        self.assertNotEqual(FakeHost.opened, self.artifact)
        bounds = evidence['definitions'][0]['bounds']
        self.assertGreater(bounds['width'], 8000)
        self.assertEqual(bounds['y'], -74)
        self.assertEqual(evidence['definitions'][0]['views'][0]['viewport']['width'], bounds['width'])
        self.assertEqual(sum(op == 'open' for op, _ in FakeHost.calls), 1)

    def test_budget_and_render_failure_never_become_full_coverage(self):
        FakeHost.fail_tile = True
        evidence = self.collect(max_tiles=2)
        self.assertEqual(evidence['status'], 'partial')
        self.assertGreater(evidence['coverage']['omittedTiles'], 0)
        self.assertGreater(evidence['coverage']['failedViews'], 0)
        self.assertNotIn('sensitive', json.dumps(evidence))
        plan = evidence['definitions'][0]['tiles']
        self.assertEqual(plan['firstOmittedIndex'], 2)

    def test_packaged_library_definitions_are_reached_and_named_separately(self):
        dependency = self.package / 'sub.circ'
        dependency.write_text(PROJECT)
        self.artifact.write_text(PROJECT.replace('<main name="main"/>', '<lib name="7" desc="file#sub.circ"/><main name="main"/>').replace('<comp loc="(100,100)" name="child"/>', '<comp lib="7" loc="(100,100)" name="child"/>', 1))
        evidence = self.collect(max_tiles=1)
        self.assertEqual(len(evidence['definitions']), 5)
        children = [d for d in evidence['definitions'] if d['name'] == 'child']
        self.assertEqual(len({d['project'] for d in children}), 2)
        self.assertEqual(len(evidence['definitions'][0]['childDefinitionIds']), 2)

    def test_unsafe_dependency_rejected_before_host(self):
        self.artifact.write_text(PROJECT.replace('<main', '<lib name="7" desc="file#../../outside.circ"/><main'))
        evidence = self.collect()
        self.assertEqual(evidence['status'], 'error')
        self.assertFalse(FakeHost.calls)

    def test_empty_budget_no_fake_detail_coverage(self):
        evidence = self.collect(max_tiles=0)
        self.assertEqual(evidence['status'], 'partial')
        self.assertTrue(all(d['tiles']['selected'] == 0 for d in evidence['definitions']))

    def test_cannot_write_evidence_inside_input_or_overwrite_evidence(self):
        with self.assertRaises(ValueError):
            visual.collect(self.artifact, self.tree, self.package / 'visual')
        self.collect()
        with self.assertRaises(FileExistsError):
            self.collect()

    def test_review_requires_real_inspected_evidence_and_exact_artifact(self):
        evidence = self.collect(max_tiles=1)
        path = self.root / 'visual/evidence.json'
        form = review.new_review(evidence, visual.digest(path))
        self.assertEqual(review.validate_review(form, evidence, visual.digest(path)), form)
        form['rubric']['local_tracing'] = {'state': 'usable', 'evidence': [], 'note': 'A reader follows an input to output.'}
        with self.assertRaisesRegex(ValueError, 'rating_evidence_required'):
            review.validate_review(form, evidence, visual.digest(path))
        view = evidence['definitions'][0]['views'][0]['id']
        form['rubric']['local_tracing']['evidence'] = [view]
        with self.assertRaisesRegex(ValueError, 'unviewed'):
            review.validate_review(form, evidence, visual.digest(path))
        form['viewedViewIds'] = [view]
        review.validate_review(form, evidence, visual.digest(path))
        self.assertGreater(len(review.review_coverage(form, evidence)['unviewedViewIds']), 0)
        self.assertFalse(review.review_coverage(form, evidence)['collectionComplete'])
        form['artifactSha256'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            review.validate_review(form, evidence, visual.digest(path))

    def test_review_page_does_not_execute_circuit_names_and_checks_image_hashes(self):
        evidence = self.collect(max_tiles=1)
        path = self.root / 'visual/evidence.json'
        evidence['definitions'][0]['name'] = '</script><script>alert(1)</script>'
        visual.write_json(path, evidence)
        page = review.build_review(path)
        self.assertNotIn('</script><script>alert(1)', page.read_text())
        self.assertIn('Export review JSON', page.read_text())
        (path.parent / evidence['definitions'][0]['views'][0]['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed_or_unsafe'):
            review.load_evidence(path)


class ReportCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def attempt(self, name, outcome='completed', passed=True, harness='a', pair='pair-1', **changes):
        path = self.root / name
        path.mkdir()
        manifest = {'task': 'full-adder', 'family': 'arithmetic', 'execution': 'real-model',
                    'pairId': pair, 'limits': {'timeoutMinutes': 15}, 'isolation': 'container',
                    'model': {'requestedId': 'private-provider/model-exact', 'reportedIds': ['private-reported'],
                              'api': 'test-api', 'effort': 'high', 'contextWindow': 10000},
                    'harness': {'sourceSha256': harness, 'binarySha256': harness}, 'harnessUnchanged': True,
                    'inputSha256': 'input', 'instructionSha256': 'instruction', 'taskContractSha256': 'task'}
        manifest.update(changes)
        run = {'outcome': outcome, 'elapsedMs': 1200, 'final': 'do not share full answer',
               'error': '/home/private-user/project and provider details',
               'ledger': {'toolCalls': 5, 'failedCalls': 1},
               'meter': {'input': 100, 'output': 20, 'cachedInput': None, 'reasoningOutput': 4,
                         'requests': 2, 'requestsWithUsage': 1, 'httpErrors': 1}}
        grade = {'status': 'passed' if passed else 'failed', 'passed': passed,
                 'provenance': {'product': {'host_sha256': 'fixed'}, 'grader_sha256': 'grader',
                                'oracle_sha256': 'oracle', 'task_sha256': 'task'}}
        for filename, data in [('manifest.json', manifest), ('run.json', run), ('grade.json', grade)]:
            visual.write_json(path / filename, data)
        (path / 'tool-calls.jsonl').write_text(json.dumps({'tool': 'render_circuit', 'target': 'private command'})+'\n')
        return path

    def test_all_attempts_and_missingness_no_best_of_k_or_prompt_leaks(self):
        a = self.attempt('a')
        b = self.attempt('b', outcome='timeout', passed=False)
        c = self.attempt('c', outcome='error', passed=False)
        (c / 'grade.json').unlink()
        d = self.root / 'd'
        d.mkdir()
        result = report.aggregate([a, b, c, d, a])
        self.assertEqual(result['attemptCount'], 4)
        self.assertEqual(result['byTask'][0]['attempts'], 3)
        self.assertEqual(result['byTask'][0]['functional']['passed'], 1)
        self.assertEqual(result['metrics']['input'], {'knownSum': 300, 'knownAttempts': 3, 'unknownAttempts': 1})
        self.assertIsNone(result['metrics']['cachedInput']['knownSum'])
        self.assertEqual(result['metrics']['cachedInput']['unknownAttempts'], 4)
        payload = json.dumps(result) + report.render_html(result)
        for private in ('private-provider', 'private-reported', '/home/', 'full answer', 'private command', 'private-user'):
            self.assertNotIn(private, payload)
        self.assertTrue(all(r['visual']['axes']['local_tracing'] == 'unknown' for r in result['attempts']))

    def test_fixed_model_pairs_and_duplicate_attempts_are_not_cherry_picked(self):
        a = self.attempt('a', harness='a')
        b = self.attempt('b', harness='b', outcome='timeout', passed=False)
        result = report.aggregate([a, b])
        pair = result['paired']['comparisons'][0]
        self.assertEqual(pair['pairs'], 1)
        self.assertEqual(pair['aOnlyPass']+pair['bOnlyPass'], 1)
        duplicate = self.attempt('duplicate', harness='a')
        result = report.aggregate([a, b, duplicate])
        self.assertFalse(result['paired']['comparisons'])
        self.assertEqual(result['paired']['ambiguousHarnessPairGroups'], 1)
        self.assertEqual(result['attemptCount'], 3)

    def test_pairing_rejects_different_input_model_budget_and_missing_conditions(self):
        a = self.attempt('a', harness='a')
        changed = [dict(inputSha256='other'), dict(limits={'timeoutMinutes': 10}),
                   dict(model={'requestedId': 'another'}), dict(pairId=None), dict(harnessUnchanged=False)]
        for i, changes in enumerate(changed):
            b = self.attempt(f'b{i}', harness='b', **changes)
            self.assertFalse(report.aggregate([a, b])['paired']['comparisons'])

    def test_evaluator_errors_do_not_count_as_known_functional_failure(self):
        a, b = self.attempt('a'), self.attempt('b', harness='b')
        grade = json.loads((b / 'grade.json').read_text())
        grade.update(status='evaluator_error', passed=None)
        visual.write_json(b / 'grade.json', grade)
        result = report.aggregate([a, b])
        self.assertEqual(result['paired']['comparisons'][0]['unknownFunctionalPairs'], 1)
        self.assertEqual(result['attempts'][1]['functional'], 'evaluator_error')

    def test_recursive_experiment_keeps_missing_trials_and_harbor_exceptions(self):
        experiment = self.root / 'experiment'
        experiment.mkdir()
        model = {'id': 'private-provider/model-exact', 'effort': 'high', 'context_window': 10000, 'api': 'test-api'}
        jobs = [{'name': 'job-a', 'task': 'full-adder', 'candidate': 'alpha', 'repeat': 1},
                {'name': 'job-b', 'task': 'full-adder', 'candidate': 'beta', 'repeat': 1},
                {'name': 'job-c', 'task': 'full-adder', 'candidate': 'alpha', 'repeat': 2},
                {'name': 'job-d', 'task': 'full-adder', 'candidate': 'beta', 'repeat': 2}]
        visual.write_json(experiment / 'experiment.json', {'schema': 'circuit-agent-bench.experiment/v1',
            'model': model, 'candidates': [{'id': 'alpha', 'image': 'image-a'}, {'id': 'beta', 'image': 'image-b'}],
            'images': {'image-a': 'sha256:a', 'image-b': 'sha256:b', 'verifier': 'sha256:v'},
            'verifier_image': 'verifier', 'tasks': {'full-adder': 'task-package-digest'}, 'jobs': jobs})
        for job in jobs:
            package = experiment / 'packages' / job['name']
            package.mkdir(parents=True)
            (package / 'task.toml').write_text('[agent]\ntimeout_sec=900\n[environment]\ncpus=2\n[verifier]\ntimeout_sec=300\n')
        import shutil
        for name, candidate in [('job-a', 'a'), ('job-b', 'b')]:
            trial = experiment / 'jobs' / name / 'trial-1'
            episode = trial / 'agent/episode'
            episode.parent.mkdir(parents=True)
            shutil.move(self.attempt(name, harness=candidate), episode)
            (trial / 'verifier').mkdir()
            (episode / 'grade.json').rename(trial / 'verifier/grade.json')
        failed = experiment / 'jobs/job-c/trial-2'
        failed.mkdir(parents=True)
        visual.write_json(failed / 'result.json', {'exception_info': {'exception_type': 'SetupError', 'exception_message': '/home/private/secret'}})
        result = report.aggregate([self.root])
        self.assertEqual(result['attemptCount'], 4)
        self.assertEqual(sum(r['functional'] == 'passed' for r in result['attempts']), 2)
        self.assertEqual({r['candidate'] for r in result['attempts']}, {'alpha', 'beta'})
        self.assertEqual({r['repeat'] for r in result['attempts']}, {1, 2})
        self.assertEqual(result['paired']['comparisons'][0]['pairs'], 1)
        self.assertEqual({r['infrastructure'] for r in result['attempts']}, {'not_recorded', 'harbor_exception', 'missing_trial'})
        self.assertNotIn('/home/private', json.dumps(result))
        self.assertNotIn('private-provider', json.dumps(result))
        self.assertEqual({r['attempts'] for r in result['byTask']}, {2})
        self.assertEqual(len(report.aggregate([experiment])['attempts']), 4)
        visual.write_json(experiment / 'assessment.json', {'status': 'infrastructure_error',
            'exclude_from_capability_comparison': True, 'retain_usage': True})
        excluded = report.aggregate([experiment])
        self.assertEqual(excluded['assessmentExcludedAttempts'], 4)
        self.assertFalse(excluded['capabilityByTask'])
        direct = report.aggregate([experiment / 'jobs/job-a/trial-1/agent/episode'])
        self.assertTrue(direct['attempts'][0]['excludedFromCapabilityComparison'])


    def test_assessment_exclusion_retains_raw_outcomes_and_observed_usage(self):
        a = self.attempt('a', outcome='interrupted', passed=False)
        b = self.attempt('b', harness='b')
        visual.write_json(self.root / 'assessment.json', {'status': 'infrastructure_error',
            'cause': 'private operator details', 'exclude_from_capability_comparison': True, 'retain_usage': True})
        run = json.loads((a / 'run.json').read_text())
        run['meter'].update(input=None, output=None, requestsWithoutUsage=1,
                            observedTokens={'input': 41, 'output': 7, 'cachedInput': 8, 'reasoningOutput': 2})
        visual.write_json(a / 'run.json', run)
        result = report.aggregate([self.root])
        self.assertEqual(result['attemptCount'], 2)
        self.assertEqual(result['assessmentExcludedAttempts'], 2)
        self.assertFalse(result['capabilityByTask'])
        self.assertFalse(result['capabilityByFamily'])
        self.assertFalse(result['paired']['comparisons'])
        self.assertEqual(result['metrics']['input']['knownSum'], 100)
        self.assertEqual(result['metrics']['input']['unknownAttempts'], 1)
        self.assertEqual(result['observedTokenLowerBounds']['input']['knownLowerBound'], 41)
        first = next(r for r in result['attempts'] if r['outcome'] == 'interrupted')
        self.assertEqual(first['metrics']['requestsWithoutUsage'], 1)
        self.assertEqual(first['metrics']['toolCalls'], 5)
        self.assertEqual(first['infrastructure'], 'assessment_infrastructure_error')
        self.assertIn('assessment_excluded', first['pairingLimits'])
        self.assertIsNone(first['metrics']['input'])
        page = report.render_html(result)
        self.assertIn('lower bounds only', page)
        self.assertNotIn('private operator details', json.dumps(result)+page)
        self.assertEqual(sum(g['attempts'] for g in result['byTask']), 2)

    def test_old_request_log_supplies_lower_bounds_without_replacing_unknown_totals(self):
        a = self.attempt('a', outcome='interrupted')
        run = json.loads((a / 'run.json').read_text())
        run['meter'].update(input=None, output=None)
        visual.write_json(a / 'run.json', run)
        (a / 'requests.jsonl').write_text(json.dumps({'usage': {'prompt_tokens': 41, 'completion_tokens': 7}})+'\n'+json.dumps({'usage': None})+'\n')
        result = report.aggregate([a])['attempts'][0]
        self.assertIsNone(result['metrics']['input'])
        self.assertEqual(result['observedTokens']['input'], 41)
        self.assertEqual(result['observedTokensSource'], 'requests')
        self.assertEqual(result['metrics']['requestsWithoutUsage'], 1)

    def test_invalid_review_is_unknown_and_cannot_override_function(self):
        a = self.attempt('a', passed=False)
        folder = a / 'visual'
        folder.mkdir()
        (folder / 'evidence.json').write_text('{}')
        result = report.aggregate([a])['attempts'][0]
        self.assertEqual(result['functional'], 'failed')
        self.assertEqual(result['visual']['review'], 'invalid')
        self.assertIn('visual_evidence_or_review_invalid', result['unknowns'])


@unittest.skipUnless(os.environ.get('VIBE_BENCH_NATIVE_TREE'), 'set VIBE_BENCH_NATIVE_TREE for one sequential native host')
class NativeEvidence(unittest.TestCase):
    def test_large_extent_and_nested_definitions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / 'answer'
            package.mkdir()
            artifact = package / 'design.circ'
            artifact.write_text(PROJECT)
            evidence = visual.collect(artifact, os.environ['VIBE_BENCH_NATIVE_TREE'], root / 'evidence')
            self.assertEqual(evidence['status'], 'complete', evidence)
            self.assertEqual(len(evidence['definitions']), 3)
            self.assertGreater(evidence['definitions'][0]['bounds']['width'], 8000)
            self.assertTrue(evidence['provenance']['sourceUnchanged'])
            self.assertTrue(evidence['provenance']['packageUnchanged'])
            review.load_evidence(root / 'evidence/evidence.json')

    def test_packaged_circuit_library(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / 'answer'
            package.mkdir()
            (package / 'sub.circ').write_text(PROJECT)
            artifact = package / 'design.circ'
            artifact.write_text(PROJECT.replace('<main name="main"/>', '<lib name="7" desc="file#sub.circ"/><main name="main"/>').replace('<comp loc="(100,100)" name="child"/>', '<comp lib="7" loc="(100,100)" name="child"/>', 1))
            evidence = visual.collect(artifact, os.environ['VIBE_BENCH_NATIVE_TREE'], root / 'evidence')
            self.assertEqual(evidence['status'], 'complete', evidence)
            self.assertEqual(len(evidence['definitions']), 5)
            self.assertEqual(evidence['unresolvedDependencies'], [])



if __name__ == '__main__':
    unittest.main()
