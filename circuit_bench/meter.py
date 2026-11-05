"""Transparent inference forwarding; real credentials stay in this process.

Request/response bodies are forwarded unchanged. No retry, model remapping,
token cap, stream-options injection, or alternate agent loop. Usage comes from
provider response fields; an absent usage record is unknown, never zero.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ResponseMeter:
    def __init__(self, sse=True):
        self.usage = None
        self.model = None
        self.buffer = b''
        self.sse = sse
        self.terminal = False

    def object(self, obj):
        if not isinstance(obj, dict):
            return
        if obj.get('type') in ('message_stop', 'response.completed'):
            self.terminal = True
        if any(choice.get('finish_reason') is not None for choice in obj.get('choices', [])):
            self.terminal = True
        nested = obj.get('response') or obj.get('message') or obj
        if isinstance(nested, dict):
            self.model = nested.get('model') or self.model
            if isinstance(nested.get('usage'), dict):
                self.usage = {**(self.usage or {}), **nested['usage']}
        if isinstance(obj.get('usage'), dict):
            self.usage = {**(self.usage or {}), **obj['usage']}

    def feed(self, chunk):
        self.buffer += chunk
        if not self.sse:
            return
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            if line.startswith(b'data:'):
                if line[5:].strip() == b'[DONE]':
                    self.terminal = True
                    continue
                try:
                    self.object(json.loads(line[5:]))
                except (ValueError, UnicodeDecodeError):
                    pass

    def finish(self):
        if self.sse and self.buffer.removeprefix(b'data:').strip() == b'[DONE]':
            self.terminal = True
            return
        try:
            self.object(json.loads(self.buffer.removeprefix(b'data:') if self.sse else self.buffer))
            if not self.sse:
                self.terminal = True
        except (ValueError, UnicodeDecodeError):
            pass


def usage_totals(rows):
    """Input includes cached input. Output includes reasoning when provider does.

The raw usage records remain authoritative; cache/reasoning are subsets, not
additional tokens to add to the total. Partial coverage has no total estimate.
"""
    totals = dict(input=0, output=0, cachedInput=0, reasoningOutput=0)
    unknown = set()
    observed = complete = 0
    observed_fields = set()
    for row in rows:
        u = row.get('usage')
        if not u:
            unknown.update(totals)
            continue
        observed += 1
        if row.get('usageComplete', True) and not row.get('transportError'):
            complete += 1
        else:
            unknown.update(totals)
        if 'prompt_tokens' in u:
            values = {'input': u.get('prompt_tokens'), 'output': u.get('completion_tokens'),
                      'cachedInput': (u.get('prompt_tokens_details') or {}).get('cached_tokens'),
                      'reasoningOutput': (u.get('completion_tokens_details') or {}).get('reasoning_tokens')}
        elif 'cache_read_input_tokens' in u or 'cache_creation_input_tokens' in u:
            values = {'input': sum(u.get(k, 0) for k in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')),
                      'output': u.get('output_tokens'), 'cachedInput': u.get('cache_read_input_tokens'),
                      'reasoningOutput': None}
        else:
            values = {'input': u.get('input_tokens'), 'output': u.get('output_tokens'),
                      'cachedInput': (u.get('input_tokens_details') or {}).get('cached_tokens'),
                      'reasoningOutput': (u.get('output_tokens_details') or {}).get('reasoning_tokens')}
        for key, value in values.items():
            if value is None:
                unknown.add(key)
            else:
                totals[key] += value
                observed_fields.add(key)
    return {'requests': len(rows), 'requestsWithUsage': observed,
            'requestsWithCompleteUsage': complete, 'requestsWithPartialUsage': observed - complete,
            'observedTokens': {k: v if k in observed_fields else None for k, v in totals.items()},
            'requestsWithoutUsage': len(rows) - observed,
            **{k: None if k in unknown or not rows else v for k, v in totals.items()}}


class MeterProxy:
    def __init__(self, base, key, journal=None, api='openai-completions'):
        self.journal = Path(journal) if journal else None
        self.rows = []
        self._lock = threading.Lock()
        self._sequence = 0
        outer = self
        opener = urllib.request.build_opener(NoRedirect)

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                route = urlsplit(self.path).path
                if route not in ('/v1/chat/completions', '/v1/responses', '/v1/messages'):
                    self.send_error(404)
                    return
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                try:
                    obj = json.loads(body)
                except ValueError:
                    self.send_error(400)
                    return
                headers = {k: v for k, v in self.headers.items()
                           if k.lower() not in ('authorization', 'x-api-key', 'host', 'connection',
                                                'content-length', 'accept-encoding')}
                headers['Authorization'] = 'Bearer ' + key
                if route == '/v1/messages':
                    headers['x-api-key'] = key
                # Avoid changing compressed data while observing SSE usage.
                headers['Accept-Encoding'] = 'identity'
                # Messages SDK appends /v1/messages to its base; OpenAI SDKs
                # append their resource to a base that normally ends in /v1.
                suffix = self.path.removeprefix('/v1')
                if route == '/v1/messages' and not base.rstrip('/').endswith('/v1'):
                    suffix = self.path
                request = urllib.request.Request(base.rstrip('/') + suffix, data=body, headers=headers)
                with outer._lock:
                    outer._sequence += 1
                    request_id = outer._sequence
                row = {'request': request_id, 'modelRequested': obj.get('model'),
                       'bodySha256': hashlib.sha256(body).hexdigest(), 'requestBytes': len(body),
                       'path': route, 'tools': [t.get('name') or (t.get('function') or {}).get('name') for t in obj.get('tools', [])],
                       'usage': None, 'usageComplete': False, 'status': None}
                with outer._lock:
                    outer.rows.append(row)
                    outer._record('started', row)
                started = time.monotonic()
                meter = ResponseMeter()
                try:
                    try:
                        upstream = opener.open(request, timeout=600)
                    except urllib.error.HTTPError as e:
                        upstream = e
                    with upstream:
                        meter.sse = 'text/event-stream' in upstream.headers.get('Content-Type', '')
                        row['status'] = upstream.status
                        self.send_response(upstream.status)
                        for k, v in upstream.headers.items():
                            if k.lower() not in ('transfer-encoding', 'connection', 'content-length'):
                                self.send_header(k, v)
                        self.end_headers()
                        while chunk := upstream.read1(65536):
                            meter.feed(chunk)
                            if meter.usage != row['usage']:
                                with outer._lock:
                                    row.update(usage=meter.usage, modelReported=meter.model)
                                    outer._record('usage', row)
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        meter.finish()
                        row['usageComplete'] = bool(meter.usage and meter.terminal and upstream.status < 400)
                        if meter.sse and upstream.status < 400 and not meter.terminal:
                            row['transportError'] = 'incomplete_stream'
                except (BrokenPipeError, ConnectionResetError):
                    row['transportError'] = 'client_disconnected'
                except Exception as e:
                    row['transportError'] = type(e).__name__
                    if row['status'] is None:
                        self.send_error(502, 'Model connection failed')
                finally:
                    row.update(usage=meter.usage, modelReported=meter.model,
                               elapsedMs=round((time.monotonic() - started) * 1000))
                    with outer._lock:
                        outer._record('finished', row)

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.url = f'http://127.0.0.1:{self.server.server_port}' + ('' if api == 'anthropic-messages' else '/v1')

    def _record(self, event, row):
        if self.journal:
            with self.journal.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'event': event, **row}, ensure_ascii=False) + '\n')
                f.flush()
                os.fsync(f.fileno())

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
