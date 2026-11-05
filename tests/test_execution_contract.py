import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from circuit_bench.package import package
from circuit_bench.adapters.vibe_logisim.runner import run_episode
from circuit_bench.meter import usage_totals

ROOT=Path(__file__).resolve().parents[1]

class ExecutionContract(unittest.TestCase):
    def test_task_package_separates_answers_and_disables_verifier_network(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'task'
            package(ROOT/'tasks/full-adder',out,'candidate:test','verifier:fixed')
            visible={p.name for p in (out/'environment').rglob('*')}
            self.assertNotIn('oracle.json',visible)
            self.assertNotIn('reference.circ',visible)
            self.assertTrue((out/'tests/oracle.json').is_file())
            self.assertFalse((out/'tests/reference.circ').exists())
            self.assertIn('network_mode: none',(out/'tests/docker-compose.yaml').read_text())
            self.assertEqual((out/'instruction.md').read_bytes(),(ROOT/'tasks/full-adder/instruction.md').read_bytes())

    def test_startup_failure_keeps_artifact_and_error(self):
        class BrokenHost:
            def __init__(self,*a,**kw):pass
            def start(self):raise RuntimeError('infrastructure unavailable')
            def close(self):pass
        class Tree:
            def describe(self):return {'commit':'fixed'}
        service={'id':'bench','model':'example','effort':'high','api':'openai-completions',
                 'apiKey':'local-proxy','baseUrl':'http://localhost:1/v1'}
        with tempfile.TemporaryDirectory() as temp,patch('circuit_bench.adapters.vibe_logisim.runner.Host',BrokenHost):
            out=Path(temp)/'trial'
            task=json.loads((ROOT/'tasks/full-adder/task.json').read_text())
            task['prompt']='Build the circuit.'
            result=run_episode(task,ROOT/'tasks/full-adder/environment/input',out,Tree(),service=service)
            self.assertEqual(result['outcome'],'error')
            self.assertIn('infrastructure unavailable',result['error'])
            self.assertTrue((out/'result/design.circ').is_file())
            self.assertEqual(json.loads((out/'run.json').read_text())['outcome'],'error')

    def test_started_unfinished_request_is_unknown_cost(self):
        result=usage_totals([{'request':1,'usage':{'prompt_tokens':10,'completion_tokens':2}},
                             {'request':2,'usage':None,'status':None}])
        self.assertEqual(result['requests'],2)
        self.assertEqual(result['requestsWithUsage'],1)
        self.assertIsNone(result['input'])

if __name__=='__main__':unittest.main()
