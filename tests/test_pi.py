"""Contract tests; opt-in tests use the actual unmodified CLI with a fake service."""
import http.server
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from circuit_bench.adapters import agent_config
from circuit_bench.adapters.pi import configuration
from circuit_bench.adapters.pi.episode import execute, model_registry, summarize, records
from circuit_bench.adapters.pi.images import RUNTIME_MODULES

MODEL = {'id': 'exact-model', 'context_window': 262144, 'max_output': 65536,
         'effort': 'high', 'api': 'openai-completions', 'vision': True}


class PiTests(unittest.TestCase):
    def test_actual_model_capacities_are_required_and_preserved(self):
        for key in ('context_window', 'max_output', 'vision'):
            missing = {k: v for k, v in MODEL.items() if k != key}
            with self.assertRaises(ValueError):
                agent_config({'adapter': 'pi'}, missing)
        config = agent_config({'adapter': 'pi'}, MODEL)
        self.assertEqual(config['model_name'], MODEL['id'])
        registry = model_registry(configuration(MODEL), 'http://127.0.0.1:1234/v1')
        model = registry['providers']['benchmark']['models'][0]
        self.assertEqual((model['contextWindow'], model['maxTokens']), (262144, 65536))
        self.assertEqual(model['input'], ['text', 'image'])
        self.assertTrue(all('vibe_logisim' not in p and 'grading' not in p for p in RUNTIME_MODULES))
        with self.assertRaises(ValueError):
            configuration({**MODEL, 'effort': 'max'})

    def test_final_error_not_success_and_interrupted_tool_not_failed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = [
                {'type': 'tool_execution_start', 'toolCallId': '1', 'toolName': 'read'},
                {'type': 'compaction_start'}, {'type': 'auto_retry_start'},
                {'type': 'summarization_retry_attempt_start'},
                {'type': 'message_end', 'message': {'role': 'assistant', 'content': [],
                    'stopReason': 'error', 'errorMessage': 'Request failed'}},
            ]
            (root / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events) + '{')
            ledger, _, error, settled = summarize(root)
            self.assertEqual((ledger['toolCalls'], ledger['interruptedCalls'], ledger['failedCalls']), (1, 1, 0))
            self.assertEqual((ledger['compactions'], ledger['retries']), (1, 2))
            self.assertEqual(error, 'Request failed')
            self.assertFalse(settled)

    @unittest.skipUnless(os.environ.get('PI_TEST_CLI'), 'Set PI_TEST_CLI to a stock installed Pi CLI')
    def test_stock_cli_tools_usage_and_native_session(self):
        self.run_cli(timeout=False)

    @unittest.skipUnless(os.environ.get('PI_TEST_CLI'), 'Set PI_TEST_CLI to a stock installed Pi CLI')
    def test_stock_cli_timeout_retains_artifact_and_session(self):
        self.run_cli(timeout=True)

    @unittest.skipUnless(os.environ.get('PI_TEST_CLI'), 'Set PI_TEST_CLI to a stock installed Pi CLI')
    def test_stock_cli_compacts_without_losing_native_history(self):
        self.run_cli(timeout=False, compact=True)

    def run_cli(self, timeout, compact=False):
        requests = []
        tool_turns = []
        class Provider(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(request)
                if request.get('tools'):
                    tool_turns.append(request)
                call = len(tool_turns)
                summary = not request.get('tools')
                if call == 1:
                    tool, args = 'write', {'path': 'design.circ', 'content': '<project>saved</project>\n'}
                else:
                    tool, args = 'bash', {'command': 'sleep 60' if timeout else 'exit 7'}
                delta = {'role': 'assistant'}
                if compact and call == 1 and not summary:
                    delta['content'] = 'synthetic history ' * 8000
                if call <= 2 and not summary:
                    delta['tool_calls'] = [{'index': 0, 'id': f'call_{call}', 'type': 'function',
                        'function': {'name': tool, 'arguments': json.dumps(args)}}]
                else:
                    delta['content'] = 'Saved.\u2028Finished.'
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                chunks = [
                    {'id': 'fixture', 'model': MODEL['id'], 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                    {'id': 'fixture', 'model': MODEL['id'], 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls' if call <= 2 and not summary else 'stop'}]},
                    {'id': 'fixture', 'model': MODEL['id'], 'choices': [], 'usage': {'prompt_tokens': 260000 if compact and len(requests) == 1 else 100, 'completion_tokens': 20,
                        'prompt_tokens_details': {'cached_tokens': 0}, 'completion_tokens_details': {'reasoning_tokens': 0}}},
                ]
                for chunk in chunks:
                    self.wfile.write(b'data: ' + json.dumps(chunk).encode() + b'\n\n')
                self.wfile.write(b'data: [DONE]\n\n')

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp); fixture = root / 'input'; fixture.mkdir()
                (fixture / 'design.circ').write_text('<project/>')
                output = root / 'episode'
                env = {'CIRCUIT_BENCH_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
                       'CIRCUIT_BENCH_API_KEY': 'synthetic-secret-only-in-proxy'}
                with patch.dict(os.environ, env), \
                     patch('circuit_bench.adapters.pi.episode.CLI', Path(os.environ['PI_TEST_CLI'])), \
                     patch('circuit_bench.adapters.pi.episode.identity', return_value={'name': 'stock-pi-test'}):
                    result = execute({'id': 'fixture', 'timeout_minutes': .1 if timeout else 1},
                        'Save the project.', fixture, output, root / 'project', MODEL)
                self.assertEqual(result['outcome'], 'timeout' if timeout else 'completed', (result, (output/'stderr.log').read_text()))
                self.assertEqual((output/'result/design.circ').read_text(), '<project>saved</project>\n')
                self.assertEqual(result['ledger']['toolCalls'], 2)
                self.assertEqual(result['ledger']['failedCalls'], 0 if timeout else 1)
                self.assertEqual(result['meter']['input'], len(requests) * 100 + (259900 if compact else 0))
                self.assertEqual(result['meter']['output'], len(requests) * 20)
                self.assertGreater(len(list((output/'trajectory').glob('*.jsonl'))), 0)
                for request in requests:
                    self.assertEqual(request['model'], MODEL['id'])
                    if request.get('tools'):
                        self.assertEqual({t['function']['name'] for t in request['tools']}, {'read', 'bash', 'edit', 'write'})
                        capacity = request.get('max_tokens', request.get('max_completion_tokens'))
                        if not compact or request is requests[0]:
                            self.assertEqual(capacity, MODEL['max_output'])
                        else:
                            # Native Pi may reduce the next response to available context headroom.
                            self.assertLessEqual(capacity, MODEL['max_output'])
                if compact:
                    self.assertGreater(result['ledger']['compactions'], 0, sorted({json.loads(line).get('type', '') for line in (output/'events.jsonl').read_text().split('\n') if line}))
                    entries = [entry for path in (output/'trajectory').glob('*.jsonl') for entry in records(path)]
                    self.assertTrue(any(e['type'] == 'compaction' for e in entries))
                    self.assertTrue(any(e.get('message', {}).get('role') == 'toolResult' for e in entries))
                for path in output.rglob('*'):
                    if path.is_file():
                        self.assertNotIn(env['CIRCUIT_BENCH_API_KEY'].encode(), path.read_bytes())
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
