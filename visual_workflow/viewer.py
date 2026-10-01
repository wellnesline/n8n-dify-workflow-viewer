"""Bounded public graph projection. Never pass arbitrary workflow values through."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
from datetime import datetime, timezone

SLUG = re.compile(r'[a-z][a-z0-9-]{0,63}\Z')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def label(value):
    if not isinstance(value, str) or not value or len(value) > 240:
        raise ValueError('Public labels must contain 1..240 characters')
    return value


def names(values):
    if values is None:
        return []
    if isinstance(values, dict):
        return [label(k) for k in values]
    return [label(v['variable']) for v in values if isinstance(v, dict) and isinstance(v.get('variable'), str)]


def normalize(n8n, dify, project, slug, title):
    try:
        return _normalize(n8n, dify, project, slug, title)
    except (KeyError, TypeError, AttributeError, IndexError) as error:
        raise ValueError('Malformed workflow graph') from None


def _normalize(n8n, dify, project, slug, title):
    if not SLUG.fullmatch(slug) or project not in ('project-a', 'project-b'):
        raise ValueError('Invalid public identity')
    graphs = []
    for engine, raw in (('n8n', n8n), ('dify', dify)):
        source = raw if engine == 'n8n' else raw['workflow']['graph']
        nodes = source['nodes']
        if not 1 <= len(nodes) <= 300:
            raise ValueError('Graph node limit exceeded')
        keys = [x['name'] if engine == 'n8n' else x['id'] for x in nodes]
        if len(set(keys)) != len(keys):
            raise ValueError('Duplicate node identity')
        ids = {key: 'n' + str(i) for i, key in enumerate(keys)}
        public = []
        for key, node in zip(keys, nodes):
            data = node if engine == 'n8n' else node['data']
            pos = node['position']
            x, y = pos if engine == 'n8n' else (pos['x'], pos['y'])
            public.append({'id': ids[key], 'label': label(data['name'] if engine == 'n8n' else data['title']), 'type': label(data['type']), 'x': x, 'y': y, 'inputs': [] if engine == 'n8n' else names(data.get('variables')), 'outputs': [] if engine == 'n8n' else names(data.get('outputs'))})
        edges = []
        if engine == 'n8n':
            for origin, channels in source.get('connections', {}).items():
                for channel, branches in channels.items():
                    for index, branch in enumerate(branches):
                        for edge in branch:
                            edges.append({'source': ids[origin], 'target': ids[edge['node']], 'port': label(str(channel) + ':' + str(index) + ' → ' + str(edge.get('type', 'main')) + ':' + str(edge.get('index', 0)))})
        else:
            for edge in source.get('edges', []):
                edges.append({'source': ids[edge['source']], 'target': ids[edge['target']], 'port': label(str(edge.get('sourceHandle', 'source')) + ' → ' + str(edge.get('targetHandle', 'target')))})
        graphs.append({'engine': engine, 'nodes': public, 'edges': edges})
    result = {'schema_version': 1, 'project': project, 'slug': slug, 'title': label(title), 'captured_at': datetime.now(timezone.utc).isoformat(), 'sources': {'n8n_sha256': digest(n8n), 'dify_sha256': digest(dify)}, 'graphs': graphs}
    errors = validate_snapshot(result)
    if errors:
        raise ValueError('; '.join(errors))
    return result


def validate_snapshot(value):
    try:
        if not isinstance(value, dict) or set(value) != {'schema_version','project','slug','title','captured_at','sources','graphs'} or type(value['schema_version']) is not int or value['schema_version'] != 1:
            raise ValueError('Invalid snapshot schema')
        if value['project'] not in ('project-a','project-b') or not SLUG.fullmatch(value['slug']):
            raise ValueError('Invalid snapshot identity')
        label(value['title'])
        datetime.fromisoformat(value['captured_at'])
        if set(value['sources']) != {'n8n_sha256','dify_sha256'} or any(not re.fullmatch('[a-f0-9]{64}', x) for x in value['sources'].values()):
            raise ValueError('Invalid source hashes')
        graphs = value['graphs']
        if not isinstance(graphs, list) or len(graphs) != 2 or {g['engine'] for g in graphs} != {'n8n','dify'}:
            raise ValueError('Both framework graphs are required')
        for graph in graphs:
            if set(graph) != {'engine','nodes','edges'} or not 1 <= len(graph['nodes']) <= 300 or len(graph['edges']) > 3000:
                raise ValueError('Invalid graph schema or bounds')
            ids = set()
            for node in graph['nodes']:
                if set(node) != {'id','label','type','x','y','inputs','outputs'} or not re.fullmatch(r'n\d+', node['id']) or node['id'] in ids:
                    raise ValueError('Invalid node schema or duplicate id')
                ids.add(node['id'])
                label(node['label']); label(node['type'])
                if any(type(node[k]) not in (int,float) or not math.isfinite(node[k]) or abs(node[k]) > 100000 for k in ('x','y')):
                    raise ValueError('Invalid node position')
                for field in ('inputs','outputs'):
                    if not isinstance(node[field],list) or len(node[field]) > 100:
                        raise ValueError('Invalid node ports')
                    for text in node[field]: label(text)
            for edge in graph['edges']:
                if set(edge) != {'source','target','port'} or edge['source'] not in ids or edge['target'] not in ids:
                    raise ValueError('Invalid edge references')
                label(edge['port'])
        return []
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        return [str(error)]


def build_site(snapshots, outputdir):
    root = Path(outputdir)
    validated = set()
    for value in snapshots:
        errors = validate_snapshot(value)
        if errors or value['slug'] in validated:
            raise ValueError('Invalid or duplicate snapshot: ' + ';'.join(errors))
        validated.add(value['slug'])
    root.mkdir(parents=True, exist_ok=True)
    (root/'snapshots').mkdir(exist_ok=True)
    for name in ('index.html','app.js','style.css'):
        shutil.copyfile(Path(__file__).parent/'viewer_public'/name, root/name)
    catalog = []
    for value in snapshots:
        raw = json.dumps(value,ensure_ascii=False,indent=2).encode()
        (root/'snapshots'/(value['slug']+'.json')).write_bytes(raw)
        catalog.append({'slug': value['slug'],'title': value['title'],'project': value['project'],'sha256': hashlib.sha256(raw).hexdigest()})
    (root/'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--snapshots',nargs='+',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    build_site([json.loads(Path(p).read_text(encoding='utf-8')) for p in args.snapshots],args.output)
