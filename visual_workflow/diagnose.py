"""Print only bounded node error summaries, never full execution data or secrets."""
import json
import os
import base64
from pathlib import Path
import urllib.parse
from bootstrap import Client


def execution_data(value):
    if not isinstance(value, str):
        return value
    values = json.loads(value)
    if not isinstance(values, list):
        return values
    memo = {}
    def item(index):
        if index in memo:
            return memo[index]
        current = values[index]
        if isinstance(current, dict):
            result = memo[index] = {}
            result.update({k: item(int(v)) if isinstance(v, str) and v.isdigit() else v for k, v in current.items()})
            return result
        if isinstance(current, list):
            result = memo[index] = []
            result.extend(item(int(v)) if isinstance(v, str) and v.isdigit() else v for v in current)
            return result
        return current
    return item(0)


def main():
    runtime = Path(os.environ['POC_DIR'])
    secret = json.loads((runtime / 'secrets.json').read_text())
    state = json.loads((runtime / 'bootstrap-state.json').read_text())
    client = Client('http://localhost:5678/rest')
    client.call('POST', '/login', {'emailOrLdapLoginId': secret['owner_email'], 'password': secret['owner_password']})
    for project, workflow_id in state['workflows'].items():
        rows = client.call('GET', '/executions?filter=' + urllib.parse.quote(json.dumps({'workflowId': workflow_id})))['data']
        rows = rows.get('results', []) if isinstance(rows, dict) else rows
        for row in rows[:1]:
            detail = client.call('GET', '/executions/' + row['id'] + '?includeData=true')['data']
            result = execution_data(detail.get('data', {})).get('resultData', {})
            error = result.get('error', {})
            runs = result.get('runData', {}).get('Dify plan', [])
            dify = runs[0]['data']['main'][0][0]['json'].get('data', {}) if runs else {}
            outputs = dify.get('outputs') or {}
            print(json.dumps({'project': project, 'execution': row['id'], 'status': row.get('status'), 'last_node': result.get('lastNodeExecuted'), 'error_message': str(error.get('message', ''))[:300], 'description': str(error.get('description', ''))[:300], 'dify_status': dify.get('status'), 'dify_error': str(dify.get('error', ''))[:300], 'dify_output_types': {k: type(v).__name__ for k, v in outputs.items()}}))
    dify_client = Client('http://localhost:8080/console/api')
    dify_client.call('POST', '/login', {'email': secret['owner_email'], 'password': base64.b64encode(secret['owner_password'].encode()).decode()})
    for project, app_id in state['apps'].items():
        runs = dify_client.call('GET', '/apps/' + app_id + '/workflow-runs?triggered_from=app-run&limit=2')
        print(json.dumps({'project': project, 'dify_history_keys': list(runs), 'data': [{'id': x.get('id'), 'status': x.get('status'), 'keys': list(x)} for x in runs.get('data', [])]}))


if __name__ == '__main__':
    main()
