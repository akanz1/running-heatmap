"""Local admin server.

Usage:
    uv run python -m heatmap.admin_server [--port 8001]

Endpoints:
    GET  /                    → serves admin.html
    GET  /api/activities      → all activities (JSON)
    POST /api/exclude         → toggle exclude state
                                body: {"source": "strava"|"intervals",
                                       "ids": [...], "excluded": true|false}
    POST /api/reimport        → evict + re-download an intervals activity
                                body: {"id": "iXXXXXXXX"}

After clicking actions in the UI, run `make run` to rebuild the heatmap so
changes take effect.
"""

from __future__ import annotations

import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from heatmap.admin import list_activities
from heatmap.admin import reimport_intervals
from heatmap.admin import set_excluded

log = logging.getLogger(__name__)

_ADMIN_HTML = Path(__file__).resolve().parent / "admin.html"


def _make_handler(config: Any) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
            log.info("%s - %s", self.address_string(), fmt % args)

        def _send_json(self, payload: object, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                return json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return {}

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html", "/admin.html"):
                if not _ADMIN_HTML.exists():
                    self.send_error(500, "admin.html missing")
                    return
                body = _ADMIN_HTML.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path == "/api/activities":
                self._send_json(list_activities(config))
                return

            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            if self.path == "/api/exclude":
                body = self._read_json()
                source = body.get("source")
                ids = body.get("ids") or []
                excluded = bool(body.get("excluded"))
                if source not in ("strava", "intervals") or not isinstance(ids, list):
                    self._send_json({"ok": False, "error": "bad payload"}, status=400)
                    return
                overrides = set_excluded(config, source, [str(i) for i in ids], excluded)
                self._send_json({"ok": True, "overrides": overrides})
                return

            if self.path == "/api/reimport":
                body = self._read_json()
                aid = body.get("id")
                if not aid:
                    self._send_json({"ok": False, "error": "missing id"}, status=400)
                    return
                result = reimport_intervals(config, str(aid))
                self._send_json(result)
                return

            self.send_error(404)

    return Handler


def main() -> None:
    from dotenv import load_dotenv

    from heatmap import configure_logging

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()

    load_dotenv()
    configure_logging()
    from main import config  # late import: depends on env

    handler = _make_handler(config)
    log.info("Admin server: http://%s:%d/  (Ctrl-C to stop)", args.bind, args.port)
    with ThreadingHTTPServer((args.bind, args.port), handler) as srv:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            log.info("Stopped.")


if __name__ == "__main__":
    main()
