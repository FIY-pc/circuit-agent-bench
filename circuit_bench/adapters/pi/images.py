"""Install the locked upstream CLI independently of any circuit application."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request

from circuit_bench.identity import sha

RUNTIME_MODULES = ('__init__.py', 'identity.py', 'meter.py', 'adapters/__init__.py',
                   'adapters/pi/__init__.py', 'adapters/pi/episode.py')
LOGISIM = {
    'version': 'Logisim ITA 2.16.2.2',
    'url': 'https://github.com/Logisim-Ita/Logisim/releases/download/v2.16.2.2/Logisim-ITA.jar',
    'sha256': '9eb1aae5e87cf0c6e4af845dde00624338c6dce25d945b6482cad017aa6cbb34',
}


def simulator(path=None):
    path = Path(path) if path else Path('.cache/logisim/Logisim-ITA-2.16.2.2.jar')
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                with urllib.request.urlopen(LOGISIM['url'], timeout=60) as response:
                    shutil.copyfileobj(response, stream)
                stream.flush()
                if sha(temporary) != LOGISIM['sha256']:
                    raise ValueError('Downloaded Logisim runtime checksum mismatch')
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
    if sha(path) != LOGISIM['sha256']:
        raise ValueError('Logisim runtime checksum mismatch')
    return path


def build(tag, *, base='ubuntu:24.04', network=None, logisim_jar=None):
    if not re.fullmatch(r'[a-zA-Z0-9_./:@-]+', tag + base):
        raise ValueError('Invalid image name')
    node = Path(os.environ.get('CIRCUIT_BENCH_NODE') or shutil.which('node') or '')
    if not node.is_file():
        raise ValueError('Node 24+ must be available')
    here = Path(__file__).parent
    logisim = simulator(logisim_jar)
    with tempfile.TemporaryDirectory(prefix='pi-image-') as temp:
        context = Path(temp)
        runtime = context / 'pi'; runtime.mkdir()
        for name in ('package.json', 'package-lock.json'):
            shutil.copy2(here / name, runtime / name)
        subprocess.run(['npm', 'ci', '--prefix', str(runtime), '--ignore-scripts', '--no-audit', '--no-fund', '--prefer-offline'], check=True)
        files = {}
        for path in sorted(runtime.rglob('*')):
            if path.is_symlink():
                if not path.resolve().is_relative_to(runtime.resolve()):
                    raise ValueError('Dependency link escapes the installation')
                continue  # targets are hashed as regular files
            if path.is_file():
                files[path.relative_to(runtime).as_posix()] = sha(path)
        cli = 'node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js'
        version = json.loads((runtime / 'node_modules/@earendil-works/pi-coding-agent/package.json').read_text())['version']
        identity = {'name': 'pi', 'version': version, 'files': files,
                    'sourceSha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
                    'binarySha256': files[cli], 'nodeSha256': sha(node),
                    'lockSha256': files['package-lock.json']}
        shutil.copy2(logisim, context / 'Logisim-ITA.jar')
        (context / 'logisim').write_text('#!/bin/sh\nexec java -Djava.awt.headless=true -jar /opt/logisim/Logisim-ITA.jar "$@"\n')
        identity['simulator'] = LOGISIM
        identity['runtimeFiles'] = {'/opt/logisim/Logisim-ITA.jar': sha(logisim),
                                    '/usr/local/bin/logisim': sha(context / 'logisim')}
        (context / 'source.json').write_text(json.dumps(identity))
        shutil.copy2(node, context / 'node')
        for name in RUNTIME_MODULES:
            dst = context / 'adapter/circuit_bench' / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(here.parents[1] / name, dst)
        (context / 'Dockerfile').write_text(f'''FROM {base}
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 python-is-python3 file git curl ripgrep ca-certificates libstdc++6 bash openjdk-21-jdk-headless && rm -rf /var/lib/apt/lists/*
COPY node /usr/local/bin/node
COPY pi /opt/pi
COPY source.json /opt/pi-source.json
COPY Logisim-ITA.jar /opt/logisim/Logisim-ITA.jar
COPY logisim /usr/local/bin/logisim
COPY adapter /opt/bench
ENV PYTHONPATH=/opt/bench LANG=C.UTF-8 HOME=/home/agent
RUN useradd -m -u 1001 agent && mkdir -p /workspace /logs/agent /logs/artifacts && chown -R agent:agent /workspace /logs /home/agent && chmod -R a+rX /opt && chmod 755 /usr/local/bin/logisim
WORKDIR /workspace
USER agent
''')
        command = ['docker', 'build', '--tag', tag]
        if network:
            command += ['--network', network]
        subprocess.run([*command, str(context)], check=True)
    subprocess.run(['docker', 'run', '--rm', '--memory', '2g', '--network', 'none', tag,
                    'node', '/opt/pi/' + cli, '--offline', '--version'], check=True)
    version = subprocess.run(['docker', 'run', '--rm', '--memory', '2g', '--network', 'none', tag,
                              'logisim', '-version'], capture_output=True, text=True)
    # This upstream build exits 255 for informational flags, including -help.
    if version.returncode not in (0, 255) or version.stdout.strip() != '2.16.2.2.jar':
        raise ValueError('Prepared simulator did not report the pinned version')
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', tag]))[0]
    return {'image': tag, 'imageId': image['Id'], 'role': 'agent',
            'harness': {k: v for k, v in identity.items() if k != 'files'}, 'base': base}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--base', default='ubuntu:24.04')
    parser.add_argument('--network')
    parser.add_argument('--logisim-jar', type=Path, help='Existing checksum-matched runtime; otherwise download the pinned release')
    args = parser.parse_args()
    result = build(args.tag, base=args.base, network=args.network, logisim_jar=args.logisim_jar)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
