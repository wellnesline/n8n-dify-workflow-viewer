"""Run inside WSL; installs/configures only the directory named by the POC_DIR environment variable."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess

RUNTIME = Path(os.environ['POC_DIR'])  # e.g. an empty directory dedicated to this PoC
DIFY_VERSION = '1.17.1'
N8N_VERSION = '2.41.6'
SOURCE = Path(__file__).resolve().parent


def compose(*args, capture=False, log=None):
    command = ['docker', 'compose', '--project-name', 'n8n-dify-poc', '--project-directory', str(RUNTIME / 'dify/docker'), '--env-file', str(RUNTIME / 'dify/docker/.env'), '-f', str(RUNTIME / 'dify/docker/docker-compose.yaml'), '-f', str(RUNTIME / 'override.yaml'), *args]
    if log:
        with open(log, 'w') as output:
            subprocess.run(command, check=True, stdout=output, stderr=output, text=True)
        return None
    return subprocess.run(command, check=True, capture_output=capture, text=True).stdout


def prepare():
    os.umask(0o077)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    runtime_source = RUNTIME / 'source'
    runtime_source.mkdir(exist_ok=True)
    for name in ('bridge.py', 'templates.py', 'bootstrap.py'):
        source = SOURCE / name
        if source.exists():
            shutil.copyfile(source, runtime_source / name)
    secret_file = RUNTIME / 'secrets.json'
    if not secret_file.exists():
        secret_file.write_text(json.dumps({'owner_email': 'poc@local.example', 'owner_password': 'Poc9' + secrets.token_hex(20), 'adapter_token': secrets.token_urlsafe(36), 'webhook_token': secrets.token_urlsafe(36), 'n8n_encryption_key': secrets.token_hex(32)}, indent=2))
    secret_file.chmod(0o600)
    secret = json.loads(secret_file.read_text())
    checkout = RUNTIME / 'dify'
    if not checkout.exists():
        subprocess.run(['git', 'clone', '--depth', '1', '--branch', DIFY_VERSION, 'https://github.com/langgenius/dify.git', str(checkout)], check=True, capture_output=True)
    tag = subprocess.check_output(['git', '-C', str(checkout), 'describe', '--tags', '--exact-match'], text=True).strip()
    if tag != DIFY_VERSION:
        raise RuntimeError('Dify checkout version does not match pin')
    env_file = checkout / 'docker/.env'
    if not env_file.exists():
        data = (checkout / 'docker/.env.example').read_text()
        replacements = {'SECRET_KEY': secrets.token_urlsafe(42), 'DB_PASSWORD': secrets.token_urlsafe(32), 'REDIS_PASSWORD': secrets.token_urlsafe(32), 'SANDBOX_API_KEY': secrets.token_urlsafe(32), 'PLUGIN_DAEMON_KEY': secrets.token_urlsafe(32), 'PLUGIN_DIFY_INNER_API_KEY': secrets.token_urlsafe(32), 'CONSOLE_WEB_URL': 'http://localhost:8080', 'CONSOLE_API_URL': 'http://localhost:8080', 'APP_WEB_URL': 'http://localhost:8080', 'SERVICE_API_URL': 'http://localhost:8080', 'FILES_URL': 'http://localhost:8080', 'CHECK_UPDATE_URL': '', 'MARKETPLACE_ENABLED': 'false', 'TELEMETRY_ENABLED': 'false'}
        lines = data.splitlines()
        present = set()
        for i, line in enumerate(lines):
            if '=' in line and not line.startswith('#'):
                key = line.split('=', 1)[0]
                if key in replacements:
                    lines[i] = key + '=' + replacements[key]
                    present.add(key)
        lines += [k + '=' + v for k, v in replacements.items() if k not in present]
        env_file.write_text('\n'.join(lines) + '\n')
        env_file.chmod(0o600)
    # The API and sandbox must share the same key; the upstream template exposes
    # two variables with identical defaults, so changing only one causes HTTP 401.
    env_lines = env_file.read_text().splitlines()
    sandbox_key = next(line.split('=', 1)[1] for line in env_lines if line.startswith('SANDBOX_API_KEY='))
    env_lines = [line for line in env_lines if not line.startswith('CODE_EXECUTION_API_KEY=')]
    env_lines.append('CODE_EXECUTION_API_KEY=' + sandbox_key)
    env_file.write_text('\n'.join(env_lines) + '\n')
    adapter_env = RUNTIME / 'adapter.env'
    adapter_env.write_text('ADAPTER_TOKEN=' + secret['adapter_token'] + '\n')
    n8n_env = RUNTIME / 'n8n.env'
    n8n_env.write_text('\n'.join(['N8N_ENCRYPTION_KEY=' + secret['n8n_encryption_key'], 'N8N_DIAGNOSTICS_ENABLED=false', 'N8N_VERSION_NOTIFICATIONS_ENABLED=false', 'N8N_TEMPLATES_ENABLED=false', 'N8N_SECURE_COOKIE=false', 'N8N_HOST=localhost', 'N8N_PORT=5678', 'N8N_PROTOCOL=http', 'WEBHOOK_URL=http://localhost:5678/', 'N8N_EDITOR_BASE_URL=http://localhost:5678/', 'GENERIC_TIMEZONE=Asia/Tokyo', 'TZ=Asia/Tokyo', 'N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=true', 'N8N_BLOCK_ENV_ACCESS_IN_NODE=true']) + '\n')
    for p in (adapter_env, n8n_env):
        p.chmod(0o600)
    raw = subprocess.check_output(['docker', 'compose', '--project-directory', str(checkout / 'docker'), '--env-file', str(env_file), '-f', str(checkout / 'docker/docker-compose.yaml'), 'config', '--format', 'json'], text=True, stderr=subprocess.DEVNULL)
    original = json.loads(raw)
    lines = ['services:']
    for name in original['services']:
        lines += ['  ' + name + ':', '    restart: "no"']
        if 'ports' in original['services'][name]:
            lines += ['    ports: !override', '      - "127.0.0.1:8080:80"'] if name == 'nginx' else ['    ports: !reset []']
    lines += ['  n8n:', '    image: docker.n8n.io/n8nio/n8n:' + N8N_VERSION, '    restart: "no"', '    ports:', '      - "127.0.0.1:5678:5678"', '    env_file:', '      - ' + str(n8n_env), '    volumes:', '      - n8n-data:/home/node/.n8n', '  poc-adapter:', '    image: python:3.12.12-slim', '    restart: "no"', '    ports:', '      - "127.0.0.1:8787:8787"', '    env_file:', '      - ' + str(adapter_env), '    working_dir: /app', '    command: ["python", "bridge.py", "--host", "0.0.0.0", "--db", "/data/jobs.sqlite3"]', '    volumes:', '      - ' + str(runtime_source) + ':/app:ro', '      - adapter-data:/data', 'volumes:', '  n8n-data:', '  adapter-data:']
    (RUNTIME / 'override.yaml').write_text('\n'.join(lines) + '\n')
    config = json.loads(compose('config', '--format', 'json', capture=True))
    for name, service in config['services'].items():
        if service.get('restart') not in (None, 'no'):
            raise RuntimeError('Automatic restart is not permitted')
        for binding in service.get('ports', []):
            if binding.get('host_ip') != '127.0.0.1':
                raise RuntimeError('Non-loopback port binding')
        for mount in service.get('volumes', []):
            if mount.get('type') == 'bind' and not str(mount['source']).startswith(str(RUNTIME) + '/'):
                raise RuntimeError('A host mount escaped the isolated runtime')
    summary = {'dify': DIFY_VERSION, 'n8n': N8N_VERSION, 'runtime': str(RUNTIME), 'loopback_only': True, 'production_mounts': False, 'automatic_restart': False, 'services': list(config['services'])}
    (RUNTIME / 'deployment.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'up', 'down', 'status', 'bootstrap'])
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
    elif args.action == 'bootstrap':
        subprocess.run(['python3', str(RUNTIME / 'source/bootstrap.py')], check=True)
    elif args.action == 'status':
        print(compose('ps', '--format', 'json', capture=True))
    else:
        if args.action == 'up':
            subprocess.run(['systemctl', 'start', 'docker'], check=True, capture_output=True)
        compose(*(['up', '-d'] if args.action == 'up' else ['down']), log=str(RUNTIME / ('compose-' + args.action + '.log')))
        print(json.dumps({'action': args.action, 'status': 'success'}))


if __name__ == '__main__':
    main()
