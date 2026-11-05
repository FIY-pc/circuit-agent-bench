"""Another Harbor integration can use the same tasks without the Vibe runner."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from circuit_bench.adapters import agent_config
from circuit_bench.experiment import prepare, run
from circuit_bench.adapters.vibe_logisim.images import RUNTIME_MODULES
from circuit_bench import report

ROOT = Path(__file__).resolve().parents[1]


class AdapterContract(unittest.TestCase):
    def test_explicit_selection_and_no_model_replacement(self):
        model = {'id': 'exact-model', 'context_window': 120000, 'effort': 'high'}
        custom = {'agent': {'import_path': 'another:Agent', 'kwargs': {'thinking': 'high'}}}
        cfg = agent_config(custom, model)
        self.assertEqual(cfg['import_path'], 'another:Agent')
        self.assertEqual(cfg['kwargs'], {'thinking': 'high'})
        self.assertEqual(cfg['model_name'], model['id'])
        self.assertTrue(cfg['skip_model_parsing'])
        for bad in ({}, {**custom, 'adapter': 'vibe-logisim'},
                    {'agent': {'name': 'example', 'model_name': 'different'}},
                    {'agent': {'name': 'example', 'import_path': 'another:Agent'}}):
            with self.assertRaises(ValueError):
                agent_config(bad, model)

    def test_agent_image_allowlist_contains_only_execution_modules(self):
        self.assertTrue(all((ROOT/'circuit_bench'/name).is_file() for name in RUNTIME_MODULES))
        self.assertNotIn('grading.py', RUNTIME_MODULES)
        self.assertNotIn('task_oracles.py', RUNTIME_MODULES)
        self.assertIn('identity.py', RUNTIME_MODULES)
        self.assertIn('adapters/vibe_logisim/episode.py', RUNTIME_MODULES)

    @unittest.skipUnless(importlib.util.find_spec('harbor'), 'Harbor extra not installed')
    def test_custom_agent_preparation_and_launch_without_product_credentials(self):
        spec = {'model': {'id': 'fixture-model', 'effort': 'none', 'context_window': 10000},
                'candidates': [{'id': 'external', 'image': 'external:fixed',
                                'agent': {'import_path': 'fixture_harbor_agent:FixtureAgent'},
                                'required_env': ['FIXTURE_API_KEY']}],
                'verifier_image': 'verifier:fixed', 'tasks': ['full-adder']}
        clean = {k:v for k,v in os.environ.items() if not k.startswith('CIRCUIT_BENCH_')}
        clean.update(FIXTURE_API_KEY='synthetic-secret', PYTHONPATH='/custom/modules')
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, clean, clear=True), \
             patch('circuit_bench.experiment.subprocess.check_output', return_value=b'[{"Id":"sha256:fixed"}]'):
            output=Path(temp)/'experiment'
            frozen=prepare(spec, ROOT, output)
            self.assertEqual(frozen['required_env'], ['FIXTURE_API_KEY'])
            self.assertIsNone(frozen['serviceFingerprint'])
            cfg=json.loads(Path(frozen['jobs'][0]['config']).read_text())
            self.assertEqual(cfg['agents'][0]['import_path'], 'fixture_harbor_agent:FixtureAgent')
            self.assertNotIn('context_window', cfg['agents'][0]['kwargs'])
            for path in output.rglob('*'):
                if path.is_file():
                    self.assertNotIn('synthetic-secret', path.read_text())
            with patch('circuit_bench.experiment.subprocess.run') as launch, \
                 patch('circuit_bench.experiment.shutil.which', return_value='/bin/true'):
                launch.return_value.returncode=0
                run(output)
                self.assertIn('/custom/modules', launch.call_args.kwargs['env']['PYTHONPATH'])

    def test_harness_identity_includes_agent_not_just_shared_image(self):
        model={'id':'fixture', 'effort':'none', 'context_window':10000}
        experiment={'model':model, 'images':{'shared':'sha256:image','verifier':'sha256:verifier'},
                    'verifier_image':'verifier', 'candidates':[
                        {'id':'a','image':'shared','agent':{'import_path':'one:Agent'}},
                        {'id':'b','image':'shared','agent':{'import_path':'two:Agent'}}]}
        rows=[]
        for candidate in ('a','b'):
            row={'model':'old-model','harness':'old-harness'}
            report.experiment_pairing(row, {}, {'experimentData':experiment,'experiment':'e',
                'candidate':candidate,'task':'full-adder','repeat':1}, {}, [])
            rows.append(row)
        self.assertNotEqual(rows[0]['harness'], rows[1]['harness'])

    def test_cross_harness_pair_uses_common_model_fields_and_actual_provider_id(self):
        model = {'id': 'fixture', 'effort': 'high', 'context_window': 10000, 'vision': True}
        experiment = {'model': model, 'candidates': [], 'images': {}, 'tasks': {}}
        manifest = {'model': {'requestedId': 'fixture', 'effort': 'high',
                    'contextWindow': 10000, 'api': 'openai-completions', 'reportedIds': ['snapshot']}}
        def conditions_for(manifest, experiment):
            conditions = {'model': manifest['model']}
            report.experiment_pairing({'model': 'old', 'harness': 'old'}, manifest,
                {'experimentData': experiment, 'experiment': 'e', 'candidate': 'a'}, conditions, [])
            return conditions['model']
        other = {'model': {**manifest['model'], 'vision': True}}
        self.assertEqual(conditions_for(manifest, experiment), conditions_for(other, experiment))
        self.assertNotEqual(conditions_for(manifest, experiment), conditions_for(
            {'model': {**manifest['model'], 'reportedIds': ['another-snapshot']}}, experiment))
        self.assertNotEqual(conditions_for(manifest, experiment), conditions_for(
            manifest, {**experiment, 'model': {**model, 'vision': False}}))


if __name__ == '__main__': unittest.main()
