"""Static file server for the heatmap viewer.

Differences from ``python -m http.server``:
  * Missing ``/tiles/*.png`` requests return a 1x1 transparent PNG with
    status 200 instead of 404. The sparse pyramid leaves many tiles
    inside the data bounding box unwritten; the browser handles those
    fine via the layer's errorTileUrl, but the 404 noise floods the log.
  * ``BrokenPipeError`` / ``ConnectionResetError`` are swallowed when the
    browser cancels in-flight requests during pan/zoom.
  * Access log drops 4xx/5xx lines.
"""

from __future__ import annotations

import argparse
import contextlib
import struct
import sys
import zlib
from functools import partial
from http.server import SimpleHTTPRequestHandler
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import BinaryIO


def _make_transparent_png() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(t: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(t + data)
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    # 1-byte filter (0) + 4 bytes transparent RGBA, then zlib-compressed.
    idat = zlib.compress(b"\x00\x00\x00\x00\x00")
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


_TRANSPARENT_PNG = _make_transparent_png()


class HeatmapHandler(SimpleHTTPRequestHandler):
    def send_head(self) -> BytesIO | BinaryIO | None:
        # Root has no index.html (the viewer is heatmap.html), so the default
        # handler would render a directory listing. Serve the viewer instead.
        if self.path in ("/", "/index.html"):
            self.path = "/heatmap.html"
        if self.path.startswith("/tiles/") and self.path.endswith(".png"):
            disk_path = Path(self.translate_path(self.path))
            if not disk_path.is_file():
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(_TRANSPARENT_PNG)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                return BytesIO(_TRANSPARENT_PNG)
        return super().send_head()

    def handle(self) -> None:
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            super().handle()

    def log_message(self, fmt, *args) -> None:
        try:
            status = int(args[1])
        except (IndexError, ValueError, TypeError):
            status = 0
        if 400 <= status < 600:
            return
        super().log_message(fmt, *args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--directory", default="outputs")
    args = parser.parse_args()

    handler = partial(HeatmapHandler, directory=args.directory)
    server = ThreadingHTTPServer(("", args.port), handler)
    print(
        f"Serving {args.directory}/ on http://localhost:{args.port}",
        file=sys.stderr,
    )
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
