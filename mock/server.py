#!/usr/bin/env python3
"""Mock backend for the Sekai feed take-home. Python 3 only, no dependencies.

    python3 mock/server.py                 # http://127.0.0.1:8787
    python3 mock/server.py --help          # latency, page size, payload size, fail rate

Endpoints
    GET  /feed?cursor=&limit=      paged, infinite by default
    GET  /content/<id>             ~5 MB HTML, exposes sekaiPlay() / sekaiPause()
    POST /moderation               202 after a delay, fails ~20% of the time

Android emulator reaches the host at http://10.0.2.2:8787 .
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# Creators repeat across the feed on purpose: blocking one has to take out several items,
# including items that have not been fetched yet.
CREATORS = [
    ("creator_1", "mejikoOV_80"),
    ("creator_2", "Dark Prince"),
    ("creator_3", "yaya_room"),
    ("creator_4", "gagg_labs"),
    ("creator_5", "bounce_kid"),
    ("creator_6", "origami_red"),
    ("creator_7", "web_weaver"),
]

TITLES = [
    "Lo-Fi Vibe Mixer", "Giggle Pop", "Yaya's Room", "Gagg Box",
    "Bouncing Red Ball", "Origami Bounce", "Web Weaver", "24 Card Arcade",
]

# The page must not start on its own: the app decides what plays. It also renders its own
# state, so a screen recording shows whether the app got the contract right.
CONTENT_TEMPLATE = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<style>
  html, body {{ margin: 0; height: 100%; background: {bg}; overflow: hidden;
                font-family: -apple-system, Roboto, sans-serif; color: #fff; }}
  .wrap {{ height: 100%; display: flex; flex-direction: column;
           align-items: center; justify-content: center; gap: 18px; }}
  .dot  {{ width: 96px; height: 96px; border-radius: 50%; background: {fg};
           animation: bob 1.1s ease-in-out infinite; animation-play-state: paused; }}
  .dot.playing {{ animation-play-state: running; }}
  @keyframes bob {{ 0%,100% {{ transform: translateY(-28px) scale(1); }}
                    50%      {{ transform: translateY(28px) scale(.86); }} }}
  h1 {{ font-size: 22px; margin: 0; }}
  #state {{ font-size: 15px; letter-spacing: .18em; opacity: .85; }}
  #ticks {{ font-size: 13px; opacity: .55; }}
</style>
</head>
<body>
  <div class="wrap">
    <div class="dot" id="dot"></div>
    <h1>{title}</h1>
    <div id="state">PAUSED</div>
    <div id="ticks">frames played: 0</div>
    <div id="ticks">{item_id} &middot; {creator_name}</div>
  </div>
<script>
  var playing = false, frames = 0, timer = null;
  var dot = document.getElementById('dot');
  var state = document.getElementById('state');
  var ticks = document.getElementById('ticks');

  // The two functions the app is expected to drive. Nothing here autostarts:
  // if the app never calls sekaiPlay(), this item stays PAUSED forever.
  window.sekaiPlay = function () {{
    if (playing) return;
    playing = true;
    dot.classList.add('playing');
    state.textContent = 'PLAYING';
    timer = setInterval(function () {{
      frames += 1;
      ticks.textContent = 'frames played: ' + frames;
    }}, 100);
  }};

  window.sekaiPause = function () {{
    if (!playing) return;
    playing = false;
    dot.classList.remove('playing');
    state.textContent = 'PAUSED';
    clearInterval(timer);
    timer = null;
  }};
</script>
<!-- Payload padding below. Real items carry this much weight; the exercise is about what
     you keep alive, so the mock does not pretend items are cheap.
{padding}
-->
</body>
</html>
"""

PALETTE = [
    ("#1b1b2f", "#e94560"), ("#12232e", "#00b7c2"), ("#2d132c", "#ee4540"),
    ("#0f3057", "#00a8cc"), ("#22223b", "#f2e9e4"), ("#1a1a2e", "#f9ed69"),
    ("#16213e", "#53d8fb"),
]


def _item(index: int, content_origin: str) -> dict:
    creator_id, creator_name = CREATORS[index % len(CREATORS)]
    return {
        "id": f"item_{index:04d}",
        "creatorId": creator_id,
        "creatorName": creator_name,
        "title": TITLES[index % len(TITLES)],
        "contentUrl": f"{content_origin}/content/item_{index:04d}",
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    args: argparse.Namespace  # set in main()

    # ── plumbing ────────────────────────────────────────────────────────────────
    def log_message(self, fmt: str, *a) -> None:  # quieter, one line per request
        print(f"  {self.command} {self.path} → {fmt % a}")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

    # ── routes ──────────────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        url = urlparse(self.path)
        if url.path == "/feed":
            return self._feed(parse_qs(url.query))
        if url.path.startswith("/content/"):
            return self._content(url.path.rsplit("/", 1)[-1])
        if url.path == "/health":
            return self._json(200, {"ok": True})
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/moderation":
            return self._json(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "invalid json"})

        time.sleep(self.args.latency_ms / 1000.0)
        # Deliberate: the optimistic local change has to survive a server that says no.
        if random.random() < self.args.fail_rate:
            return self._json(500, {"error": "moderation service unavailable"})
        return self._json(202, {"accepted": True, "echo": body})

    def _feed(self, query: dict) -> None:
        cursor = int((query.get("cursor") or ["0"])[0] or 0)
        limit = int((query.get("limit") or [str(self.args.page_size)])[0])
        limit = max(1, min(limit, 50))
        time.sleep(self.args.latency_ms / 1000.0)

        origin = f"http://{self.headers.get('Host', f'127.0.0.1:{self.args.port}')}"
        items = [_item(i, origin) for i in range(cursor, cursor + limit)]
        end = self.args.total is not None and cursor + limit >= self.args.total
        self._json(200, {
            "items": items,
            "nextCursor": None if end else str(cursor + limit),
        })

    def _content(self, item_id: str) -> None:
        try:
            index = int(item_id.rsplit("_", 1)[-1])
        except ValueError:
            return self._json(404, {"error": "unknown item"})
        creator_id, creator_name = CREATORS[index % len(CREATORS)]
        bg, fg = PALETTE[index % len(PALETTE)]
        head = CONTENT_TEMPLATE.format(
            bg=bg, fg=fg, title=TITLES[index % len(TITLES)],
            item_id=item_id, creator_name=creator_name, padding="",
        )
        pad = max(0, self.args.item_bytes - len(head.encode()))
        # A repeating, compressible-but-present block. Not random bytes: this stands in for
        # real page weight (fonts, sprite sheets, an engine), which is not noise either.
        padding = ("<!-- sekai payload padding " + "=" * 80 + " -->\n") * (pad // 110 + 1)
        html = CONTENT_TEMPLATE.format(
            bg=bg, fg=fg, title=TITLES[index % len(TITLES)],
            item_id=item_id, creator_name=creator_name, padding=padding,
        )
        self._send(200, html.encode(), "text/html; charset=utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--host", default="0.0.0.0",
                   help="0.0.0.0 so an emulator/simulator can reach it (default)")
    p.add_argument("--page-size", type=int, default=6, help="items per /feed page")
    p.add_argument("--latency-ms", type=int, default=350,
                   help="artificial latency on /feed and /moderation")
    p.add_argument("--item-bytes", type=int, default=5 * 1024 * 1024,
                   help="approximate size of each /content page (default ~5 MB)")
    p.add_argument("--fail-rate", type=float, default=0.2,
                   help="fraction of /moderation calls that return 500")
    p.add_argument("--total", type=int, default=None,
                   help="stop the feed after N items (default: infinite)")
    p.add_argument("--seed", type=int, default=None, help="seed the failure RNG")
    args = p.parse_args()
    if args.seed is not None:
        random.seed(args.seed)

    Handler.args = args
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"mock feed on http://127.0.0.1:{args.port}  "
          f"(emulator: http://10.0.2.2:{args.port})")
    print(f"  page size {args.page_size} · latency {args.latency_ms}ms · "
          f"item ~{args.item_bytes // 1024 // 1024}MB · moderation fail rate {args.fail_rate}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
