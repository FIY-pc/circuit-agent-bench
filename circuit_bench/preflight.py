"""Exercise native loading, component metadata, rendering and simulation, no model."""
from pathlib import Path
import tempfile
from circuit_bench.host import Host,Tree

PROJECT='''<?xml version="1.0" encoding="UTF-8"?>
<project source="2.15.0.2.exe" version="1.0">
<lib name="0" desc="#Wiring"/><lib name="1" desc="#Gates"/><lib name="6" desc="#Base"/>
<main name="main"/><circuit name="main">
<comp lib="0" name="Pin" loc="(100,100)"><a name="label" val="A"/></comp>
<comp lib="0" name="Pin" loc="(300,100)"><a name="output" val="true"/><a name="facing" val="west"/><a name="label" val="Y"/></comp>
<wire from="(100,100)" to="(300,100)"/>
</circuit></project>'''

def check(tree):
    with tempfile.TemporaryDirectory() as temp,Host(tree) as host:
        f=Path(temp)/'preflight.circ';f.write_text(PROJECT)
        doc=host.call('open',path=str(f));args=dict(id=doc['id'],revision=doc['revision'],circuit='main')
        host.call('template',**args,library='0',name='Pin',attributes={})
        focus=host.call('observe',**args)['focus']
        if len(focus['components'])!=2:raise RuntimeError('Native loader dropped components')
        image=host.call('render',**args)
        if not image.get('image','').startswith('data:image/png;base64,'):raise RuntimeError('Native render failed')
        result=host.call('simulate',**args,vectors=[{'inputs':{'A':v},'expected':{'Y':v}} for v in (0,1)])
        if len(result.get('rows',[]))!=2 or any(r.get('oscillating') is not False or r.get('outputs',{}).get('Y',{}).get('value')!=i for i,r in enumerate(result['rows'])):
            raise RuntimeError('Native wire simulation failed')
    return True

def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--tree',default='/opt/product');a=p.parse_args()
    check(Tree.resolve(a.tree));print('Native preflight passed')

if __name__=='__main__':main()
