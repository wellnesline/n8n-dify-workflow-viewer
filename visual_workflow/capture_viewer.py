"""Read registered graphs on the local host; save only the public projection."""
import base64
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from bootstrap import Client
from viewer import normalize, build_site, validate_snapshot, SLUG


def _safe_snapshot(snapshot):
    if validate_snapshot(snapshot):
        raise ValueError('Invalid registered public snapshot schema')
    texts = [snapshot['title']]
    for graph in snapshot['graphs']:
        for node in graph['nodes']:
            texts.extend([node['label'], node['type'], *node['inputs'], *node['outputs']])
        texts.extend(edge['port'] for edge in graph['edges'])
    sensitive = re.compile(r'(?i)(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+\S+|\b(?:password|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*\S+|\bsk-[a-z0-9_-]{16,}|\beyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}|https?://[^\s/@]+:[^\s/@]+@)')
    if any(sensitive.search(text) for text in texts):
        raise ValueError('Private values are not permitted in public labels')
    return snapshot


def _inside_file(path, repo_root):
    root = Path(repo_root).resolve()
    candidate = Path(path)
    if '..' in candidate.parts:
        raise ValueError('Registered files must not traverse parent paths')
    lexical = candidate.absolute()
    if not lexical.is_relative_to(root):
        raise ValueError('Registered files must stay within the repository')
    current = root
    for part in lexical.relative_to(root).parts:
        if part.startswith('.') or part.lower() in ('private', 'secrets', 'runtime'):
            raise ValueError('Private files cannot be registered for public viewing')
        current = current / part
        if current.is_symlink() or getattr(current, 'is_junction', lambda: False)():
            raise ValueError('Registered files must not traverse links')
    resolved = lexical.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file() or resolved.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Registered JSON file is missing or exceeds its bounds')
    return resolved


def load_registered_snapshots(registry, repo_root):
    """Read reviewed public snapshots; never load raw workflow or runtime files."""
    root = Path(repo_root).resolve()
    if isinstance(registry, dict):
        data = registry
    else:
        registry_path = Path(registry)
        if not registry_path.is_absolute():
            registry_path = root / registry_path
        try:
            data = json.loads(_inside_file(registry_path, root).read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError('Invalid workflow view registry') from None
    if not isinstance(data, dict) or set(data) != {'schema_version', 'views'} or type(data['schema_version']) is not int or data['schema_version'] != 1 or not isinstance(data['views'], list) or len(data['views']) > 300:
        raise ValueError('Invalid workflow view registry schema')
    snapshots = []
    slugs = set()
    for entry in data['views']:
        if not isinstance(entry, dict) or set(entry) != {'slug', 'snapshot'} or not isinstance(entry['slug'], str) or not SLUG.fullmatch(entry['slug']) or entry['slug'] in slugs:
            raise ValueError('Invalid or duplicate registered view identity')
        relative = entry['snapshot']
        if not isinstance(relative, str) or not relative or len(relative) > 512 or '\\' in relative or PureWindowsPath(relative).drive or PurePosixPath(relative).is_absolute() or any(part in ('', '.', '..') or part.startswith('.') or part.lower() in ('private', 'secrets', 'runtime') for part in relative.split('/')) or PurePosixPath(relative).suffix != '.json':
            raise ValueError('Registered snapshots must use safe repository-relative JSON paths')
        try:
            snapshot = json.loads(_inside_file(root / relative, root).read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError('Invalid registered public snapshot') from None
        _safe_snapshot(snapshot)
        if snapshot['slug'] != entry['slug']:
            raise ValueError('Registered view identity differs from its snapshot')
        slugs.add(entry['slug'])
        snapshots.append(snapshot)
    return snapshots


def merge_registered_snapshots(captured, registered):
    """Keep fresh incoming metadata; reject ambiguous public graph identities."""
    result = []
    by_slug = {}
    for snapshot in captured:
        _safe_snapshot(snapshot)
        if snapshot['slug'] in by_slug:
            raise ValueError('Duplicate captured view identity')
        by_slug[snapshot['slug']] = snapshot
        result.append(snapshot)
    seen = set()
    for snapshot in registered:
        _safe_snapshot(snapshot)
        slug = snapshot['slug']
        if slug in seen:
            raise ValueError('Duplicate registered view identity')
        seen.add(slug)
        if slug in by_slug:
            existing = by_slug[slug]
            if any(existing[key] != snapshot[key] for key in ('project', 'title', 'graphs')):
                raise ValueError('Conflicting registered public graph identity')
        else:
            by_slug[slug] = snapshot
            result.append(snapshot)
    return result


def main():
    runtime = Path(os.environ['POC_DIR'])
    secret = json.loads((runtime/'secrets.json').read_text())
    shared = json.loads((runtime/'bootstrap-state.json').read_text())
    n8n = Client('http://localhost:5678/rest')
    dify = Client('http://localhost:8080/console/api')
    n8n.call('POST','/login',{'emailOrLdapLoginId':secret['owner_email'],'password':secret['owner_password']})
    dify.call('POST','/login',{'email':secret['owner_email'],'password':base64.b64encode(secret['owner_password'].encode()).decode()})
    # Add further (project, slug, title, n8n workflow id, Dify app id) tuples here for additional registered flows.
    sources = [(p,p+'-poc',p+' · mock PoC',shared['workflows'][p],shared['apps'][p]) for p in ('project-a','project-b')]
    out = Path(__file__).parent/'viewer_generated'
    snapshots = []
    for project,slug,title,workflow_id,app_id in sources:
        workflow = n8n.call('GET','/workflows/'+workflow_id)['data']
        published = dify.call('GET','/apps/'+app_id+'/workflows/publish')
        snapshot = normalize(workflow, {'workflow':{'graph':published['graph']}}, project, slug,title)
        snapshots.append(snapshot)
    repo_root = Path(__file__).resolve().parents[1]
    registry = repo_root/'config/registered_workflow_views.json'
    if registry.is_file():  # optional: reviewed snapshots that are merged in without live capture
        snapshots = merge_registered_snapshots(snapshots, load_registered_snapshots(registry, repo_root))
    build_site(snapshots,out)
    print(json.dumps({'captured':len(snapshots),'node_counts':{s['slug']:{g['engine']:len(g['nodes']) for g in s['graphs']} for s in snapshots},'private_values_published':False}))

if __name__ == '__main__':
    main()
