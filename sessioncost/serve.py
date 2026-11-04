"""`sessioncost serve`: a live report page on this computer only. The page checks every few seconds whether the chat's
log changed and reloads itself; each load rebuilds the report
from the newest chat's log, so a new answer shows without running /sessioncost again.

Safety: it listens on 127.0.0.1 only; the address holds a random secret, so other pages in the browser cannot drive
it (any request without it gets 403); a request naming another host is refused (no DNS rebinding); it writes only the
reports folder, as /sessioncost does; it sends nothing anywhere. Ctrl+C stops it.
"""
from __future__ import annotations

import re
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import cli, readers

CHECK_S = 5          # the page asks this often whether the chat's log changed, and reloads itself when it did
BAR = ('<div id="sc-live" style="position:fixed;top:10px;right:12px;z-index:9999;font:13px system-ui,sans-serif;'
       'background:#1f6f78;color:#fff;border-radius:6px;padding:6px 10px;box-shadow:0 1px 4px #0004">'
       'Live · updated {when}</div>'
       '<script>(function(){{var v={version};setInterval(function(){{fetch("version",{{cache:"no-store"}})'
       '.then(function(r){{return r.ok?r.text():v}}).then(function(t){{if(t!==v)location.reload()}})'
       '.catch(function(){{}})}},{ms})}})();</script>')


def log_version(args) -> str:
    """Changes whenever the newest chat's log does (a new answer, or a new chat)."""
    path = cli.find_session(args, "last")
    try:
        st = path.stat() if path else None
    except OSError:
        st = None
    return f"{path}|{st.st_mtime_ns}|{st.st_size}" if st else "none"


def add_bar(html: str, when: str, version: str = "") -> str:
    import json
    bar = BAR.format(when=when, version=json.dumps(version).replace("<", "\\u003c"), ms=CHECK_S * 1000)
    m = re.search(r"<body[^>]*>", html, re.I)
    return html[:m.end()] + bar + html[m.end():] if m else bar + html


def make_server(args, port: int = 0, token: str | None = None) -> tuple[ThreadingHTTPServer, str]:
    """The server (not yet serving) and its address with the secret."""
    token = token or secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):          # quiet: the terminal shows only the address
            pass

        def send(self, code: int, body: str, kind: str = "text/plain; charset=utf-8") -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")       # the secret never leaves in a link's Referer
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            host = (self.headers.get("Host") or "").lower()
            if host not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"):
                return self.send(403, "Forbidden")
            parts = self.path.split("?", 1)[0].split("/")
            if len(parts) < 3 or not secrets.compare_digest(parts[1], token):
                return self.send(403, "Forbidden")
            page = "/".join(parts[2:])
            if page == cli.GUARD_PAGE:                               # the report's "full guard report" link
                f = cli.REPORTS / cli.GUARD_PAGE
                return self.send(200, f.read_text(encoding="utf-8"), "text/html; charset=utf-8") if f.is_file() \
                    else self.send(404, "Not found")
            if page == "version":                                   # the page's own check, a few bytes
                return self.send(200, log_version(args))
            if page:
                return self.send(404, "Not found")
            version = log_version(args)
            path = cli.find_session(args, "last")
            if path is None:
                return self.send(404, "No chat found for this project yet.")
            try:
                a, out = cli.build(path, args)
            except (readers.NotSupported, ValueError, OSError) as e:
                return self.send(500, f"sessioncost: {e}")
            import time
            self.send(200, add_bar(out.read_text(encoding="utf-8"), time.strftime("%H:%M:%S"), version),
                      "text/html; charset=utf-8")

        def do_POST(self):
            self.send(405, "Method not allowed")

        do_PUT = do_DELETE = do_PATCH = do_POST

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return srv, f"http://127.0.0.1:{srv.server_port}/{token}/"


def run(args) -> int:
    args.json = False
    try:
        srv, url = make_server(args, args.port)
    except OSError as e:
        print(f"sessioncost: cannot listen on port {args.port}: {e}")
        return 2
    print(f"Live report (this computer only): {url}\nIt updates by itself after each answer. Ctrl+C stops it.")
    if not args.no_open:
        threading.Timer(0.3, webbrowser.open, (url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    print("sessioncost: live report stopped.")
    return 0
