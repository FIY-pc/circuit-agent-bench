import json,tempfile,unittest
from pathlib import Path
from circuit_bench.report import discover_attempts, aggregate

class HarborDiscovery(unittest.TestCase):
    def test_infrastructure_trials_retain_cost_without_capability_credit(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name, execution in [('model', 'real-model'), ('smoke', 'scripted')]:
                path=root/name;path.mkdir()
                (path/'manifest.json').write_text(json.dumps({'task':'full-adder','execution':execution}))
                (path/'run.json').write_text(json.dumps({'outcome':'completed','meter':{'input':10,'output':2}}))
                (path/'grade.json').write_text(json.dumps({'status':'passed','passed':True}))
            result=aggregate([root])
            self.assertEqual(result['attemptCount'],2)
            self.assertEqual(result['nonModelAttempts'],1)
            self.assertEqual(sum(g['attempts'] for g in result['capabilityByTask']),1)
            self.assertEqual(result['metrics']['input']['knownSum'],20)

    def test_job_and_artifact_manifests_are_not_trials(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);job=root/'job';trial=job/'trial'
            (trial/'artifacts').mkdir(parents=True)
            (trial/'verifier').mkdir()
            (job/'config.json').write_text('{}')
            (job/'result.json').write_text(json.dumps({'n_total_trials':1,'stats':{}}))
            (trial/'result.json').write_text(json.dumps({'trial_name':'trial','task_id':'task'}))
            (trial/'artifacts/manifest.json').write_text('{"files":[]}')
            found=discover_attempts([root])
            self.assertEqual(len(found),1)
            self.assertEqual(found[0][0],trial/'agent/episode')

if __name__=='__main__':unittest.main()
