"""Harbor lifecycle around an unmodified Pi CLI process; no agent loop here."""
import asyncio
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile

from harbor.agents.installed.base import BaseInstalledAgent
from . import configuration as validate


class Pi(BaseInstalledAgent):
    def __init__(self, *args, configuration, **kwargs):
        super().__init__(*args, **kwargs)
        self.configuration = validate(configuration)

    @staticmethod
    def name():
        return 'pi'

    async def install(self, environment):
        await self.exec_as_agent(environment,
            'test -f /workspace/task.json && node /opt/pi/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js --offline --version')

    async def run(self, instruction, environment, context):
        config = {**self.configuration, 'id': self.model_name}
        with tempfile.TemporaryDirectory() as temp:
            for name, data in [('instruction.md', instruction), ('model.json', json.dumps(config))]:
                path = Path(temp) / name
                path.write_text(data)
                await environment.upload_file(path, '/workspace/' + name)
        command = ['python3', '-m', 'circuit_bench.adapters.pi.episode',
                   '--task', '/workspace/task.json', '--instruction', '/workspace/instruction.md',
                   '--input', '/workspace/input', '--model-config', '/workspace/model.json',
                   '--output', '/logs/agent/episode', '--project', '/logs/artifacts/project']
        try:
            await self.exec_as_agent(environment, shlex.join(command), cwd='/workspace',
                env={k: os.environ[k] for k in ('CIRCUIT_BENCH_BASE_URL', 'CIRCUIT_BENCH_API_KEY')})
        finally:
            failure = sys.exc_info()[1]
            try:
                await asyncio.shield(self.exec_as_agent(environment,
                    'python3 -m circuit_bench.adapters.pi.episode --interrupt /logs/agent/episode',
                    timeout_sec=45))
            except Exception:
                if failure is None:
                    raise
                self.logger.exception('Pi finalization failed; native session and project remain in collected logs')

    def populate_context_post_run(self, context):
        path = self.logs_dir / 'episode/run.json'
        if not path.is_file():
            return
        run = json.loads(path.read_text())
        usage = run.get('meter') or {}
        context.n_input_tokens = usage.get('input')
        context.n_output_tokens = usage.get('output')
        context.n_cache_tokens = usage.get('cachedInput')
        context.metadata = {'outcome': run['outcome'], 'meter': usage, 'tools': run.get('ledger')}
