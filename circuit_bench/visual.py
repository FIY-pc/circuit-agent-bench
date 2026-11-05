"""Collect native visual evidence without judging appearance or changing the answer."""
from __future__ import annotations

import argparse
import base64
from contextlib import redirect_stdout
import hashlib
import json
import math
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from .host import Host, Tree

SCHEMA = 'circuit-bench.visual/v1'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def package_digest(root):
    h = hashlib.sha256()
    for path in sorted(Path(root).rglob('*')):
        if path.is_symlink():
            raise ValueError('package_contains_symlink')
        if path.is_file():
            h.update(path.relative_to(root).as_posix().encode() + b'\0')
            h.update(bytes.fromhex(digest(path)))
    return h.hexdigest()


def _project(path, package):
    root = ET.parse(path).getroot()
    if root.tag != 'project':
        raise ValueError('not_a_project')
    definitions = root.findall('circuit')
    names = [c.get('name') for c in definitions]
    if not all(names) or len(set(names)) != len(names):
        raise ValueError('missing_or_duplicate_definition')
    # The renderer may follow library paths; keep them inside the frozen package.
    for lib in root.findall('lib'):
        desc = lib.get('desc', '')
        if desc.startswith(('file#', 'jar#')):
            relative = desc.split('#')[1]
            candidate = (path.parent / relative).resolve()
            if Path(relative).is_absolute() or not candidate.is_relative_to(package.resolve()):
                raise ValueError('external_library_outside_package')
    graph = {c.get('name'): sorted({x.get('name') for x in c.findall('comp')
             if not x.get('lib') and x.get('name') in names}) for c in definitions}
    main = root.find('main')
    return graph, main.get('name') if main is not None else (names[0] if names else None)


def reachable(graph, entry):
    pending, seen = [entry], []
    while pending:
        name = pending.pop(0)
        if name not in seen:
            seen.append(name)
            pending.extend(x for x in graph.get(name, []) if x not in seen)
    return seen



def definition_plan(artifact, entry):
    """Follow local and packaged .circ library definitions, once per file/name."""
    package = artifact.parent
    cache = {}
    def project(path):
        if path not in cache:
            graph, main = _project(path, package)
            root = ET.parse(path).getroot()
            libraries = {lib.get('name'): lib.get('desc', '') for lib in root.findall('lib')}
            cache[path] = (graph, main, root, libraries)
        return cache[path]
    graph, main, _, _ = project(artifact)
    entry = entry or main
    if entry not in graph:
        raise ValueError('entry_missing')
    pending, nodes, edges = [(artifact, entry)], [], {}
    while pending:
        node = pending.pop(0)
        if node in nodes:
            continue
        nodes.append(node)
        path, name = node
        local, _, root, libraries = project(path)
        if name not in local:
            raise ValueError('library_definition_missing')
        circuit = next(c for c in root.findall('circuit') if c.get('name') == name)
        children = []
        for comp in circuit.findall('comp'):
            target = None
            if not comp.get('lib') and comp.get('name') in local:
                target = (path, comp.get('name'))
            else:
                desc = libraries.get(comp.get('lib'), '')
                if desc.startswith('file#'):
                    target_path = (path.parent / desc.split('#')[1]).resolve()
                    target_graph, _, _, _ = project(target_path)
                    if comp.get('name') in target_graph:
                        target = (target_path, comp.get('name'))
            if target and target not in children:
                children.append(target)
        edges[node] = children
        pending.extend(child for child in children if child not in nodes)
    ids = {node: f'd{i:03}' for i, node in enumerate(nodes)}
    definitions = [{'id': ids[node], 'name': node[1],
                    'project': 'project-' + hashlib.sha256(node[0].relative_to(package).as_posix().encode()).hexdigest()[:12],
                    'children': [child[1] for child in edges[node]],
                    'childDefinitionIds': [ids[child] for child in edges[node]],
                    'status': 'unviewed', 'views': []} for node in nodes]
    unreachable = sorted(set(graph) - {name for path, name in nodes if path == artifact})
    return definitions, nodes, entry, unreachable


def drawing_bounds(focus, margin=24):
    """Union runtime drawing bounds, including label extents and wire bounds."""
    boxes = [focus.get('bounds')]
    boxes += [c.get('visualBounds') or c.get('bounds') for c in focus.get('components', [])]
    boxes = [b for b in boxes if isinstance(b, dict) and all(
        isinstance(b.get(k), (int, float)) and math.isfinite(b[k]) for k in ('x', 'y', 'width', 'height'))]
    if not boxes:
        raise ValueError('missing_drawing_bounds')
    x = math.floor(min(b['x'] for b in boxes)) - margin
    y = math.floor(min(b['y'] for b in boxes)) - margin
    right = math.ceil(max(b['x'] + b['width'] for b in boxes)) + margin
    bottom = math.ceil(max(b['y'] + b['height'] for b in boxes)) + margin
    return dict(x=x, y=y, width=max(1, right-x), height=max(1, bottom-y))


def tile_plan(bounds, width=1200, height=900, overlap=80, limit=256):
    if width <= overlap or height <= overlap or overlap < 0 or limit < 0:
        raise ValueError('invalid_tile_budget')
    nx = max(1, math.ceil((bounds['width'] - overlap) / (width-overlap)))
    ny = max(1, math.ceil((bounds['height'] - overlap) / (height-overlap)))
    tiles = []
    for index in range(min(nx * ny, limit)):
        col, row = index % nx, index // nx
        dx, dy = col*(width-overlap), row*(height-overlap)
        tiles.append(dict(x=bounds['x']+dx, y=bounds['y']+dy,
                          width=min(width, bounds['width']-dx), height=min(height, bounds['height']-dy)))
    return tiles, {'columns': nx, 'rows': ny, 'planned': nx*ny, 'selected': len(tiles),
                   'omitted': nx*ny-len(tiles), 'firstOmittedIndex': len(tiles) if nx*ny > len(tiles) else None,
                   'tileWidth': width, 'tileHeight': height, 'overlap': overlap}


def _font_identity():
    try:
        proc = subprocess.run(['fc-list', '--format', '%{family}|%{style}\n'], capture_output=True, timeout=10)
        if proc.returncode == 0:
            return hashlib.sha256(b'\n'.join(sorted(proc.stdout.splitlines()))).hexdigest()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _render(host, args, viewport, out, view_id, kind):
    view = {'id': view_id, 'kind': kind, 'viewport': viewport, 'status': 'error'}
    try:
        result = host.call('render', **args, **viewport)
        encoded = result['image']
        if encoded.startswith('data:'):
            encoded = encoded.split(',', 1)[1]
        png = base64.b64decode(encoded, validate=True)
        if png[:8] != b'\x89PNG\r\n\x1a\n':
            raise ValueError('not_png')
        width, height = struct.unpack('>II', png[16:24])
        expected = (math.ceil(viewport['width']*viewport['scale']), math.ceil(viewport['height']*viewport['scale']))
        if (width, height) != expected:
            raise ValueError('render_dimensions_mismatch')
        path = out / f'{view_id}.png'
        path.write_bytes(png)
        view.update(status='captured', path=path.name, sha256=digest(path), pixels={'width': width, 'height': height})
    except Exception as error:
        # Exception text can include absolute source paths. Keep a typed failure only.
        view['errorType'] = type(error).__name__
    return view


def collect(artifact, tree, out, *, circuit=None, tile_width=1200, tile_height=900,
            scale=1.0, overlap=80, max_tiles=256):
    """One fixed evaluator Host, sequential definitions/tiles, no model calls.

    `tree` is an independent evaluator product path or Tree, never inferred from
    the generating run. Output directory must be new; evidence is immutable.
    """
    if not math.isfinite(scale) or scale <= 0 or tile_width*scale > 4096 or tile_height*scale > 4096 or tile_width*tile_height*scale*scale > 8_000_000:
        raise ValueError('invalid_render_scale_or_pixel_budget')
    tile_plan(dict(x=0, y=0, width=1, height=1), tile_width, tile_height, overlap, max_tiles)
    artifact, out = Path(artifact).absolute(), Path(out).absolute()
    if out.resolve().is_relative_to(artifact.parent.resolve()):
        raise ValueError('evidence_must_be_outside_artifact_package')
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    evidence = {'schema': SCHEMA, 'status': 'error', 'entry': circuit, 'definitions': [],
                'unreachableDefinitions': [], 'unresolvedDependencies': [],
                'provenance': {'collectorSha256': digest(__file__), 'artifactSha256': None,
                               'sourceUnchanged': None, 'packageUnchanged': None},
                'protocol': {'scale': scale, 'tileWidth': tile_width, 'tileHeight': tile_height,
                             'overlap': overlap, 'maxTilesPerDefinition': max_tiles,
                             'simulation': 'fresh-open; state coloring disabled', 'selection': 'none',
                             'readability': 'human judgment required; fixed scale is not a legibility guarantee'},
                'errors': []}
    before_package = before = None
    try:
        if artifact.is_symlink():
            raise ValueError('artifact_symlink')
        before = digest(artifact)
        before_package = package_digest(artifact.parent)
        evidence['provenance'].update(artifactSha256=before, packageSha256=before_package)
        # Validate all package projects, including transitive library files.
        for path in artifact.parent.rglob('*.circ'):
            _project(path, artifact.parent)
        definitions, nodes, entry, unreachable = definition_plan(artifact, circuit)
        evidence['entry'] = entry
        evidence['unreachableDefinitions'] = unreachable
        evidence['definitions'] = definitions
        selected_tree = tree if isinstance(tree, Tree) else Tree.resolve(tree=tree)
        # Resolve and fingerprint the actual executable; a source commit alone is insufficient.
        binary = selected_tree.host_binary()
        lock = selected_tree.root / 'config/runtime-lock.json'
        evidence['provenance']['renderer'] = {'commit': selected_tree.commit, 'dirty': selected_tree.dirty,
            'hostSha256': digest(binary), 'runtimeLockSha256': digest(lock) if lock.is_file() else None,
            'platform': platform.system(), 'fontInventorySha256': _font_identity(),
            'fontResolution': 'environment fingerprint only; effective label heights unmeasured'}
        with tempfile.TemporaryDirectory(prefix='circuit-visual-') as temp:
            frozen = Path(temp) / 'project'
            shutil.copytree(artifact.parent, frozen)
            if package_digest(frozen) != before_package:
                raise ValueError('snapshot_changed')
            # Host shutdown also reaps its worker. Never open a second Host here.
            with Host(selected_tree) as host:
                doc, current_file = None, None
                try:
                    for definition, (source_file, _) in zip(evidence['definitions'], nodes):
                        try:
                            if source_file != current_file:
                                if doc:
                                    host.call('close', id=doc['id'], revision=doc['revision'], discard=True)
                                    doc = None
                                doc = host.call('open', path=str(frozen / source_file.relative_to(artifact.parent)))
                                current_file = source_file
                            args = {'id': doc['id'], 'revision': doc['revision'], 'circuit': definition['name']}
                            observation = host.call('observe', **args)
                            focus = observation['focus']
                            runtime = observation.get('runtime', {})
                            definition['runtime'] = {k: runtime.get(k) for k in ('jarSha256', 'reportedVersion', 'javaVersion')}
                            definition['observationUnknowns'] = [u.get('code', 'unknown') for u in observation.get('unknowns', [])]
                            external = sorted({c['subcircuit'] for c in focus.get('components', [])
                                               if c.get('subcircuit') and c['subcircuit'] not in definition['children']})
                            evidence['unresolvedDependencies'].extend({'parent': definition['name'], 'name': n,
                                'reason': 'external_definition_not_addressable_by_local_name'} for n in external)
                            bounds = drawing_bounds(focus)
                            definition['bounds'] = bounds
                            tiles, coverage = tile_plan(bounds, tile_width, tile_height, overlap, max_tiles)
                            definition['tiles'] = coverage
                            overview_scale = min(1.0, 1400/bounds['width'], 900/bounds['height'])
                            overview = _render(host, args, {**bounds, 'scale': overview_scale}, out,
                                               definition['id']+'-overview', 'overview')
                            definition['views'].append(overview)
                            for i, tile in enumerate(tiles):
                                definition['views'].append(_render(host, args, {**tile, 'scale': scale}, out,
                                                            definition['id']+f'-tile-{i:04}', 'detail'))
                            complete = not coverage['omitted'] and all(v['status'] == 'captured' for v in definition['views'])
                            definition['status'] = 'captured' if complete else 'partial'
                        except Exception as error:
                            definition.update(status='error', errorType=type(error).__name__)
                finally:
                    if doc:
                        host.call('close', id=doc['id'], revision=doc['revision'], discard=True)
        evidence['status'] = 'complete' if all(d['status'] == 'captured' for d in evidence['definitions']) and not evidence['unresolvedDependencies'] else 'partial'
    except Exception as error:
        evidence['errors'].append({'type': type(error).__name__})
    finally:
        if before is not None:
            evidence['provenance']['sourceUnchanged'] = artifact.is_file() and digest(artifact) == before
        if before_package is not None:
            try:
                evidence['provenance']['packageUnchanged'] = package_digest(artifact.parent) == before_package
            except (OSError, ValueError):
                evidence['provenance']['packageUnchanged'] = False
        if evidence['provenance']['sourceUnchanged'] is False or evidence['provenance']['packageUnchanged'] is False:
            evidence['status'] = 'error'
            evidence['errors'].append({'type': 'SourceChanged'})
        evidence['elapsedMs'] = round((time.monotonic()-started)*1000)
        evidence['coverage'] = {'definitions': len(evidence['definitions']),
            'capturedDefinitions': sum(d['status'] == 'captured' for d in evidence['definitions']),
            'capturedViews': sum(v['status'] == 'captured' for d in evidence['definitions'] for v in d['views']),
            'failedViews': sum(v['status'] != 'captured' for d in evidence['definitions'] for v in d['views']),
            'omittedTiles': sum(d.get('tiles', {}).get('omitted', 0) for d in evidence['definitions'])}
        write_json(out / 'evidence.json', evidence)
    return evidence


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--artifact', required=True, type=Path)
    p.add_argument('--tree', required=True, type=Path, help='Fixed evaluator product, independent of generating harness')
    p.add_argument('--out', required=True, type=Path, help='New evidence directory')
    p.add_argument('--circuit')
    p.add_argument('--scale', type=float, default=1)
    p.add_argument('--tile-width', type=int, default=1200)
    p.add_argument('--tile-height', type=int, default=900)
    p.add_argument('--overlap', type=int, default=80)
    p.add_argument('--max-tiles', type=int, default=256)
    args = p.parse_args(argv)
    with redirect_stdout(sys.stderr):
        result = collect(**vars(args))
    print(json.dumps({'status': result['status'], 'coverage': result['coverage']}))
    return 1 if result['status'] == 'error' else 0


if __name__ == '__main__':
    raise SystemExit(main())
