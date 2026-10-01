"""Secret-free exports for the actual n8n and Dify editors."""
import json

PROJECTS = ('project-a', 'project-b')


def dify_workflow(project='project-a'):
    if project not in PROJECTS:
        raise ValueError('Unsupported project')
    code = "def main(title: str) -> dict:\n    title = title.strip()\n    if not title or len(title) > 2000:\n        raise ValueError('title must contain 1..2000 characters')\n    return {'title': title, 'outline': 'PoC: ' + title + ' / plan -> mock production -> QA', 'project': '" + project + "', 'mock': True}\n"
    def node(node_id, typ, title, x, data):
        return {'id': node_id, 'type': 'custom', 'position': {'x': x, 'y': 200}, 'data': {'type': typ, 'title': title, 'desc': '', 'selected': False, **data}, 'sourcePosition': 'right', 'targetPosition': 'left', 'width': 244, 'height': 100}
    nodes = [node('start', 'start', 'Input / 入力', 0, {'variables': [{'variable': 'title', 'label': 'タイトル', 'type': 'text-input', 'required': True, 'max_length': 2000}]}), node('prepare', 'code', 'Prepare mock plan / 模擬企画', 320, {'code_language': 'python3', 'code': code, 'variables': [{'variable': 'title', 'value_selector': ['start', 'title']}], 'outputs': {'title': {'type': 'string', 'children': None}, 'outline': {'type': 'string', 'children': None}, 'project': {'type': 'string', 'children': None}, 'mock': {'type': 'boolean', 'children': None}}}), node('end', 'end', 'Result / 結果', 640, {'outputs': [{'variable': key, 'value_selector': ['prepare', key]} for key in ('title', 'outline', 'project', 'mock')]})]
    edges = [{'id': a + '-' + b, 'source': a, 'target': b, 'sourceHandle': 'source', 'targetHandle': 'target', 'type': 'custom', 'data': {'sourceType': at, 'targetType': bt, 'isInIteration': False}} for a, b, at, bt in [('start', 'prepare', 'start', 'code'), ('prepare', 'end', 'code', 'end')]]
    return {'version': '0.3.0', 'kind': 'app', 'app': {'name': project + ' | No-cost PoC', 'mode': 'workflow', 'icon': '🧪', 'icon_background': '#E4FBCC', 'description': 'Deterministic mock planning only. No models, videos or publishing.', 'use_icon_as_answer_icon': False}, 'dependencies': [], 'workflow': {'conversation_variables': [], 'environment_variables': [], 'features': {'file_upload': {'enabled': False}, 'opening_statement': '', 'retriever_resource': {'enabled': False}, 'sensitive_word_avoidance': {'enabled': False}, 'speech_to_text': {'enabled': False}, 'suggested_questions': [], 'suggested_questions_after_answer': {'enabled': False}, 'text_to_speech': {'enabled': False}}, 'graph': {'nodes': nodes, 'edges': edges, 'viewport': {'x': 0, 'y': 0, 'zoom': .85}}}}


def n8n_workflow(project='project-a', dify_credential_id='DIFY_CREDENTIAL', adapter_credential_id='ADAPTER_CREDENTIAL', webhook_credential_id='WEBHOOK_CREDENTIAL'):
    if project not in PROJECTS:
        raise ValueError('Unsupported project')
    def node(name, typ, x, params, version=1, credential=None):
        data = {'id': name.replace(' ', '-'), 'name': name, 'type': typ, 'typeVersion': version, 'position': [x, 200], 'parameters': params}
        if credential:
            data['credentials'] = credential
        return data
    http = 'n8n-nodes-base.httpRequest'
    dify_auth = {'httpHeaderAuth': {'id': dify_credential_id, 'name': project + ' Dify PoC'}}
    adapter_auth = {'httpHeaderAuth': {'id': adapter_credential_id, 'name': 'PoC adapter'}}
    nodes = [node('PoC request', 'n8n-nodes-base.webhook', 0, {'httpMethod': 'POST', 'path': 'visual-poc-' + project, 'authentication': 'headerAuth', 'responseMode': 'responseNode', 'options': {}}, 2, {'httpHeaderAuth': {'id': webhook_credential_id, 'name': 'PoC webhook'}}),
             node('Dify plan', http, 280, {'method': 'POST', 'url': 'http://nginx/v1/workflows/run', 'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth', 'sendBody': True, 'specifyBody': 'json', 'jsonBody': "={{ JSON.stringify({inputs:{title:$json.body.title},response_mode:'blocking',user:'poc-" + project + "'}) }}", 'options': {'timeout': 60000}}, 4.2, dify_auth),
             node('Submit mock job', http, 560, {'method': 'POST', 'url': 'http://poc-adapter:8787/jobs', 'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth', 'sendHeaders': True, 'headerParameters': {'parameters': [{'name': 'Idempotency-Key', 'value': "={{ $('PoC request').item.json.body.idempotency_key }}"}]}, 'sendBody': True, 'specifyBody': 'json', 'jsonBody': "={{ JSON.stringify({project:'" + project + "',input:{title:$json.data.outputs.title,outline:$json.data.outputs.outline,mock:true} }) }}", 'options': {'timeout': 10000}}, 4.2, adapter_auth),
             node('Wait for mock job', 'n8n-nodes-base.wait', 840, {'amount': 1, 'unit': 'seconds'}, 1.1),
             node('Observe job', http, 1120, {'url': '={{ "http://poc-adapter:8787/jobs/" + $json.job_id }}', 'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth', 'options': {'timeout': 10000}}, 4.2, adapter_auth),
             node('Return result', 'n8n-nodes-base.respondToWebhook', 1400, {'respondWith': 'json', 'responseBody': '={{ $json }}', 'options': {}}, 1.4)]
    nodes.insert(2, node('Validate Dify result', 'n8n-nodes-base.code', 420, {'jsCode': "const d = $input.first().json.data; if (d?.status !== 'succeeded' || d.outputs?.project !== '" + project + "' || typeof d.outputs?.title !== 'string' || !d.outputs.title.length) { throw new Error('Dify PoC did not produce a successful project-specific plan'); } return $input.all();"}, 2))
    for i, current in enumerate(nodes):
        current['position'] = [i * 280, 200]
    names = [n['name'] for n in nodes]
    connections = {a: {'main': [[{'node': b, 'type': 'main', 'index': 0}]]} for a, b in zip(names, names[1:])}
    return {'name': project + ' | Dify + observable mock job PoC', 'nodes': nodes, 'connections': connections, 'settings': {'executionOrder': 'v1', 'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all', 'saveManualExecutions': True}, 'active': False}
