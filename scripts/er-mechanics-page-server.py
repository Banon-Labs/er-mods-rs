#!/usr/bin/env python3
"""Serve a directory of generated HTML pages with hot reload.

    python3 scripts/er-mechanics-page-server.py <dir> [--port 3000]

Every `.html` response gets a small script that polls `/__mtime?f=<path>` once a second and reloads
the page when the file's modification time changes, so rerunning a generator (for example
`er-mechanics-ash-table.py --out <dir>/page.html`) updates the open browser tab by itself.
Binds to 127.0.0.1 only.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import sys
import urllib.parse
from pathlib import Path

RELOAD = b"""<script>(function(){var p=location.pathname,m=null;setInterval(function(){
fetch('/__mtime?f='+encodeURIComponent(p),{cache:'no-store'}).then(function(r){return r.json()})
.then(function(j){if(m!==null&&j.mtime!==m)location.reload();m=j.mtime}).catch(function(){})},1000)})();</script>"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/__mtime":
            rel = urllib.parse.parse_qs(url.query).get("f", ["/"])[0].lstrip("/")
            target = Path(self.directory, rel).resolve()
            mtime = target.stat().st_mtime if target.is_file() and Path(self.directory).resolve() in target.parents else None
            body = json.dumps({"mtime": mtime}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        target = Path(self.translate_path(url.path))
        if target.suffix == ".html" and target.is_file():
            body = target.read_bytes()
            body = body.replace(b"</body>", RELOAD + b"</body>") if b"</body>" in body else body + RELOAD
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *args):
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir", type=Path)
    ap.add_argument("--port", type=int, default=3000)
    a = ap.parse_args()
    handler = functools.partial(Handler, directory=str(a.dir.resolve()))
    with http.server.ThreadingHTTPServer(("127.0.0.1", a.port), handler) as srv:
        print(f"serving {a.dir.resolve()} on http://127.0.0.1:{a.port}/", flush=True)
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
