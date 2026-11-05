"""Build independent candidate/verifier images from explicit prepared source trees."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from circuit_bench.episode import sha, source_identity
from circuit_bench.host import Tree

RUNTIME_MODULES = ('__init__.py', 'host.py', 'runner.py', 'episode.py', 'meter.py')


def snapshot(tree, destination):
    """Only versioned product files, dependencies and checksum-pinned runtimes.

    Untracked sources must be staged first: they are not silently mixed into a
    supposedly reproducible image. Ignored credentials/profiles are never copied.
    """
    tree.check_runtimes()
    root = tree.root
    untracked = subprocess.check_output(['git', '-C', str(root), 'ls-files', '--others', '--exclude-standard', '-z'])
    if untracked:
        raise ValueError('Stage new product source files before building its image')
    names = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z']).split(b'\0')
    identity = source_identity(tree)
    files = {}
    for raw in sorted(set(names) - {b''}):
        name = os.fsdecode(raw)
        src, dst = root / name, destination / name
        if not src.exists():
            continue  # a local deletion is part of this candidate
        if src.is_symlink():
            raise ValueError('Tracked product symlink requires explicit packaging: ' + name)
        if not src.is_file():
            raise ValueError('Product submodules must be materialized explicitly: ' + name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        files[name] = sha(src)
    shutil.copytree(root / 'node_modules', destination / 'node_modules', symlinks=True)
    for link in (destination / 'node_modules').rglob('*'):
        if link.is_symlink() and not link.resolve().is_relative_to(destination.resolve()):
            raise ValueError('Dependency symlink escapes the product snapshot: ' + str(link.relative_to(destination)))
    lock = json.loads((root / 'config/runtime-lock.json').read_text())
    for artifact, filename in [('logisim.jar', 'Logisim-ITA.jar'), ('logisim-2.15', 'logisim-ita-cn-20200118.exe')]:
        src = root / '.workbench-cache' / filename
        if sha(src) != lock['artifacts'][artifact]['sha256']:
            raise ValueError('Runtime checksum mismatch: ' + filename)
        dst = destination / '.workbench-cache' / filename
        dst.parent.mkdir(exist_ok=True)
        shutil.copy2(src, dst)
        files['.workbench-cache/' + filename] = sha(src)
    identity['files'] = files
    return identity


def build(tree, tag, *, role='agent', base='ubuntu:24.04', network=None):
    if role not in ('agent', 'verifier') or not re.fullmatch(r'[a-zA-Z0-9_./:@-]+', tag + base):
        raise ValueError('Invalid image role or name')
    node = Path(os.environ.get('VIBE_LOGISIM_NODE') or shutil.which('node') or '')
    if not node.is_file():
        raise ValueError('Node 24+ must be available; set VIBE_LOGISIM_NODE if needed')
    pkg = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='circuit-image-') as temp:
        context = Path(temp)
        identity = snapshot(tree, context / 'product')
        (context / 'source.json').write_text(json.dumps(identity, indent=2))
        shutil.copy2(tree.host_binary(), context / 'host')
        shutil.copy2(node, context / 'node')
        adapter = context / 'adapter' / 'circuit_bench'; adapter.mkdir(parents=True)
        for path in pkg.glob('*.py'):
            if role == 'verifier' or path.name in RUNTIME_MODULES:
                shutil.copy2(path, adapter / path.name)
        dockerfile = f'''FROM {base}
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 openjdk-21-jdk-headless ca-certificates fontconfig fonts-dejavu-core fonts-noto-cjk libstdc++6 git curl tar bash && rm -rf /var/lib/apt/lists/*
COPY node /usr/local/bin/node
COPY host /usr/local/bin/vibe-workbench-host
COPY product /opt/product
COPY adapter /opt/bench
COPY source.json /opt/source.json
ENV PYTHONPATH=/opt/bench VIBE_BENCH_HOST=/usr/local/bin/vibe-workbench-host VIBE_BENCH_SOURCE_MANIFEST=/opt/source.json VIBE_LOGISIM_NODE=/usr/local/bin/node LANG=C.UTF-8 HOME=/home/agent
RUN useradd -m -u 1001 agent && mkdir -p /workspace /logs/agent /logs/artifacts /logs/verifier && chown -R agent:agent /workspace /logs /home/agent && chmod -R a+rX /opt
WORKDIR /opt/bench
USER agent
'''
        (context / 'Dockerfile').write_text(dockerfile)
        cmd = ['docker', 'build', '--tag', tag]
        if network:
            cmd.extend(['--network', network])
        cmd.append(str(context))
        subprocess.run(cmd, check=True)
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', tag]))[0]
    return {'image': tag, 'imageId': image['Id'], 'role': role, 'harness': identity,
            'nodeSha256': sha(node), 'base': base}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    g=p.add_mutually_exclusive_group(required=True); g.add_argument('--tree'); g.add_argument('--ref')
    p.add_argument('--tag', required=True); p.add_argument('--role', choices=['agent','verifier'], default='agent')
    p.add_argument('--base', default='ubuntu:24.04'); p.add_argument('--network')
    p.add_argument('--manifest', type=Path, required=True)
    a=p.parse_args()
    result=build(Tree.resolve(a.tree,a.ref),a.tag,role=a.role,base=a.base,network=a.network)
    a.manifest.parent.mkdir(parents=True,exist_ok=True)
    a.manifest.write_text(json.dumps(result,indent=2))

if __name__=='__main__': main()
