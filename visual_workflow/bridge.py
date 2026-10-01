"""Isolated simulated jobs. Never invokes production systems, shell commands or model APIs."""
from __future__ import annotations

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sqlite3
import threading
import time
import uuid

PROJECTS = ('project-a', 'project-b')


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, project TEXT, idem TEXT, payload TEXT, created REAL, status TEXT, UNIQUE(project, idem))')
        self.db.commit()

    def create(self, payload, key):
        if not isinstance(payload, dict) or set(payload) - {'project', 'input'}:
            raise ValueError('Only project and input are supported')
        project, inputs = payload.get('project'), payload.get('input')
        if project not in PROJECTS or not isinstance(inputs, dict):
            raise ValueError('Unsupported project or input')
        if set(inputs) - {'title', 'outline', 'mock', 'simulate_failure'}:
            raise ValueError('Unsupported input fields')
        if not isinstance(inputs.get('title'), str) or not 1 <= len(inputs['title']) <= 2000:
            raise ValueError('title must contain 1..2000 characters')
        if 'outline' in inputs and (not isinstance(inputs['outline'], str) or len(inputs['outline']) > 8000):
            raise ValueError('outline must be a string of at most 8000 characters')
        if 'simulate_failure' in inputs and type(inputs['simulate_failure']) is not bool:
            raise ValueError('simulate_failure must be boolean')
        if 'mock' in inputs and inputs['mock'] is not True:
            raise ValueError('Only mock jobs are supported')
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise ValueError('Idempotency-Key is required, maximum 128 characters')
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        with self.lock:
            row = self.db.execute('SELECT * FROM jobs WHERE project=? AND idem=?', (project, key)).fetchone()
            if row:
                if row['payload'] != data:
                    raise FileExistsError('Idempotency key was already used with different input')
                return self.get(row['id'])
            job_id = str(uuid.uuid4())
            self.db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?)', (job_id, project, key, data, time.time(), 'queued'))
            self.db.commit()
            return self.get(job_id)

    def get(self, job_id):
        with self.lock:
            row = self.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                return None
            inputs = json.loads(row['payload'])['input']
            elapsed = time.time() - row['created']
            status = 'queued' if elapsed < .15 else 'running' if elapsed < .6 else 'failed' if inputs.get('simulate_failure') else 'completed'
            self.db.execute('UPDATE jobs SET status=? WHERE id=?', (status, job_id))
            self.db.commit()
            result = {'mock': True, 'project': row['project'], 'title': inputs['title'], 'outline': inputs.get('outline', '')} if status == 'completed' else None
            error = {'code': 'SIMULATED_FAILURE', 'message': 'Requested mock failure'} if status == 'failed' else None
            return {'job_id': job_id, 'project': row['project'], 'status': status, 'progress': 100 if status in ('completed', 'failed') else 50 if status == 'running' else 0, 'result': result, 'error': error, 'mock': True}

    def close(self):
        self.db.close()


def make_server(host, port, store, token):
    if not isinstance(token, str) or len(token) < 24:
        raise ValueError('Adapter token must contain at least 24 characters')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log request bodies or authorization headers.

        def reply(self, code, body):
            raw = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def authorized(self):
            return hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token)

        def do_GET(self):
            if self.path == '/health':
                return self.reply(200, {'status': 'ok', 'mode': 'mock', 'projects': list(PROJECTS)})
            if not self.authorized():
                return self.reply(401, {'error': 'unauthorized'})
            if self.path.startswith('/jobs/'):
                job = store.get(self.path.removeprefix('/jobs/'))
                return self.reply(200 if job else 404, job or {'error': 'job_not_found'})
            self.reply(404, {'error': 'not_found'})

        def do_POST(self):
            if not self.authorized():
                return self.reply(401, {'error': 'unauthorized'})
            if self.path != '/jobs':
                return self.reply(404, {'error': 'not_found'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 1 <= length <= 16384:
                    return self.reply(413, {'error': 'body_size'})
                payload = json.loads(self.rfile.read(length))
                job = store.create(payload, self.headers.get('Idempotency-Key'))
                self.reply(202, job)
            except FileExistsError:
                self.reply(409, {'error': 'idempotency_conflict'})
            except (ValueError, TypeError, UnicodeError):
                self.reply(400, {'error': 'invalid_request'})

    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--db', default='jobs.sqlite3')
    args = parser.parse_args()
    store = Store(args.db)
    server = make_server(args.host, args.port, store, os.environ.get('ADAPTER_TOKEN', ''))
    try:
        server.serve_forever()
    finally:
        server.server_close()
        store.close()


if __name__ == '__main__':
    main()
