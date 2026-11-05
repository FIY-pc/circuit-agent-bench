"""Real process cancellation and native-format evidence retention, without models."""
import asyncio
import base64
import http.server
import importlib.util
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from circuit_bench.adapters.vibe_logisim.retention import NativeTranscript, interrupt_episode, write_json
from circuit_bench.adapters.vibe_logisim.host import EventStream, Host
from circuit_bench.adapters.vibe_logisim.runner import run_episode

ROOT = Path(__file__).resolve().parents[1]
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jK1sAAAAASUVORK5CYII=')
SERVICE = {'id': 'bench', 'model': 'fixture', 'effort': 'high', 'api': 'openai-completions',
           'apiKey': 'local-proxy', 'baseUrl': 'http://127.0.0.1:1/v1'}
TASK = {'id': 'fixture', 'artifact': 'design.circ', 'circuit': 'main',
        'prompt': 'Fixture only', 'timeout_minutes': 1}


def native_record():
    return {'schema': 'vibe-logisim.conversations/v2', 'workspaces': {'workspace': {
        'activeId': 'conversation', 'conversations': {'conversation': {
            'id': 'conversation', 'messages': [{'type': 'activity', 'text': 'UI preview'}],
            'transcript': [
                {'role': 'assistant', 'content': [{'type': 'toolCall', 'id': 'call',
                    'name': 'inspect_circuit', 'arguments': {'fullInput': 'I' * 9000}}]},
                {'role': 'toolResult', 'toolCallId': 'call', 'content': [
                    {'type': 'text', 'text': 'O' * 12000},
                    {'type': 'image', 'data': base64.b64encode(PNG).decode(), 'mimeType': 'image/png'}]}],
            'compactions': [{'through': 1, 'summary': 'Summary leaves raw data intact'}],
            'selection': {'model': 'fixture', 'maxOutput': 12345, 'apiKey': 'DO-NOT-EXPORT',
                          'baseUrl': 'private-service'},
            'auth': {'secret': 'DO-NOT-EXPORT'},
        }}}}}


class FixtureStream:
    events = []
    times = []

    def __init__(self, mode):
        self.mode = mode
        self.waits = 0

    def wait(self, predicate, timeout, start=0):
        self.waits += 1
        if self.waits == 1:
            return {'topic': 'resync'}
        if self.mode == 'block':
            while True:
                time.sleep(0.05)
        if self.mode == 'timeout':
            return None
        return {'data': {'event': {'status': 'completed'}}}


class FixtureHost:
    mode = 'completed'

    def __init__(self, tree, *, state, log):
        self.state = state
        self.run_dir = log.parent
        self.folder = None

    def start(self):
        pass

    def events(self, journal):
        return FixtureStream(self.mode)

    def call(self, op, **kwargs):
        if op == 'folderOpen':
            self.folder = Path(kwargs['path'])
        return {'id': 'document'}

    def agent(self, method, **kwargs):
        if method == 'ask':
            (self.folder / 'design.circ').write_text('saved candidate artifact')
            write_json(self.state / 'conversations.json', native_record())
            (self.state / 'auth.json').write_text('DO-NOT-EXPORT')
            (self.state / 'profile' / 'credentials.json').write_text('DO-NOT-EXPORT')
            (self.run_dir / 'ready').write_text('ready')
            if self.mode == 'keyboard':
                raise KeyboardInterrupt('fixture cancellation')
        return {'turnId': 'turn'}

    def close(self):
        if self.mode == 'close-error':
            raise RuntimeError('fixture shutdown failure')


def fixture_episode(root, mode):
    root = Path(root)
    fixture = root / 'input'
    fixture.mkdir()
    (fixture / 'design.circ').write_text('initial')
    tree = SimpleNamespace(describe=lambda: {'commit': 'fixture'})
    with patch('circuit_bench.adapters.vibe_logisim.runner.Host', FixtureHost), patch.object(FixtureHost, 'mode', mode):
        return run_episode(TASK, fixture, root / 'episode', tree, service=SERVICE)


class EpisodeRetentionTests(unittest.TestCase):
    def check_evidence(self, root, expected, *, recovered=False):
        out = root / 'episode'
        run = json.loads((out / 'run.json').read_text())
        self.assertEqual(run['outcome'], expected)
        self.assertTrue(run['finalized'])
        self.assertEqual(bool(run.get('recovered')), recovered)
        self.assertEqual((out / 'result/design.circ').read_text(), 'saved candidate artifact')
        self.assertEqual((root / 'collected/project/design.circ').read_text(), 'saved candidate artifact')
        exported = json.loads((out / 'trajectory/conversations.json').read_text())
        record = exported['workspaces']['workspace']['conversations']['conversation']
        self.assertEqual(record['transcript'], native_record()['workspaces']['workspace']['conversations']['conversation']['transcript'])
        self.assertEqual(record['selection']['maxOutput'], 12345)
        media = json.loads((out / 'trajectory/media.json').read_text())
        self.assertEqual(len(media), 1)
        self.assertEqual((out / 'trajectory' / media[0]['path']).read_bytes(), PNG)
        for path in out.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'DO-NOT-EXPORT', path.read_bytes())
        self.assertFalse(any(p.name in ('profile', 'auth.json', 'credentials.json') for p in out.rglob('*')))
        state = Path(json.loads((out / 'process.json').read_text())['state'])
        self.assertFalse(state.exists())
        return run

    def test_completion_keyboard_interrupt_timeout_and_shutdown_error(self):
        for mode, expected in [('completed', 'completed'), ('keyboard', 'interrupted'),
                               ('timeout', 'timeout'), ('close-error', 'error')]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
                with patch.dict(os.environ, {'CIRCUIT_BENCH_PROJECT_DIR': str(root / 'collected/project')}):
                    fixture_episode(root, mode)
                self.check_evidence(root, expected)
                self.assertEqual(handlers, {sig: signal.getsignal(sig) for sig in handlers})

    def start_child(self, root):
        env = {k: v for k, v in os.environ.items() if not k.startswith('CIRCUIT_BENCH_')}
        env.update(PYTHONPATH=str(ROOT), CIRCUIT_BENCH_PROJECT_DIR=str(root / 'collected/project'))
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--episode-child', str(root)],
                                 env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        deadline = time.monotonic() + 10
        while not (root / 'episode/trajectory/conversations.json').exists():
            if child.poll() is not None:
                self.fail('Fixture child failed: ' + str(child.communicate()))
            if time.monotonic() > deadline:
                child.kill()
                self.fail('Fixture child did not retain its native transcript')
            time.sleep(0.02)
        # These assertions happen before any shutdown or finalizer.
        self.assertEqual((root / 'collected/project/design.circ').read_text(), 'saved candidate artifact')
        self.assertEqual(json.loads((root / 'episode/run.json').read_text())['outcome'], 'running')
        return child

    @unittest.skipUnless(sys.platform == 'linux', 'Harbor process recovery uses Linux process identities')
    def test_actual_sigterm_finishes_and_preserves_native_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child = self.start_child(root)
            child.terminate()
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, stdout + stderr)
            self.check_evidence(root, 'interrupted')

    @unittest.skipUnless(sys.platform == 'linux', 'Harbor process recovery uses Linux process identities')
    def test_actual_sigkill_keeps_live_artifact_and_recovers_last_native_save(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child = self.start_child(root)
            child.kill()
            child.communicate(timeout=10)
            interrupt_episode(root / 'episode', timeout=0.1)
            run = self.check_evidence(root, 'interrupted', recovered=True)
            self.assertIsNone(run['ledger'])  # absent accounting is not zero
            before = (root / 'episode/run.json').read_bytes()
            interrupt_episode(root / 'episode', timeout=0.1)
            self.assertEqual(before, (root / 'episode/run.json').read_bytes())

    @unittest.skipUnless(sys.platform == 'linux', 'Harbor process recovery uses Linux process identities')
    def test_harbor_recovery_signals_a_live_episode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child = self.start_child(root)
            interrupt_episode(root / 'episode', timeout=5)
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, stdout + stderr)
            self.check_evidence(root, 'interrupted')

    def test_retained_snapshot_survives_discarded_private_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / 'state'
            state.mkdir()
            write_json(state / 'conversations.json', native_record())
            self.assertEqual(NativeTranscript(state, root / 'out').close()['status'], 'retained')
            shutil.rmtree(state)
            status = NativeTranscript(state, root / 'out').close()
            self.assertEqual(status['status'], 'retained')
            self.assertTrue(status['sourceMissing'])

    def test_repeated_compaction_saves_keep_full_native_observations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / 'state'
            state.mkdir()
            native = native_record()
            record = native['workspaces']['workspace']['conversations']['conversation']
            original = json.loads(json.dumps(record['transcript']))
            record['compactions'] = []
            exporter = NativeTranscript(state, root / 'out')
            write_json(state / 'conversations.json', native)
            exporter.snapshot()
            for timestamp in (1, 2):
                # Product compaction stores a projection checkpoint alongside
                # the intact raw transcript, then subsequent messages append.
                record['compactions'].append({'through': len(record['transcript']),
                    'summary': 'Earlier context summarized', 'timestamp': timestamp})
                record['transcript'].append({'role': 'user', 'content': 'Continue',
                                             'timestamp': timestamp})
                write_json(state / 'conversations.json', native)
                exporter.snapshot()
            self.assertEqual(exporter.close()['status'], 'retained')
            exported = json.loads((root / 'out/trajectory/conversations.json').read_text())
            retained = exported['workspaces']['workspace']['conversations']['conversation']
            self.assertEqual(retained['transcript'][:len(original)], original)
            self.assertEqual(retained['compactions'], record['compactions'])
            media = json.loads((root / 'out/trajectory/media.json').read_text())
            self.assertEqual((root / 'out/trajectory' / media[0]['path']).read_bytes(), PNG)

    def test_native_symlink_never_exports_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / 'state'
            state.mkdir()
            (state / 'profile.json').write_text('DO-NOT-EXPORT')
            (state / 'conversations.json').symlink_to(state / 'profile.json')
            status = NativeTranscript(state, root / 'out').close()
            self.assertEqual(status['status'], 'error')
            self.assertFalse((root / 'out/trajectory/conversations.json').exists())

    def test_cancellation_during_initial_copy_still_records_partial_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = root / 'input'
            fixture.mkdir()
            (fixture / 'design.circ').write_text('initial')
            copytree = shutil.copytree

            def interrupted_copy(source, target, **kwargs):
                copytree(source, target, **kwargs)
                raise KeyboardInterrupt('cancelled during initialization')

            with patch('circuit_bench.adapters.vibe_logisim.runner.shutil.copytree', side_effect=interrupted_copy), \
                 patch.dict(os.environ, {'CIRCUIT_BENCH_PROJECT_DIR': ''}):
                run = run_episode(TASK, fixture, root / 'episode',
                                  SimpleNamespace(describe=lambda: {}), service=SERVICE)
            self.assertEqual(run['outcome'], 'interrupted')
            self.assertTrue(run['finalized'])
            self.assertEqual((root / 'episode/result/design.circ').read_text(), 'initial')

    def test_idle_event_reader_cannot_block_host_shutdown(self):
        ended = threading.Event()

        class Server(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(b'data: {"topic":"resync"}\n\n')
                self.wfile.flush()
                ended.wait(3)

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            host = Host(None)
            stream = EventStream(f'http://127.0.0.1:{server.server_port}')
            self.assertIsNotNone(stream.wait(lambda event: True, 1))
            host._streams.append(stream)
            host.proc = SimpleNamespace(pid=123, stdout=None, poll=lambda: None,
                                        send_signal=lambda sig: ended.set(), wait=lambda **kw: None)
            with patch('circuit_bench.adapters.vibe_logisim.host.os.killpg'):
                start = time.monotonic()
                host.close()
                self.assertLess(time.monotonic() - start, 1)
        finally:
            ended.set()
            server.shutdown()
            server.server_close()


@unittest.skipUnless(importlib.util.find_spec('harbor') is not None, 'Harbor extra not installed')
class HarborCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_exec_is_finalized_before_run_returns(self):
        from circuit_bench.adapters.vibe_logisim.harbor_agent import VibeLogisim
        started = asyncio.Event()
        calls = []

        async def execute(environment, command, **kwargs):
            calls.append((command, kwargs))
            if 'circuit_bench.adapters.vibe_logisim.episode ' in command:
                started.set()
                await asyncio.Event().wait()
            await asyncio.sleep(0)

        async def upload(*args):
            pass

        agent = SimpleNamespace(model_name='fixture', effort='high', context_window=10000,
                                api='openai-completions', max_output=None, exec_as_agent=execute,
                                logger=logging.getLogger('fixture'))
        with patch.dict(os.environ, {'CIRCUIT_BENCH_BASE_URL': 'http://127.0.0.1:1/v1', 'CIRCUIT_BENCH_API_KEY': 'sentinel'}):
            task = asyncio.create_task(VibeLogisim.run(agent, 'fixture', SimpleNamespace(upload_file=upload), None))
            await asyncio.wait_for(started.wait(), timeout=5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1]['env']['CIRCUIT_BENCH_PROJECT_DIR'], '/logs/artifacts/project')
        self.assertIn('circuit_bench.adapters.vibe_logisim.retention --interrupt', calls[1][0])
        self.assertNotIn('env', calls[1][1])


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--episode-child':
        fixture_episode(sys.argv[2], 'block')
    else:
        unittest.main()
