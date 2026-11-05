import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest
from circuit_bench.host import Tree
from circuit_bench.experiment import prepare, validate_job
from circuit_bench.episode import sha, digest_tree

class VersionTests(unittest.TestCase):
    def test_changed_prepared_model_and_instruction_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); config=root/'jobs-config/job.json';package=root/'packages/job'
            config.parent.mkdir();package.mkdir(parents=True)
            config.write_text('{"model":"fixed-model"}')
            instruction=package/'instruction.md';instruction.write_text('Build the circuit.')
            job={'name':'job','config':str(config),'configSha256':sha(config),'packageSha256':digest_tree(package)}
            validate_job(root,job)
            original=config.read_text();config.write_text('{"model":"changed-model"}')
            with self.assertRaisesRegex(ValueError,'config changed'):
                validate_job(root,job)
            config.write_text(original);instruction.write_text('Different task.')
            with self.assertRaisesRegex(ValueError,'package changed'):
                validate_job(root,job)

    def test_ref_fetch_preserves_source_checkout_and_rejects_modified_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); source=root/'product'; cache=root/'cache'
            subprocess.run(['git','init','-q','-b','main',str(source)],check=True)
            subprocess.run(['git','-C',str(source),'config','user.name','Fixture'],check=True)
            subprocess.run(['git','-C',str(source),'config','user.email','fixture@example.com'],check=True)
            (source/'file').write_text('original')
            subprocess.run(['git','-C',str(source),'add','file'],check=True)
            subprocess.run(['git','-C',str(source),'commit','-qm','Initial fixture'],check=True)
            subprocess.run(['git','-C',str(source),'tag','v1'],check=True)
            (source/'file').write_text('working change')
            with patch('circuit_bench.host.CACHE',cache),patch.dict('os.environ',{'VIBE_BENCH_REPO':str(source)}),patch.object(Tree,'_prepare',lambda self:self):
                tree=Tree.resolve(ref='v1')
                self.assertEqual((tree.root/'file').read_text(),'original')
                self.assertEqual((source/'file').read_text(),'working change')
                self.assertEqual(Tree.resolve(ref='v1').commit,tree.commit)
                (tree.root/'file').write_text('cache changed')
                with self.assertRaisesRegex(ValueError,'Cached ref tree'):
                    Tree.resolve(ref='v1')

    def test_experiment_config_does_not_store_service_credentials(self):
        try:
            import harbor
        except ImportError:
            self.skipTest('Harbor extra not installed')
        root=Path(__file__).resolve().parents[1]
        spec={'model':{'id':'exact-model','effort':'high','context_window':123456},
              'candidates':[{'id':'a','image':'candidate:a'},{'id':'b','image':'candidate:b'}],
              'verifier_image':'verifier:fixed','tasks':['full-adder','counter4'],'repeats':2}
        with tempfile.TemporaryDirectory() as temp,patch('circuit_bench.experiment.subprocess.check_output',return_value=b'[{"Id":"sha256:fixed"}]'),patch.dict('os.environ',{'VIBE_BENCH_API_KEY':'secret-sentinel','VIBE_BENCH_BASE_URL':'https://private.example/v1'}):
            output=Path(temp)/'experiment'
            frozen=prepare(spec,root,output)
            self.assertEqual(len(frozen['jobs']),8)
            self.assertEqual([j['candidate'] for j in frozen['jobs'][:4]],['a','b','a','b'])
            for f in output.rglob('*'):
                if f.is_file():
                    text=f.read_text()
                    self.assertNotIn('secret-sentinel',text)
                    self.assertNotIn('private.example',text)
            cfg=json.loads(Path(frozen['jobs'][0]['config']).read_text())
            self.assertTrue(cfg['agents'][0]['skip_model_parsing'])
            self.assertEqual(cfg['retry']['max_retries'],0)

if __name__=='__main__':unittest.main()
