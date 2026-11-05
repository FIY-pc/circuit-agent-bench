"""Synthetic integration check, not a model or circuit-solving baseline."""
import json
from pathlib import Path
import tempfile
from harbor.agents.installed.base import BaseInstalledAgent


class FixtureAgent(BaseInstalledAgent):
    @staticmethod
    def name():
        return 'circuit-fixture'

    async def install(self, environment):
        pass

    async def run(self, instruction, environment, context):
        await self.exec_as_agent(environment,
            'mkdir -p /logs/artifacts/project /logs/agent/episode && cp -r /workspace/input/. /logs/artifacts/project/')
        with tempfile.TemporaryDirectory() as temp:
            for name, data in (
                ('manifest.json', {'schema':'circuit-agent-bench.trial/v1', 'execution':'synthetic',
                                   'task':'full-adder','model':{'requestedId':self.model_name},
                                   'harness':{'adapter':'fixture'}}),
                ('run.json', {'outcome':'completed','finalized':True,'ledger':{'toolCalls':1,'failedCalls':0}})):
                path=Path(temp)/name;path.write_text(json.dumps(data))
                await environment.upload_file(path, '/logs/agent/episode/'+name)
