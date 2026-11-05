"""Package construction tasks using Harbor's task schema.

Runtime images are supplied separately: the candidate contains the actual
product; the verifier contains a fixed product and circuit_bench.grading. This command
does not build images, contact models, or claim the images were tested.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def package(source, output, agent_image, verifier_image):
    source, output = Path(source), Path(output)
    meta = json.loads((source / 'task.json').read_text())
    if output.exists():
        raise ValueError(f'Output already exists: {output}')
    output.mkdir(parents=True)
    shutil.copy2(source / 'instruction.md', output / 'instruction.md')
    env, tests = output / 'environment', output / 'tests'
    shutil.copytree(source / 'environment/input', env / 'input')
    shutil.copy2(source / 'task.json', env / 'task.json')
    tests.mkdir()
    shutil.copy2(source / 'tests/oracle.json', tests / 'oracle.json')
    shutil.copy2(source / 'task.json', tests / 'task.json')
    # The grader expects task/tests/oracle.json. Only the independent verifier
    # receives this directory; the agent image contains input + visible metadata.
    (env / 'Dockerfile').write_text(f'FROM {agent_image}\nCOPY input /workspace/input\nCOPY task.json /workspace/task.json\nWORKDIR /workspace\n')
    (tests / 'Dockerfile').write_text(f'FROM {verifier_image}\nCOPY oracle.json /evaluation/task/tests/oracle.json\nCOPY task.json /evaluation/task/task.json\nCOPY test.sh /tests/test.sh\nWORKDIR /opt/bench\n')
    (output / 'task.toml').write_text(
        'schema_version = "1.4"\nartifacts = ["/logs/artifacts/project"]\n'
        f'[task]\nname = "circuits/{meta["id"]}"\n'
        f'[agent]\ntimeout_sec = {meta["timeout_minutes"] * 60 + 180}\n'
        '[environment]\ncpus = 2\nmemory_mb = 6144\n'
        '[verifier]\nenvironment_mode = "separate"\ntimeout_sec = 300\nnetwork_mode = "no-network"\n'
        '[verifier.environment]\ncpus = 2\nmemory_mb = 6144\n')
    artifact = meta['artifact']
    if Path(artifact).name != artifact:
        raise ValueError('Initial tasks require a single artifact basename')
    script = f'''#!/bin/bash
set -uo pipefail
python3 -m circuit_bench.grading --task /evaluation/task --artifact /logs/artifacts/project/{artifact} --tree /opt/product --out /logs/verifier/grade.json
status=$?
if [ "$status" -ge 2 ]; then exit "$status"; fi
python3 - <<'PY'
import json
from pathlib import Path
r = json.loads(Path('/logs/verifier/grade.json').read_text())
if r['status'] == 'error':
    raise SystemExit(2)
Path('/logs/verifier/reward.json').write_text(json.dumps({{'functional_pass': int(r['passed'] is True)}}))
PY
'''
    (tests / 'test.sh').write_text(script)
    (tests / 'test.sh').chmod(0o755)
    hashes = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (source / 'task.json', source / 'instruction.md', source / 'tests/oracle.json')}
    (output / 'provenance.json').write_text(json.dumps({'source': hashes, 'agent_image': agent_image, 'verifier_image': verifier_image}, indent=2))
    return output


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--agent-image', required=True)
    p.add_argument('--verifier-image', required=True)
    a = p.parse_args()
    package(a.task, a.output, a.agent_image, a.verifier_image)
