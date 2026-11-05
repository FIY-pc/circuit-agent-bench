"""Optional real product SDK transport against a local fixture, no paid model."""
import http.server
import json
import os
from pathlib import Path
import subprocess
import threading
import unittest

from circuit_bench.meter import MeterProxy, usage_totals

SCRIPT = r'''
import {pathToFileURL} from 'node:url';
const [root, url] = process.argv.slice(1);
const {modelFor, transport} = await import(pathToFileURL(root + '/packages/agent-runtime/src/models/services.mts'));
const {generationPayload} = await import(pathToFileURL(root + '/packages/agent-runtime/src/models/generation.mts'));
const config = {id:'bench', model:'example-model', baseUrl:url, api:'anthropic-messages',
  apiKey:'placeholder', effort:'none', contextWindow:200000, maxOutput:64000, vision:true};
const api = await transport(config.api);
const stream = api.streamSimple(modelFor(config),
  {messages:[{role:'user', content:'Reply OK.', timestamp:Date.now()}]},
  {apiKey:config.apiKey, onPayload:generationPayload(config), signal:AbortSignal.timeout(15000)});
const result = await stream.result();
if (result.stopReason === 'error') throw Error(result.errorMessage);
console.log(JSON.stringify({stopReason:result.stopReason, content:result.content}));
'''


@unittest.skipUnless(os.environ.get('VIBE_BENCH_NATIVE_TREE'), 'set VIBE_BENCH_NATIVE_TREE for product SDK test')
class ProductTransport(unittest.TestCase):
    def test_messages_sdk_route_query_and_capacity(self):
        received=[]
        class Server(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append((self.path,body.get('max_tokens')))
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                events=[
                    {'type':'message_start','message':{'id':'msg_fixture','type':'message','role':'assistant','model':'example-model','content':[],'stop_reason':None,'usage':{'input_tokens':12,'output_tokens':1}}},
                    {'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}},
                    {'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'OK'}},
                    {'type':'content_block_stop','index':0},
                    {'type':'message_delta','delta':{'stop_reason':'end_turn','stop_sequence':None},'usage':{'output_tokens':2}},
                    {'type':'message_stop'},
                ]
                for event in events:
                    self.wfile.write(('event: '+event['type']+'\ndata: '+json.dumps(event)+'\n\n').encode())
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Server)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with MeterProxy(f'http://127.0.0.1:{server.server_port}','fixture',api='anthropic-messages') as proxy:
                result=subprocess.run([os.environ.get('VIBE_LOGISIM_NODE','node'),'--input-type=module','-e',SCRIPT,
                    str(Path(os.environ['VIBE_BENCH_NATIVE_TREE']).resolve()),proxy.url],capture_output=True,text=True,timeout=25)
                self.assertEqual(result.returncode,0,result.stderr[-2000:])
                self.assertEqual(json.loads(result.stdout)['content'],[{'type':'text','text':'OK'}])
            self.assertEqual(received,[('/v1/messages?beta=true',64000)])
            self.assertEqual(usage_totals(proxy.rows)['output'],2)
            self.assertEqual(usage_totals(proxy.rows)['requestsWithCompleteUsage'],1)
        finally:
            server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
