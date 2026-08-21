#!/usr/bin/env python3
"""
Static server for output/ with an /immich/* proxy that injects the Immich API
key server-side, so the key never appears in served HTML.

Usage: python3 serve.py [port]   (default 8082)
"""
import json
import os
import re
import sys
import urllib.request
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')

# Only asset thumbnails/originals may be proxied — no album/user enumeration.
ASSET_RE = re.compile(r'^/api/assets/[0-9a-f-]{36}/(thumbnail|original)$')

with open(os.path.join(BASE_DIR, 'config.json')) as f:
    hikes = json.load(f)['hikes']
# ponytail: uses first hike's creds; all hikes share one Immich server today —
# if that changes, route by album or per-hike prefix instead.
HIKE = next(iter(hikes.values()))
IMMICH = HIKE['immich_base_url'].rstrip('/')
API_KEY = HIKE['immich_api_key']


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=OUTPUT_DIR, **kwargs)

    def do_GET(self):
        if not self.path.startswith('/immich/'):
            super().do_GET()
            return
        path = self.path[len('/immich'):].split('?')[0]
        if not ASSET_RE.match(path):
            self.send_error(404)
            return
        req = urllib.request.Request(IMMICH + path, headers={'x-api-key': API_KEY})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
                ctype = resp.headers.get('Content-Type', 'application/octet-stream')
        except Exception:
            self.send_error(502)
            return
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'max-age=86400')
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8082
    print(f'Serving {OUTPUT_DIR} on http://0.0.0.0:{port} (/immich/ proxied, key stays server-side)')
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
