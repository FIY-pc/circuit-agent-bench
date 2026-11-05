"""Harbor 0.25.0 installed-agent adapter for a prepared Vibe Logisim image.

The image supplies /opt/product, /opt/bench and /workspace/task.json. Only
the task instruction enters the product. Harbor owns scheduling/isolation;
the product owns its tools, retries, context management and stopping policy.
"""
from pathlib import Path
import asyncio
import json
import os
import shlex
import sys
import tempfile

from harbor.agents.installed.base import BaseInstalledAgent
from harbor.models.agent.context import AgentContext


class VibeLogisim(BaseInstalledAgent):
    def __init__(self, *args, effort='high', context_window=None, api='openai-completions', max_output=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not context_window or int(context_window) <= 0:
            raise ValueError('context_window is required for the selected model')
        self.effort, self.context_window, self.api = effort, int(context_window), api
        self.max_output = max_output

    @staticmethod
    def name():
        return 'vibe-logisim'

    async def install(self, environment):
        await self.exec_as_agent(environment, 'test -f /workspace/task.json && python3 -m circuit_bench.adapters.vibe_logisim.preflight', cwd='/opt/bench')

    async def run(self, instruction, environment, context):
        if not self.model_name:
            raise ValueError('Specify --model with the exact provider model id')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'instruction.md'
            path.write_text(instruction)
            await environment.upload_file(path, '/workspace/instruction.md')
        command = ['python3', '-m', 'circuit_bench.adapters.vibe_logisim.episode', '--task', '/workspace/task.json',
                   '--instruction', '/workspace/instruction.md', '--input', '/workspace/input',
                   '--tree', '/opt/product', '--output', '/logs/agent/episode',
                   '--model', self.model_name, '--effort', self.effort,
                   '--context-window', str(self.context_window), '--api', self.api, '--isolation', 'harbor']
        if self.max_output is not None:
            command += ['--max-output', str(self.max_output)]
        # Environment is passed through Harbor's scoped execution, not written
        # into a task, Dockerfile, job YAML or a product authentication profile.
        env = {k: os.environ[k] for k in ('CIRCUIT_BENCH_BASE_URL', 'CIRCUIT_BENCH_API_KEY')}
        env['CIRCUIT_BENCH_PROJECT_DIR'] = '/logs/artifacts/project'
        try:
            await self.exec_as_agent(environment, ' '.join(map(shlex.quote, command)),
                                     env=env, cwd='/opt/bench')
        finally:
            # Cancelling docker exec does not signal its container process.
            # Finalize while the environment is still available, before Harbor
            # collects artifacts and stops it. No model credentials are needed.
            failure = sys.exc_info()[1]
            try:
                await asyncio.shield(self.exec_as_agent(environment,
                    'python3 -m circuit_bench.adapters.vibe_logisim.retention --interrupt /logs/agent/episode',
                    cwd='/opt/bench', timeout_sec=45))
            except Exception:
                if failure is None:
                    raise
                self.logger.exception('Episode finalization failed; collected project remains available')

    def populate_context_post_run(self, context: AgentContext):
        path = self.logs_dir / 'episode/run.json'
        if not path.is_file():
            return
        run = json.loads(path.read_text())
        usage = run.get('meter') or {}
        context.n_input_tokens = usage.get('input')
        context.n_output_tokens = usage.get('output')
        context.n_cache_tokens = usage.get('cachedInput')
        context.metadata = {'outcome': run['outcome'], 'meter': usage, 'tools': run.get('ledger')}
