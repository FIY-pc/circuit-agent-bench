"""Prepare a circuit experiment with an explicit harness and let Harbor schedule trials."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
from circuit_bench.identity import digest_tree, sha
from circuit_bench.package import package
from circuit_bench.adapters import agent_config, required_environment


def prepare(spec, root, output):
    from harbor.models.job.config import JobConfig
    root,output=Path(root),Path(output)
    if output.exists():
        raise ValueError('Use a new experiment output directory; previous attempts are never replaced')
    if spec.get('track','unassisted') != 'unassisted':
        raise ValueError('Human-assisted runs need a separate recorded protocol; this runner is unassisted')
    model=spec['model']; model_id=model.get('id') or os.environ.get('CIRCUIT_BENCH_MODEL')
    if not model_id or type(model.get('context_window')) is not int or model['context_window']<=0:
        raise ValueError('Exact model ID and context window are required')
    if model.get('max_output') is not None and (type(model['max_output']) is not int or model['max_output'] <= 0):
        raise ValueError('max_output must be a positive integer')
    candidates=spec['candidates']; tasks=spec['tasks']; repeats=spec.get('repeats',1)
    if not candidates or not tasks or repeats<1 or len(set(tasks))!=len(tasks):
        raise ValueError('Supply distinct tasks, candidates, and positive repeats')
    if len({c['id'] for c in candidates})!=len(candidates):
        raise ValueError('Candidate IDs must be unique')
    resolved_agents = {c['id']: agent_config(c, {**model, 'id': model_id}) for c in candidates}
    required_env = sorted({key for c in candidates for key in required_environment(c)})
    output.mkdir(parents=True)
    frozen={'schema':'circuit-agent-bench.experiment/v1','track':'unassisted',
        'model':{**model,'id':model_id},'question':spec.get('question'),
        'phase':spec.get('phase','development'),'repeats':repeats,
        'verifier_image':spec['verifier_image'], 'required_env': required_env,
        'serviceFingerprint':hashlib.sha256(os.environ.get('CIRCUIT_BENCH_BASE_URL','').rstrip('/').encode()).hexdigest() if 'CIRCUIT_BENCH_BASE_URL' in required_env and os.environ.get('CIRCUIT_BENCH_BASE_URL') else None,
        'images':{},'tasks':{},'candidates':candidates}
    # Images are already built from selected tree/ref. Record their immutable IDs.
    for tag in {spec['verifier_image'],*(c['image'] for c in candidates)}:
        image=json.loads(subprocess.check_output(['docker','image','inspect',tag]))[0]
        frozen['images'][tag]=image['Id']
    jobs=[]
    # Interleave candidates per task/repeat. Harbor still owns each job lifecycle,
    # isolation, artifacts, verifier, and timeouts. No agent-loop retry is added.
    for repeat in range(repeats):
        for task_id in tasks:
            task_dir=root/'tasks'/task_id
            if not task_dir.resolve().is_relative_to((root/'tasks').resolve()):
                raise ValueError('Task must be inside the task collection')
            frozen['tasks'][task_id]=digest_tree(task_dir)
            for candidate in candidates:
                alias=candidate['id']
                if not alias.replace('-','').replace('_','').isalnum():
                    raise ValueError('Candidate ID must be a simple label')
                name=f'{task_id}-{alias}-r{repeat+1}'
                task_out=output/'packages'/name
                package(task_dir,task_out,candidate['image'],spec['verifier_image'])
                config={'job_name':name,'jobs_dir':str((output/'jobs').resolve()),
                    'n_attempts':1,'n_concurrent_trials':1,'retry':{'max_retries':0},
                    'tasks':[{'path':str(task_out.resolve())}],
                    'agents':[resolved_agents[alias]]}
                validated=JobConfig.model_validate(config)
                target=output/'jobs-config'/f'{name}.json';target.parent.mkdir(exist_ok=True)
                target.write_text(validated.model_dump_json(indent=2))
                jobs.append({'name':name,'config':str(target.resolve()),'task':task_id,
                             'candidate':alias,'repeat':repeat+1,
                             'configSha256':sha(target),'packageSha256':digest_tree(task_out)})
    frozen['jobs']=jobs
    (output/'experiment.json').write_text(json.dumps(frozen,ensure_ascii=False,indent=2))
    return frozen


def validate_job(output, job):
    config = Path(job['config'])
    expected_config = (Path(output)/'jobs-config'/f"{job['name']}.json").resolve()
    if config.resolve() != expected_config or not config.is_file() or sha(config) != job.get('configSha256'):
        raise ValueError('Prepared job config changed or has no digest: '+job['name'])
    package_path = Path(output)/'packages'/job['name']
    if not package_path.is_dir() or digest_tree(package_path) != job.get('packageSha256'):
        raise ValueError('Prepared task package changed or has no digest: '+job['name'])


def run(output):
    output=Path(output)
    frozen=json.loads((output/'experiment.json').read_text())
    for tag, expected in frozen['images'].items():
        image=json.loads(subprocess.check_output(['docker','image','inspect',tag]))[0]
        if image['Id']!=expected:
            raise ValueError('Image changed after experiment preparation: '+tag)
    for key in frozen.get('required_env', ('CIRCUIT_BENCH_BASE_URL','CIRCUIT_BENCH_API_KEY')):
        if not os.environ.get(key): raise ValueError('Missing '+key)
    service = hashlib.sha256(os.environ.get('CIRCUIT_BENCH_BASE_URL','').rstrip('/').encode()).hexdigest()
    if frozen.get('serviceFingerprint') not in (None, service):
        raise ValueError('Inference service differs from the prepared experiment')
    for job in frozen['jobs']:
        # Resume only unstarted jobs. Interrupted/failed attempts stay visible;
        # request another repeat to try again, not an overwrite.
        job_dir=output/'jobs'/job['name']
        if job_dir.exists():
            print('Retaining existing attempt:',job['name'],flush=True);continue
        validate_job(output, job)
        print('Running:',job['name'],flush=True)
        harbor = Path(sys.executable).parent / 'harbor'
        if not harbor.is_file():
            harbor = Path(shutil.which('harbor') or '')
        if not harbor.is_file():
            raise ValueError('Install the harbor extra into this Python environment')
        result=subprocess.run([str(harbor),'run','--config',job['config']],
                              env={**os.environ,'PYTHONPATH':os.pathsep.join(filter(None, (str(Path(__file__).resolve().parents[1]), os.environ.get('PYTHONPATH')))),'LITELLM_LOCAL_MODEL_COST_MAP':'True'})
        with (output/'launches.jsonl').open('a') as f:
            f.write(json.dumps({'job':job['name'],'exit_code':result.returncode})+'\n')
    return frozen


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('prepare');a.add_argument('spec',type=Path);a.add_argument('--output',type=Path,required=True)
    a.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    a=sub.add_parser('run');a.add_argument('output',type=Path)
    a=p.parse_args()
    if a.command=='prepare':prepare(json.loads(a.spec.read_text()),a.root,a.output)
    else:run(a.output)

if __name__=='__main__':main()
