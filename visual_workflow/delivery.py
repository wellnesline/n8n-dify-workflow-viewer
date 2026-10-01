"""Allowlisted read-only handoff server: fixed static files only, GET only (loopback by default)."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import json

FILES = {'': 'index.html', 'index.html': 'index.html',
         'n8n-project-a.json': 'n8n-project-a.json', 'n8n-project-b.json': 'n8n-project-b.json',
         'dify-project-a.json': 'dify-project-a.json', 'dify-project-b.json': 'dify-project-b.json'}
PREFIX = '/poc/'
CONTENT_TYPES = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json'}


def make_server(root, host='127.0.0.1', port=8090, viewer_root=None):
    root = Path(root).resolve()
    viewer_root = Path(viewer_root).resolve() if viewer_root else None
    viewer_files = {}
    if viewer_root:
        catalog = json.loads((viewer_root / 'catalog.json').read_text(encoding='utf-8'))
        viewer_files = {'/viewer/': 'index.html',
                        **{'/viewer/' + name: name for name in ('app.js', 'style.css', 'catalog.json')}}
        for item in catalog:
            slug = item['slug']
            if not re.fullmatch('[a-z][a-z0-9-]{0,63}', slug):
                raise ValueError('Invalid viewer catalog')
            viewer_files['/viewer/' + slug + '/'] = 'index.html'
            viewer_files['/viewer/snapshots/' + slug + '.json'] = 'snapshots/' + slug + '.json'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_file(self, file, content_type, csp=None):
            raw = file.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if csp:
                self.send_header('Content-Security-Policy', csp)
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            path = self.path.split('?', 1)[0]
            if viewer_root and path in viewer_files:
                file = viewer_root / viewer_files[path]
                if not file.is_file() or not file.resolve().is_relative_to(viewer_root):
                    self.send_error(404)
                    return
                self.send_file(file, CONTENT_TYPES[file.suffix],
                               "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                return
            suffix = path[len(PREFIX):] if path.startswith(PREFIX) else None
            filename = FILES.get(suffix)
            if filename is None or not (root / filename).is_file():
                self.send_error(404)
                return
            self.send_file(root / filename, CONTENT_TYPES[Path(filename).suffix])

        def do_POST(self):
            self.send_error(405)

    return ThreadingHTTPServer((host, port), Handler)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--port', type=int, default=8090)
    parser.add_argument('--viewer-root')
    args = parser.parse_args()
    make_server(args.root, port=args.port, viewer_root=args.viewer_root).serve_forever()