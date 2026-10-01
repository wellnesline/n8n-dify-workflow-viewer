"""Explicit manifest gate. Project instruction files are guidance; this is an explicit check, not a global hook."""
import argparse
import json
import os
from pathlib import Path
import hashlib
import ipaddress
import re
from urllib.parse import urlsplit


def public_url(value):
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ''
        if parsed.scheme != 'https' or not hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or hostname == 'localhost' or hostname.endswith(('.localhost','.local')):
            return False
        try:
            ipaddress.ip_address(hostname)
            return False
        except ValueError:
            pass
        return '.' in hostname and parsed.port in (None,443) and parsed.path.startswith('/viewer/')
    except (ValueError,TypeError):
        return False

REQUIRED = {'n8n', 'dify', 'openspec', 'langgraph'}
GRAPH_STYLE = 'job-agent-directed-graph'
CODING_MODEL = os.environ.get('CODING_DELEGATION_MODEL', 'example-coding-model')  # configurable default
CODING_EFFORT = 'medium'


def validate_manifest(data):
    if not isinstance(data, dict):
        return ['manifest must be an object']
    errors = []
    if data.get('project') not in ('project-a', 'project-b'):
        errors.append('unsupported project')
    if data.get('scope') not in ('new-pipeline', 'new-application', 'legacy-maintenance'):
        errors.append('scope must be explicit')
    if data.get('scope') == 'legacy-maintenance':
        if data.get('migrate_legacy') is not False:
            errors.append('legacy migration must not be implicit')
        return errors
    frameworks = data.get('frameworks')
    if not isinstance(frameworks, list) or any(not isinstance(x, str) for x in frameworks):
        errors.append('frameworks must be a string list')
        frameworks = []
    missing = REQUIRED - set(frameworks)
    if missing:
        exception = data.get('exception', {})
        if not isinstance(exception, dict) or exception.get('user_authorized') is not True or not isinstance(exception.get('reason'), str) or not exception['reason'].strip() or not isinstance(exception.get('authorization_reference'), str) or not exception['authorization_reference'].strip():
            errors.append('missing frameworks: ' + ', '.join(sorted(missing)) + '; documented explicit user exception required')
    for field in ('n8n_export', 'dify_export', 'job_contract', 'verification'):
        if field not in data or not isinstance(data[field], str) or not data[field].strip():
            errors.append(field + ' must reference the design or evidence')
    if data.get('migrate_legacy') is not False:
        errors.append('migrate_legacy must be false unless separately authorized')
    view = data.get('public_view')
    if not isinstance(view,dict):
        errors.append('public_view is required for new development')
    else:
        if not public_url(view.get('url')):
            errors.append('public_view.url must be an external HTTPS viewer URL without credentials')
        if view.get('read_only') is not True or view.get('authentication') != 'none':
            errors.append('public_view must be read_only and authentication none')
        for field in ('snapshot','verification'):
            if not isinstance(view.get(field),str) or not view[field].strip():
                errors.append('public_view.'+field+' must reference a file')
    return errors


def verify_public_view(data, base_path):
    if data.get('scope') == 'legacy-maintenance':
        return []
    errors=validate_manifest(data)
    if errors:
        return errors
    try:
        try:
            from .viewer import normalize,validate_snapshot
        except ImportError:
            from viewer import normalize,validate_snapshot
        root=Path(base_path)
        view=data['public_view']
        snapshot_path=root/view['snapshot']
        raw=snapshot_path.read_bytes()
        snapshot=json.loads(raw)
        errors=validate_snapshot(snapshot)
        if errors:
            return errors
        if urlsplit(view['url']).path != '/viewer/'+snapshot['slug']+'/':
            errors.append('public_view URL must select its own snapshot slug')
        n8n=json.loads((root/data['n8n_export']).read_text(encoding='utf-8'))
        dify=json.loads((root/data['dify_export']).read_text(encoding='utf-8'))
        expected=normalize(n8n,dify,data['project'],snapshot['slug'],snapshot['title'])
        if snapshot['project'] != data['project'] or snapshot['graphs'] != expected['graphs']:
            errors.append('public_view graph differs from current workflow exports')
        receipt=json.loads((root/view['verification']).read_text(encoding='utf-8'))
        required={'status':'passed','url':view['url'],'read_only':True,'authentication':'none','http_status':200,'snapshot_sha256':hashlib.sha256(raw).hexdigest(),'n8n_export_sha256':hashlib.sha256((root/data['n8n_export']).read_bytes()).hexdigest(),'dify_export_sha256':hashlib.sha256((root/data['dify_export']).read_bytes()).hexdigest()}
        if any(type(receipt.get(k)) is not type(v) or receipt[k] != v for k,v in required.items()):
            errors.append('public_view verification is missing, stale or not anonymous HTTP200 evidence')
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        errors.append('public_view files must exist and contain valid current graph/evidence JSON')
    return errors


def verify_future_defaults(data, base_path):
    """Additional delivery contract for new profiles registered after this policy."""
    if data.get('scope') == 'legacy-maintenance':
        return []
    errors = []
    delegation = data.get('coding_delegation')
    if not isinstance(delegation, dict) or delegation != {
        'default_model': CODING_MODEL,
        'reasoning_effort': CODING_EFFORT,
        'unavailable': 'stop-and-report',
    }:
        errors.append('coding_delegation must default to the configured default model with medium effort and stop if unavailable')
    view = data.get('inspection_view')
    if not isinstance(view, dict):
        return errors + ['inspection_view is required for graphical job/agent delivery']
    if view.get('style') != GRAPH_STYLE or view.get('default') is not True:
        errors.append('inspection_view must default to the directed job/agent graph')
    if not public_url(view.get('url')) or view.get('read_only') is not True or view.get('authentication') != 'none':
        errors.append('inspection_view must be an anonymous read-only external HTTPS page')
    if not isinstance(view.get('graph'), str) or not view['graph'].strip() or not isinstance(view.get('verification'), str) or not view['verification'].strip():
        return errors + ['inspection_view graph and verification files are required']
    try:
        base = Path(base_path).resolve()
        graph_path = (base / view['graph']).resolve()
        receipt_path = (base / view['verification']).resolve()
        if not graph_path.is_relative_to(base) or not receipt_path.is_relative_to(base):
            raise ValueError('inspection files must stay beside their manifest')
        raw = graph_path.read_bytes()
        graph = json.loads(raw)
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        snapshot = json.loads((base / data['public_view']['snapshot']).read_text(encoding='utf-8'))
        if set(graph) != {'schema_version','style','title','source_snapshot_sha256','nodes','edges'} or type(graph['schema_version']) is not int or graph['schema_version'] != 1 or graph['style'] != GRAPH_STYLE:
            raise ValueError('inspection graph schema/style mismatch')
        if not isinstance(graph['title'], str) or not graph['title'].strip() or len(graph['title']) > 160:
            raise ValueError('inspection graph title missing')
        snapshot_hash = hashlib.sha256((base / data['public_view']['snapshot']).read_bytes()).hexdigest()
        if graph['source_snapshot_sha256'] != snapshot_hash:
            raise ValueError('inspection graph source snapshot is stale')
        nodes, edges = graph['nodes'], graph['edges']
        if not isinstance(nodes, list) or not 2 <= len(nodes) <= 100 or not isinstance(edges, list) or not 1 <= len(edges) <= 300:
            raise ValueError('inspection graph needs bounded nodes and directed edges')
        source_ids = {(g['engine'], n['id']) for g in snapshot['graphs'] for n in g['nodes']}
        ids = set()
        kinds = set()
        for node in nodes:
            if not isinstance(node, dict) or set(node) != {'id','label','kind','job','implementation','explanation','source'}:
                raise ValueError('inspection node schema mismatch')
            identity = node['id']
            if not isinstance(identity, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', identity) or identity in ids:
                raise ValueError('inspection node id invalid/duplicated')
            ids.add(identity)
            kind = node['kind']
            if kind not in ('job','ai','program','human','external'):
                raise ValueError('inspection node kind invalid')
            kinds.add(kind)
            for field in ('label','job','implementation','explanation'):
                value = node[field]
                if not isinstance(value, str) or not value.strip() or len(value) > 240:
                    raise ValueError('inspection node needs human-readable job, implementation and explanation')
            source = node['source']
            if source is not None and (not isinstance(source, dict) or set(source) != {'engine','node_id'} or (source['engine'],source['node_id']) not in source_ids):
                raise ValueError('inspection node source must identify a real n8n/Dify node')
            if kind in ('job','ai','program') and source is None:
                raise ValueError('job/AI/program nodes need a real source graph reference')
        if not {'job','program'}.issubset(kinds):
            raise ValueError('inspection graph needs job and implementation categories')
        for edge in edges:
            if not isinstance(edge, dict) or set(edge) != {'source','target','label'} or edge['source'] not in ids or edge['target'] not in ids or edge['source'] == edge['target'] or not isinstance(edge['label'],str) or not edge['label'].strip() or len(edge['label']) > 160:
                raise ValueError('inspection directed edge invalid')
        required = {'status':'passed','url':view['url'],'http_status':200,'read_only':True,'authentication':'none','graph_sha256':hashlib.sha256(raw).hexdigest(),'svg_node_count':len(nodes),'svg_edge_count':len(edges),'node_selection_verified':True}
        if any(type(receipt.get(key)) is not type(value) or receipt[key] != value for key,value in required.items()):
            raise ValueError('inspection viewer evidence is missing or stale')
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        errors.append(str(exc) or 'inspection graph invalid')
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest')
    parser.add_argument('--future-defaults', action='store_true', help='Require graphical inspection and coding delegation for new work')
    args = parser.parse_args()
    path=Path(args.manifest)
    data=json.loads(path.read_text(encoding='utf-8'))
    errors = verify_public_view(data,path.parent)
    if args.future_defaults:
        errors += verify_future_defaults(data,path.parent)
    print(json.dumps({'status': 'failed' if errors else 'passed', 'errors': errors}, ensure_ascii=False))
    return 2 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
