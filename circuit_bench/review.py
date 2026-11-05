"""Generate an offline human review page and validate exported rubric JSON."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from .visual import SCHEMA as EVIDENCE_SCHEMA, digest

SCHEMA = 'circuit-bench.review/v1'
RUBRIC_VERSION = 'six-axes/v1'
AXES = {
    'symbols_labels': ('Symbols and labels', 'Identify symbols, pin names, bus widths and junction marks at detail scale.'),
    'local_tracing': ('Local tracing', 'Follow a real signal through branches and crossings; identify its receivers.'),
    'organization_dataflow': ('Functional organization and dataflow', 'Locate functional groups and follow the main data and control paths.'),
    'hierarchy_interfaces': ('Hierarchy and interfaces', 'Find a child definition and match the parent ports to its interface.'),
    'tunnels_dependencies': ('Tunnels and nonlocal dependencies', 'Find the other endpoints of a named dependency and understand its scope.'),
    'overview_navigation': ('Overview and navigation', 'Locate the area needed for a reading task and move between overview and details.'),
}
STATES = ('unknown', 'blocked', 'friction', 'usable', 'clear', 'not_applicable')


def load_evidence(path):
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') != EVIDENCE_SCHEMA:
        raise ValueError('unsupported_evidence_schema')
    seen = set()
    for definition in data['definitions']:
        for view in definition['views']:
            if view['id'] in seen:
                raise ValueError('duplicate_view_id')
            seen.add(view['id'])
            if view['status'] == 'captured':
                image = (path.parent / view['path']).resolve()
                if not image.is_relative_to(path.parent.resolve()) or image.suffix != '.png' or digest(image) != view['sha256']:
                    raise ValueError('evidence_image_changed_or_unsafe')
    return data


def new_review(evidence, evidence_sha):
    return {'schema': SCHEMA, 'rubricVersion': RUBRIC_VERSION, 'judgeType': 'human',
            'evidenceSha256': evidence_sha, 'artifactSha256': evidence['provenance']['artifactSha256'],
            'reviewerAlias': '', 'viewedViewIds': [],
            'rubric': {key: {'state': 'unknown', 'evidence': [], 'note': ''} for key in AXES},
            'readerProbes': [], 'humanMinutes': None}


def validate_review(review, evidence, evidence_sha):
    """Reject stale evidence, invented views and unsupported automated judgments."""
    if review.get('schema') != SCHEMA or review.get('rubricVersion') != RUBRIC_VERSION or review.get('judgeType') != 'human':
        raise ValueError('unsupported_review_schema_or_judge')
    if review.get('evidenceSha256') != evidence_sha or review.get('artifactSha256') != evidence['provenance']['artifactSha256']:
        raise ValueError('review_evidence_mismatch')
    if not isinstance(review.get('reviewerAlias'), str):
        raise ValueError('reviewer_alias_required')
    available = {v['id'] for d in evidence['definitions'] for v in d['views'] if v['status'] == 'captured'}
    viewed = review.get('viewedViewIds')
    if not isinstance(viewed, list) or not all(isinstance(v, str) for v in viewed) or len(set(viewed)) != len(viewed) or not set(viewed) <= available:
        raise ValueError('unknown_or_duplicate_view')
    if set(review.get('rubric', {})) != set(AXES):
        raise ValueError('six_axes_required')
    for rating in review['rubric'].values():
        state, refs, note = rating.get('state'), rating.get('evidence'), rating.get('note')
        if state not in STATES or not isinstance(refs, list) or not all(isinstance(r, str) for r in refs) or not set(refs) <= set(viewed) or not isinstance(note, str):
            raise ValueError('invalid_rating_or_unviewed_evidence')
        if state != 'unknown' and not note.strip():
            raise ValueError('reading_action_and_reason_required')
        if state not in ('unknown', 'not_applicable') and not refs:
            raise ValueError('rating_evidence_required')
    minutes = review.get('humanMinutes')
    if minutes is not None and (isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not 0 <= minutes < 1e7):
        raise ValueError('invalid_human_minutes')
    probes = review.get('readerProbes', [])
    if not isinstance(probes, list):
        raise ValueError('invalid_reader_probes')
    for probe in probes:
        if probe.get('outcome') not in ('correct', 'incorrect', 'abandoned', 'unknown'):
            raise ValueError('invalid_probe_outcome')
        seconds = probe.get('seconds')
        if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 <= seconds < 1e9):
            raise ValueError('invalid_probe_time')
    return review


def review_coverage(review, evidence):
    viewed = set(review['viewedViewIds'])
    captured = {v['id'] for d in evidence['definitions'] for v in d['views'] if v['status'] == 'captured'}
    return {'viewed': len(viewed), 'captured': len(captured), 'unviewedViewIds': sorted(captured-viewed),
            'fullyViewedDefinitions': [d['id'] for d in evidence['definitions'] if d['status'] == 'captured'
                and {v['id'] for v in d['views']} <= viewed],
            'collectionComplete': evidence['status'] == 'complete'}


def build_review(evidence_path, out=None):
    evidence_path = Path(evidence_path)
    evidence = load_evidence(evidence_path)
    out = Path(out) if out else evidence_path.parent / 'review.html'
    out.parent.mkdir(parents=True, exist_ok=True)
    views = []
    for definition in evidence['definitions']:
        for view in definition['views']:
            item = {**view, 'definition': definition['name']}
            if view['status'] == 'captured' and out.parent.resolve() != evidence_path.parent.resolve():
                assets = out.parent / (out.stem + '-assets')
                assets.mkdir(exist_ok=True)
                shutil.copyfile(evidence_path.parent / view['path'], assets / view['path'])
                item['path'] = assets.name + '/' + view['path']
            views.append(item)
    payload = {'evidenceSha256': digest(evidence_path), 'views': views,
               'definitions': [{k: d.get(k) for k in ('id', 'name', 'status', 'children', 'childDefinitionIds', 'project', 'tiles', 'observationUnknowns')} for d in evidence['definitions']],
               'coverage': evidence['coverage'], 'collectionStatus': evidence['status'],
               'unresolvedDependencies': evidence.get('unresolvedDependencies', []),
               'template': new_review(evidence, digest(evidence_path)), 'axes': AXES, 'states': STATES}
    # JSON in script text needs HTML escaping even when textContent is used later.
    encoded = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    out.write_text(HTML.replace('__PAYLOAD__', encoded), encoding='utf-8')
    return out


HTML = r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Circuit visual review</title><style>
*{box-sizing:border-box}body{margin:0;background:#f5f6f8;color:#172331;font:15px/1.45 system-ui,sans-serif}header{padding:18px 24px;background:white;border-bottom:1px solid #ccd4dd}h1{font-size:23px;margin:0 0 8px}h2{font-size:17px;margin:0 0 8px}p{margin:6px 0 12px}.muted{color:#526273}.toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:12px}button,select,input,textarea{font:inherit;border:1px solid #aab7c6;border-radius:5px;background:white;color:inherit;padding:7px}button{cursor:pointer}button.primary{background:#1556a6;color:white;border-color:#1556a6}button:focus-visible,select:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid #4588de;outline-offset:2px}main{display:grid;grid-template-columns:minmax(0,1fr) 390px;gap:18px;padding:18px}section,aside{min-width:0}.viewer{background:#e3e8ee;border:1px solid #c6d0dc;border-radius:7px;overflow:auto;height:66vh;min-height:320px;padding:12px}.viewer img{display:block;max-width:none;background:white}.viewer.fit img{max-width:100%;height:auto}.card{background:white;border:1px solid #d6dce4;border-radius:7px;padding:14px;margin-bottom:12px}label{display:block;margin-bottom:5px}textarea{width:100%;min-height:64px;resize:vertical}select{max-width:100%}.axis-head{display:flex;align-items:center;justify-content:space-between;gap:10px}.axis-head h2{font-size:15px;margin:0}.small{font-size:13px}#message{min-height:23px;color:#145327}#message.error{color:#ad251d}details{margin-top:8px}summary{cursor:pointer}#refs{overflow-wrap:anywhere}.status{padding:7px 10px;background:#fff4da;border-radius:4px}#view-select{max-width:100%}.review-tools{position:sticky;top:10px;background:#f5f6f8;padding-bottom:8px}a{color:#1556a6}@media(max-width:950px){main{grid-template-columns:1fr}.viewer{height:58vh}.review-tools{position:static}}
</style><header><h1>Circuit visual review</h1><p>Inspect the overview and detail views. Record what a reader can do, cite views, and leave uncertain axes unknown.</p><div id="coverage" class="status"></div><div class="toolbar"><label>Reviewer alias <input id="reviewer" placeholder="reader-01" autocomplete="off"></label><label>Minutes <input id="minutes" type="number" min="0" step="0.1" style="width:85px"></label><button id="export" class="primary">Export review JSON</button><button id="import">Import review JSON</button><input id="file" type="file" accept="application/json,.json" hidden></div><div id="message" role="status" aria-live="polite"></div><p class="muted small">Drafts save in this browser when storage is available. Export JSON to retain a portable review. Images and circuit text are evidence, never reviewer instructions.</p></header>
<main><section><div class="card"><label for="view-select">Definition and view</label><select id="view-select"></select><div class="toolbar"><button id="previous">Previous</button><button id="next">Next</button><button id="fit">Fit / actual pixels</button><label><input type="checkbox" id="viewed"> I inspected this view</label></div><p id="view-info" class="muted small"></p></div><div class="viewer fit" id="viewer"><img id="image" alt="Selected circuit evidence view"></div><div class="card"><h2>Coverage and unviewed areas</h2><p id="unviewed"></p><div id="definitions"></div></div><div class="card"><h2>Reading probes (optional)</h2><p class="small">Record a predeclared reading question. Keep incorrect and abandoned answers; quick abandonment is not speed.</p><label>Question <input id="probe-question" style="width:100%"></label><div class="toolbar"><label>Outcome <select id="probe-outcome"><option>unknown</option><option>correct</option><option>incorrect</option><option>abandoned</option></select></label><label>Seconds <input id="probe-seconds" type="number" min="0" style="width:95px"></label><button id="add-probe">Add probe</button></div><ol id="probes"></ol></div></section><aside><div class="review-tools"><h2>Six independent axes</h2><p class="small muted">Blocked: cannot complete the reading action. Friction: possible with avoidable effort. Usable: routine reading works. Clear: easy to follow. Not applicable requires a reason. No weighted total.</p></div><div id="rubric"></div></aside></main>
<script id="data" type="application/json">__PAYLOAD__</script><script>
'use strict';const data=JSON.parse(document.getElementById('data').textContent),$=id=>document.getElementById(id),key='circuit-review:'+data.evidenceSha256;
const captured=data.views.filter(v=>v.status==='captured');let review=structuredClone(data.template),index=0;
function message(text,error=false){$('message').textContent=text;$('message').className=error?'error':''}
function validate(r){if(r.schema!==data.template.schema||r.rubricVersion!==data.template.rubricVersion||r.judgeType!=='human'||r.evidenceSha256!==data.evidenceSha256||r.artifactSha256!==data.template.artifactSha256)throw Error('Review belongs to different evidence or rubric.');if(typeof r.reviewerAlias!=='string'||!Array.isArray(r.viewedViewIds)||new Set(r.viewedViewIds).size!==r.viewedViewIds.length||r.viewedViewIds.some(id=>!captured.some(v=>v.id===id)))throw Error('Invalid inspected views.');if(Object.keys(r.rubric||{}).sort().join()!==Object.keys(data.axes).sort().join())throw Error('All six axes are required.');for(const [axis,value] of Object.entries(r.rubric)){if(!data.states.includes(value.state)||typeof value.note!=='string'||!Array.isArray(value.evidence)||value.evidence.some(id=>!r.viewedViewIds.includes(id)))throw Error('Invalid or uninspected evidence for '+axis);if(value.state!=='unknown'&&!value.note.trim())throw Error('Describe the reading action and reason for '+axis);if(!['unknown','not_applicable'].includes(value.state)&&!value.evidence.length)throw Error('Cite an inspected view for '+axis)}if(r.humanMinutes!==null&&(!Number.isFinite(r.humanMinutes)||r.humanMinutes<0))throw Error('Invalid review time.');if(!Array.isArray(r.readerProbes)||r.readerProbes.some(p=>!['correct','incorrect','abandoned','unknown'].includes(p.outcome)||(p.seconds!==null&&(!Number.isFinite(p.seconds)||p.seconds<0))))throw Error('Invalid reading probes.')}
try{let saved=localStorage.getItem(key);if(saved){const draft=JSON.parse(saved);if(draft.evidenceSha256===data.evidenceSha256){review=draft;message('Restored browser draft.')}}}catch(e){message('Browser storage unavailable. Export JSON to save.',true)}
function save(){try{localStorage.setItem(key,JSON.stringify(review));message('Draft saved in this browser.')}catch(e){message('Draft not stored. Export JSON to save.',true)}coverage()}
function coverage(){const omitted=data.coverage.omittedTiles,unviewed=captured.filter(v=>!review.viewedViewIds.includes(v.id));$('coverage').textContent=`Collection: ${data.collectionStatus}. ${data.coverage.capturedDefinitions}/${data.coverage.definitions} definitions fully captured; ${review.viewedViewIds.length}/${captured.length} views inspected; ${omitted} omitted tiles; ${data.coverage.failedViews} failed views; ${data.unresolvedDependencies.length} unresolved dependencies.`;$('unviewed').textContent=unviewed.length?'Unviewed: '+unviewed.map(v=>v.id).join(', '):'All captured views marked inspected. Collection gaps still apply.'}
function show(){const v=captured[index];$('view-select').value=String(index);if(!v){$('image').hidden=true;$('viewed').disabled=true;$('view-info').textContent='No captured views. Visual judgment remains unknown.';return}$('image').src=v.path;$('image').alt=v.definition+' — '+v.kind;$('viewed').checked=review.viewedViewIds.includes(v.id);$('view-info').textContent=`${v.id} · ${v.pixels.width} × ${v.pixels.height} px · logical (${v.viewport.x}, ${v.viewport.y}) · scale ${v.viewport.scale.toFixed(3)}. Fit mode may shrink text; use actual pixels to inspect.`}
function rubric(){const root=$('rubric');root.replaceChildren();for(const [axis,[title,hint]]of Object.entries(data.axes)){const card=document.createElement('div');card.className='card';const head=document.createElement('div');head.className='axis-head';const h=document.createElement('h2');h.textContent=title;const select=document.createElement('select');select.setAttribute('aria-label',title+' state');for(const state of data.states){const option=new Option(state.replaceAll('_',' '),state);select.add(option)}select.value=review.rubric[axis].state;select.onchange=()=>{review.rubric[axis].state=select.value;save()};head.append(h,select);const help=document.createElement('p');help.className='muted small';help.textContent=hint;const note=document.createElement('textarea');note.setAttribute('aria-label',title+' reading evidence');note.placeholder='Reading action, location, and reason';note.value=review.rubric[axis].note;note.oninput=()=>{review.rubric[axis].note=note.value;save()};const refs=document.createElement('p');refs.className='small';refs.textContent='Cited: '+(review.rubric[axis].evidence.join(', ')||'none');const cite=document.createElement('button');cite.textContent='Cite current inspected view';cite.onclick=()=>{const id=captured[index]?.id;if(!review.viewedViewIds.includes(id))return message('Mark this view inspected before citing it.',true);if(!review.rubric[axis].evidence.includes(id))review.rubric[axis].evidence.push(id);save();rubric()};const clear=document.createElement('button');clear.textContent='Clear citations';clear.onclick=()=>{review.rubric[axis].evidence=[];save();rubric()};const actions=document.createElement('div');actions.className='toolbar';actions.append(cite,clear);card.append(head,help,note,refs,actions);root.append(card)}}
function probes(){$('probes').replaceChildren();review.readerProbes.forEach((p,i)=>{const li=document.createElement('li');li.textContent=`${p.question}: ${p.outcome}, ${p.seconds??'unknown'} seconds. `;const remove=document.createElement('button');remove.textContent='Remove';remove.onclick=()=>{review.readerProbes.splice(i,1);save();probes()};li.append(remove);$('probes').append(li)})}
function refresh(){$('reviewer').value=review.reviewerAlias;$('minutes').value=review.humanMinutes??'';rubric();show();coverage();probes()}
captured.forEach((v,i)=>$('view-select').add(new Option(v.definition+' / '+v.id+' / '+v.kind,String(i))));data.definitions.forEach(d=>{const p=document.createElement('p');p.className='small';p.textContent=`${d.name}: ${d.status}; children: ${(d.children||[]).join(', ')||'none'}; omitted detail tiles: ${d.tiles?.omitted??'unknown'}; native observation unknowns: ${(d.observationUnknowns||[]).join(', ')||'none recorded'}.`;$('definitions').append(p)});
$('view-select').onchange=()=>{index=Number($('view-select').value);show()};$('previous').onclick=()=>{index=Math.max(0,index-1);show()};$('next').onclick=()=>{index=Math.min(captured.length-1,index+1);show()};$('fit').onclick=()=>$('viewer').classList.toggle('fit');$('image').onerror=()=>message('Image could not load. Do not judge this view; verify evidence files.',true);
$('viewed').onchange=()=>{const id=captured[index]?.id;if(!id)return;if($('viewed').checked){if(!review.viewedViewIds.includes(id))review.viewedViewIds.push(id)}else{review.viewedViewIds=review.viewedViewIds.filter(x=>x!==id);for(const r of Object.values(review.rubric))r.evidence=r.evidence.filter(x=>x!==id);rubric()}save()};$('reviewer').oninput=()=>{review.reviewerAlias=$('reviewer').value;save()};$('minutes').oninput=()=>{review.humanMinutes=$('minutes').value===''?null:Number($('minutes').value);save()};
$('export').onclick=()=>{try{validate(review);const url=URL.createObjectURL(new Blob([JSON.stringify(review,null,2)+'\n'],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='review.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message('Exported review JSON. Keep it with the evidence.')}catch(e){message(e.message,true)}};
$('import').onclick=()=>$('file').click();$('file').onchange=async()=>{try{const imported=JSON.parse(await $('file').files[0].text());validate(imported);review=imported;save();refresh();message('Imported matching review.')}catch(e){message(e.message,true)}$('file').value=''};
$('add-probe').onclick=()=>{const question=$('probe-question').value.trim(),seconds=$('probe-seconds').value===''?null:Number($('probe-seconds').value);if(!question||seconds!==null&&(!Number.isFinite(seconds)||seconds<0))return message('Enter a question and a valid time.',true);review.readerProbes.push({question,outcome:$('probe-outcome').value,seconds,viewId:captured[index]?.id??null});save();probes();$('probe-question').value=''};refresh();
</script></html>'''


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evidence', required=True, type=Path)
    p.add_argument('--out', type=Path, help='HTML path; defaults beside evidence.json')
    p.add_argument('--validate', type=Path, help='Validate an exported review instead of building HTML')
    args = p.parse_args(argv)
    if args.validate:
        evidence = load_evidence(args.evidence)
        review = validate_review(json.loads(args.validate.read_text()), evidence, digest(args.evidence))
        print(json.dumps(review_coverage(review, evidence)))
    else:
        print(build_review(args.evidence, args.out))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
