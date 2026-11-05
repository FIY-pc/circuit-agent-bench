import http.server
import json
import threading
import unittest
import urllib.error
import urllib.request

from circuit_bench.meter import MeterProxy, ResponseMeter, usage_totals


class MeterTests(unittest.TestCase):
    def test_split_sse_usage_is_last_cumulative_record(self):
        meter = ResponseMeter()
        stream = b'data: {"model":"actual-id","usage":{"prompt_tokens":100,"completion_tokens":2}}\n\ndata: {"usage":{"prompt_tokens":100,"completion_tokens":8}}\n\ndata: [DONE]\n\n'
        for i in range(0, len(stream), 7):
            meter.feed(stream[i:i+7])
        meter.finish()
        self.assertEqual(meter.usage, {'prompt_tokens': 100, 'completion_tokens': 8})
        self.assertEqual(meter.model, 'actual-id')

    def test_pretty_json_responses_and_missing_usage(self):
        meter = ResponseMeter(sse=False)
        data = {'response': {'model': 'snapshot', 'usage': {'input_tokens': 9, 'output_tokens': 4}}}
        meter.feed(json.dumps(data, indent=2).encode())
        meter.finish()
        self.assertEqual(meter.usage['input_tokens'], 9)
        self.assertIsNone(usage_totals([{'usage': None}])['input'])
        self.assertIsNone(usage_totals([])['output'])

    def test_cache_and_reasoning_are_not_double_counted(self):
        result = usage_totals([{'usage': {'prompt_tokens': 100, 'completion_tokens': 20,
            'prompt_tokens_details': {'cached_tokens': 70}, 'completion_tokens_details': {'reasoning_tokens': 12}}}])
        self.assertEqual((result['input'], result['output'], result['cachedInput'], result['reasoningOutput']), (100, 20, 70, 12))
        result = usage_totals([{'usage': {'input_tokens': 10, 'cache_read_input_tokens': 20,
            'cache_creation_input_tokens': 30, 'output_tokens': 5}}])
        self.assertEqual(result['input'], 60)
        self.assertIsNone(result['reasoningOutput'])

    def test_partial_coverage_is_not_a_total(self):
        result = usage_totals([{'usage': {'prompt_tokens': 100, 'completion_tokens': 2}}, {'usage': None}])
        self.assertEqual(result['requestsWithUsage'], 1)
        self.assertIsNone(result['input'])

    def test_proxy_preserves_wire_payload_and_does_not_log_secret(self):
        received = []
        secret = 'test-secret-only-in-memory'
        response = b'data: {"usage":{"prompt_tokens":11,"completion_tokens":5}}\n\ndata: [DONE]\n\n'

        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                received.append((self.path, self.rfile.read(int(self.headers['Content-Length'])), self.headers['Authorization']))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(response)

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            body = b'{ "model": "exact/id", "stream": true, "tools": [], "messages": [] }'
            with MeterProxy(f'http://127.0.0.1:{server.server_port}/v1', secret) as proxy:
                request = urllib.request.Request(proxy.url + '/chat/completions', data=body,
                    headers={'Authorization': 'Bearer placeholder', 'Content-Type': 'application/json'})
                with urllib.request.urlopen(request) as r:
                    self.assertEqual(r.read(), response)
            self.assertEqual(received, [('/v1/chat/completions', body, 'Bearer ' + secret)])
            self.assertNotIn(secret, json.dumps(proxy.rows))
            self.assertEqual(usage_totals(proxy.rows)['input'], 11)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
