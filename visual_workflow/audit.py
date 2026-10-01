"""Read-only verification of scoped instructions and actual running deployment."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
# Optional: instruction/manifest files of other projects whose hashes are recorded (os.pathsep-separated paths).
EXTERNAL = [Path(p) for p in os.environ.get('EXTERNAL_POLICY_FILES', '').split(os.pathsep) if p]
POC_DIR = os.environ.get('POC_DIR', '')  # WSL path of the PoC runtime directory (required by the WSL-backed checks)


def verify_policy_snapshot(snapshot=None):
    path = Path(snapshot) if snapshot else HERE / 'evidence/external-policy.json'
    expected = json.loads(path.read_text(encoding='utf-8'))
    if EXTERNAL and set(expected) != {p.as_posix() for p in EXTERNAL}:
        raise ValueError('Unexpected external policy scope')
    for file in EXTERNAL:
        if hashlib.sha256(file.read_bytes()).hexdigest() != expected[file.as_posix()]:
            raise ValueError('External project policy changed: ' + file.name)
    for file in [p for p in (ROOT / 'AGENTS.md', *EXTERNAL) if p.is_file()]:
        text = file.read_text(encoding='utf-8')
        if not all(word in text for word in ('n8n', 'Dify', 'OpenSpec', 'LangGraph', '既存', '新規', 'public_view', 'ログイン不要', '閲覧専用', '外部URL', '固定テスト', 'gate')):
            raise ValueError('Required scoped instructions missing')
    return {'status': 'passed', 'external_policy_files': len(EXTERNAL)}


def verify_runtime():
    if not POC_DIR:
        raise ValueError('Set POC_DIR to the PoC runtime directory (WSL path)')
    ids = subprocess.check_output(['wsl', '-e', 'docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=n8n-dify-poc'], encoding='utf-8').split()
    if not ids:
        raise ValueError('PoC containers missing')
    # Keep raw inspection (including environment variables) in memory only.
    containers = json.loads(subprocess.check_output(['wsl', '-e', 'docker', 'inspect', *ids], encoding='utf-8'))
    required = {'n8n', 'api', 'worker', 'web', 'nginx', 'sandbox', 'poc-adapter'}
    running = set()
    images = {}
    ports = []
    for container in containers:
        service = container['Config']['Labels']['com.docker.compose.service']
        if container['State']['Running']:
            running.add(service)
        elif service in required:
            raise ValueError('Required service is not running: ' + service)
        if container['HostConfig']['RestartPolicy']['Name'] not in ('no', ''):
            raise ValueError('Automatic restart configured')
        for bindings in container['NetworkSettings']['Ports'].values():
            for binding in bindings or []:
                if binding['HostIp'] != '127.0.0.1':
                    raise ValueError('Non-loopback runtime binding')
                ports.append(int(binding['HostPort']))
        for mount in container['Mounts']:
            if mount['Type'] == 'bind' and not mount['Source'].startswith(POC_DIR + '/'):
                raise ValueError('Runtime mounted an unrelated host path')
        images[service] = {'image': container['Config']['Image'], 'image_id': container['Image']}
    if not required <= running:
        raise ValueError('Required service set incomplete')
    return {'status': 'passed', 'running_services': sorted(running), 'loopback_ports': sorted(ports), 'no_production_mounts': True, 'no_automatic_restart': True, 'images': images}


if __name__ == '__main__':
    result = {'policy': verify_policy_snapshot(), 'deployment': verify_runtime()}
    (HERE / 'evidence/configuration.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'policy': result['policy']['status'], 'runtime': result['deployment']['status'], 'running_services': len(result['deployment']['running_services'])}))
