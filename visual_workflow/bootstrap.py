"""Initialize local owners, import secret-free exports, and run actual service E2E."""
from __future__ import annotations

import base64
import http.cookiejar
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from templates import PROJECTS, dify_workflow, n8n_workflow

RUNTIME = Path(os.environ['POC_DIR'])


class Client:
    def __init__(self, base):
        self.base = base
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def call(self, method, path, body=None, headers=None):
        request_headers = {'Content-Type': 'application/json', **(headers or {})}
        for cookie in self.cookies:
            if 'csrf_token' in cookie.name:
                request_headers['X-CSRF-Token'] = cookie.value
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=request_headers)
        try:
            with self.opener.open(request, timeout=90) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            # Local diagnostics only, omit authentication secrets and raw body.
            try:
                error = json.loads(exc.read())
                message = str(error.get('message', error.get('error', error.get('code', ''))))[:240]
            except (ValueError, AttributeError):
                message = ''
            raise RuntimeError(f'{method} {path}: HTTP {exc.code} {message}') from None


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    temporary.replace(path)


def wait_ready(client, path, seconds=300):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            return client.call('GET', path)
        except (RuntimeError, urllib.error.URLError, TimeoutError):
            time.sleep(2)
    raise RuntimeError('Service readiness timed out: ' + path)


def main():
    os.umask(0o077)
    secrets_file = RUNTIME / 'secrets.json'
    secret = json.loads(secrets_file.read_text())
    state_file = RUNTIME / 'bootstrap-state.json'
    state = json.loads(state_file.read_text()) if state_file.exists() else {'apps': {}, 'workflows': {}, 'credentials': {}}
    dify = Client('http://localhost:8080/console/api')
    n8n = Client('http://localhost:5678/rest')
    setup = wait_ready(dify, '/setup')
    wait_ready(Client('http://localhost:5678'), '/healthz')
    if setup['step'] == 'not_started':
        dify.call('POST', '/setup', {'email': secret['owner_email'], 'name': 'Local PoC Owner', 'password': secret['owner_password'], 'language': 'ja-JP'})
    dify.call('POST', '/login', {'email': secret['owner_email'], 'password': base64.b64encode(secret['owner_password'].encode()).decode(), 'remember_me': True})
    settings = n8n.call('GET', '/settings').get('data', {})
    if settings.get('userManagement', {}).get('showSetupOnFirstLoad'):
        n8n.call('POST', '/owner/setup', {'email': secret['owner_email'], 'password': secret['owner_password'], 'firstName': 'Local', 'lastName': 'PoC'})
    else:
        n8n.call('POST', '/login', {'emailOrLdapLoginId': secret['owner_email'], 'password': secret['owner_password']})

    exports = RUNTIME / 'exports'
    exports.mkdir(exist_ok=True)

    def credential(key, name, header, value):
        if key not in state['credentials']:
            result = n8n.call('POST', '/credentials', {'name': name, 'type': 'httpHeaderAuth', 'data': {'name': header, 'value': value}})['data']
            state['credentials'][key] = result['id']
            save(state_file, state)
        return state['credentials'][key]

    adapter_id = credential('adapter', 'PoC adapter', 'Authorization', 'Bearer ' + secret['adapter_token'])
    webhook_id = credential('webhook', 'PoC webhook', 'X-PoC-Key', secret['webhook_token'])
    for project in PROJECTS:
        dsl = dify_workflow(project)
        save(exports / ('dify-' + project + '.json'), dsl)
        if project not in state['apps']:
            imported = dify.call('POST', '/apps/imports', {'mode': 'yaml-content', 'yaml_content': json.dumps(dsl, ensure_ascii=False)})
            if imported.get('status') not in ('completed', 'completed-with-warnings'):
                raise RuntimeError('Dify import did not complete: ' + str(imported.get('status')) + ' ' + str(imported.get('error', ''))[:240])
            state['apps'][project] = imported['app_id']
            save(state_file, state)
        app_id = state['apps'][project]
        published = dify.call('GET', '/apps/' + app_id + '/workflows/publish')
        if not published:
            dify.call('POST', '/apps/' + app_id + '/workflows/publish', {'marked_name': 'No-cost PoC', 'marked_comment': 'Mock plan only; no model API'})
        keys = dify.call('GET', '/apps/' + app_id + '/api-keys').get('data', [])
        key = keys[0]['token'] if keys else dify.call('POST', '/apps/' + app_id + '/api-keys')['token']
        dify_id = credential(project, project + ' Dify PoC', 'Authorization', 'Bearer ' + key)
        workflow = n8n_workflow(project, dify_id, adapter_id, webhook_id)
        # Exports contain placeholders instead of local credential IDs.
        save(exports / ('n8n-' + project + '.json'), n8n_workflow(project))
        if project not in state['workflows']:
            created = n8n.call('POST', '/workflows', workflow)['data']
            state['workflows'][project] = created['id']
            save(state_file, state)
        workflow_id = state['workflows'][project]
        saved = n8n.call('GET', '/workflows/' + workflow_id)['data']
        if saved.get('nodes') != workflow['nodes'] or saved.get('connections') != workflow['connections']:
            if saved.get('active'):
                n8n.call('POST', '/workflows/' + workflow_id + '/deactivate', {})
            saved = n8n.call('PATCH', '/workflows/' + workflow_id, {'name': workflow['name'], 'nodes': workflow['nodes'], 'connections': workflow['connections'], 'settings': workflow['settings']})['data']
        if not saved.get('active'):
            n8n.call('POST', '/workflows/' + workflow_id + '/activate', {'versionId': saved['versionId']})

    results = []
    webhook = Client('http://localhost:5678')
    for project in PROJECTS:
        title = project + ' visual integration PoC'
        idem = 'e2e-' + str(uuid.uuid4())
        result = webhook.call('POST', '/webhook/visual-poc-' + project, {'title': title, 'idempotency_key': idem}, {'X-PoC-Key': secret['webhook_token']})
        if result.get('status') != 'completed' or result.get('project') != project or result.get('result', {}).get('title') != title or result.get('result', {}).get('mock') is not True:
            raise RuntimeError('End-to-end result mismatch: ' + project)
        repeated = webhook.call('POST', '/webhook/visual-poc-' + project, {'title': title, 'idempotency_key': idem}, {'X-PoC-Key': secret['webhook_token']})
        if repeated.get('job_id') != result['job_id']:
            raise RuntimeError('Duplicate webhook submission created another job')
        for _ in range(10):
            rows = n8n.call('GET', '/executions?filter=' + urllib.parse.quote(json.dumps({'workflowId': state['workflows'][project]})))['data']
            execution_list = rows.get('results', []) if isinstance(rows, dict) else rows
            if len(execution_list) >= 2 and all(row.get('status') == 'success' for row in execution_list[:2]):
                break
            time.sleep(.2)
        else:
            raise RuntimeError('Successful n8n execution history missing')
        runs = dify.call('GET', '/apps/' + state['apps'][project] + '/workflow-runs?triggered_from=app-run&limit=2')
        run_list = runs.get('data', [])
        if not run_list or any(run.get('status') != 'succeeded' for run in run_list[:2]):
            raise RuntimeError('Successful Dify run history missing')
        results.append({'project': project, 'job_id': result['job_id'], 'status': result['status'], 'mock': True, 'idempotent': True, 'n8n_workflow_id': state['workflows'][project], 'dify_app_id': state['apps'][project], 'n8n_execution_ids': [row['id'] for row in execution_list[:2]], 'n8n_execution_status': 'success', 'dify_run_ids': [row['id'] for row in run_list[:2]], 'dify_run_status': 'succeeded'})
    report = {'status': 'passed', 'verified_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'actual_services': True, 'model_calls': 0, 'production_calls': 0, 'results': results, 'deployment': json.loads((RUNTIME / 'deployment.json').read_text())}
    save(RUNTIME / 'runtime-evidence.json', report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
