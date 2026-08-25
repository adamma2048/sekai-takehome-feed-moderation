#!/usr/bin/env python3
"""Mock backend for the Sekai feed take-home. Python 3 only, no dependencies.

    python3 mock/server.py                 # http://127.0.0.1:8787
    python3 mock/server.py --help          # latency, page size, payload size, fail rate

Endpoints — paths and fields copy the shape of our production API; the content is fake:
    GET  /game/feed?limit=&refresh=              bare array (the live feed has no envelope)
    GET  /content/<gameId>                       ~5 MB HTML, exposes sekaiPlay()/sekaiPause()
    GET  /api/user/info/v1/userProfile?user_id=  creator profile
    GET  /api/game/list/v1/userGames?user_id=&page=&size=
                                                 that creator's sekais, paged; game_id shares the feed's source
    GET  /avatar/<creatorId>                     tiny SVG, so the whole exercise stays off the public net
    POST /api/user/block/v1/blockUser            {code,message,data}; ~20% fail on purpose
    POST /api/report/content/v1/reportContent    same as above

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

BIOS = {
    "creator_1": "lo-fi loops and small machines",
    "creator_2": "dark ambient, mostly at 3am",
    "creator_3": "cozy rooms for tired people",
    "creator_4": "prank boxes. no refunds.",
    "creator_5": "physics toys",
    "creator_6": "paper, folded until it bounces",
    "creator_7": "webs, weavers, and the flies between",
}

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
    """One sekai. Field names copy our live feed DTO (snake_case), keeping only what this exercise needs."""
    creator_id, creator_name = CREATORS[index % len(CREATORS)]
    return {
        "game_id": f"game_{index:04d}",
        "title": TITLES[index % len(TITLES)],
        "game_url": f"{content_origin}/content/game_{index:04d}",
        "cover_url": f"{content_origin}/avatar/{creator_id}",
        "creator_id": creator_id,
        "creator_name": creator_name,
        "like_count": 7 + (index * 13) % 400,
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
        query = parse_qs(url.query)
        # Paths follow the shape of our production API; only the content is fake:
        #   the feed is `game/feed` and returns a **bare array** (no {code,data} envelope);
        #   everything else is `api/<domain>/<area>/v1/<action>` and returns {code,message,data}.
        if url.path == "/game/feed":
            return self._feed(query)
        if url.path == "/api/user/info/v1/userProfile":
            return self._creator(query)
        if url.path == "/api/game/list/v1/userGames":
            return self._creator_items(query)
        if url.path.startswith("/content/"):
            return self._content(url.path.rsplit("/", 1)[-1])
        if url.path.startswith("/avatar/"):
            return self._avatar(url.path.rsplit("/", 1)[-1])
        if url.path == "/health":
            return self._json(200, {"ok": True})
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        # Same shape as production: blocking lives in the user domain, reporting in the report
        # domain. Both return {code,message,data}.
        if path not in ("/api/user/block/v1/blockUser",
                        "/api/report/content/v1/reportContent"):
            return self._json(404, {"code": 40400, "message": "not found", "data": None})
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "invalid json"})

        time.sleep(self.args.latency_ms / 1000.0)
        # Deliberate: an optimistic update has to survive the server saying no.
        if random.random() < self.args.fail_rate:
            return self._json(200, {"code": 50000, "message": "service unavailable",
                                    "data": None})
        return self._json(200, {"code": 0, "message": "ok", "data": {"echo": body}})

    def _feed(self, query: dict) -> None:
        """`GET /game/feed?limit=&refresh=` — same shape as production: a **bare array**, no envelope.

        In production `refresh` means "which refresh is this"; here it doubles as a cursor,
        so refresh=n serves page n. Without it the server keeps serving from the top, which
        matches how pull-to-refresh feels in production.
        """
        limit = int((query.get("limit") or [str(self.args.page_size)])[0])
        limit = max(1, min(limit, 50))
        refresh = int((query.get("refresh") or ["0"])[0] or 0)
        time.sleep(self.args.latency_ms / 1000.0)

        origin = f"http://{self.headers.get('Host', f'127.0.0.1:{self.args.port}')}"
        start = refresh * limit
        indices = range(start, start + limit)
        if self.args.total is not None:
            indices = [i for i in indices if i < self.args.total]
        self._send(200,
                   json.dumps([_item(i, origin) for i in indices]).encode(),
                   "application/json")

    # ── creator secondary page ──────────────────────────────────────────────────
    #
    # A creator's works come from the same rule the feed uses (creator_k owns every item
    # whose index % len(CREATORS) hits it), so the item ids on the creator page are **the
    # same batch** as in the feed. After a block both places must go away together — an
    # implementation that filters only on the feed side is exposed the moment this page opens.
    def _creator_ids(self) -> dict:
        return {cid: i for i, (cid, _name) in enumerate(CREATORS)}

    def _creator(self, query: dict) -> None:
        """`GET /api/user/info/v1/userProfile?user_id=` — envelope shape {code,message,data}."""
        creator_id = (query.get("user_id") or [""])[0]
        offsets = self._creator_ids()
        if creator_id not in offsets:
            return self._json(200, {"code": 40400, "message": "user not found", "data": None})
        index = offsets[creator_id]
        _cid, name = CREATORS[index]
        origin = f"http://{self.headers.get('Host', f'127.0.0.1:{self.args.port}')}"
        time.sleep(self.args.latency_ms / 1000.0)
        self._json(200, {"code": 0, "message": "ok", "data": {
            "user_id": creator_id,
            "nick_name": name,
            "avatar": f"{origin}/avatar/{creator_id}",
            "bio": BIOS.get(creator_id, ""),
            # Stable fake numbers: identical for a given creator every time, so screenshots
            # and screen recordings stay comparable.
            "following_count": 10 + index * 7,
            "follower_count": 60 + index * 23,
            "like_count": 440 + index * 137,
        }})

    def _creator_items(self, query: dict) -> None:
        """`GET /api/game/list/v1/userGames?user_id=&page=&size=` — that creator's sekais, paged.

        The game_ids returned share **exactly** the feed's source (creator_k owns every 7th
        item). An implementation that filters only on the feed side gives itself away as soon
        as this page opens — which is precisely what "no works visible after a block" tests.
        """
        creator_id = (query.get("user_id") or [""])[0]
        offsets = self._creator_ids()
        if creator_id not in offsets:
            return self._json(200, {"code": 40400, "message": "user not found", "data": None})
        page = int((query.get("page") or ["0"])[0] or 0)
        size = int((query.get("size") or [str(self.args.page_size)])[0])
        size = max(1, min(size, 50))
        time.sleep(self.args.latency_ms / 1000.0)

        origin = f"http://{self.headers.get('Host', f'127.0.0.1:{self.args.port}')}"
        step = len(CREATORS)
        first = offsets[creator_id]
        indices = [first + step * (page * size + n) for n in range(size)]
        if self.args.total is not None:
            indices = [i for i in indices if i < self.args.total]
        items = [_item(i, origin) for i in indices]
        self._json(200, {"code": 0, "message": "ok", "data": {
            "list": items,
            "page": page,
            "size": size,
            "has_more": bool(items) and (self.args.total is None or
                                         indices[-1] + step < self.args.total),
        }})

    def _avatar(self, creator_id: str) -> None:
        offsets = self._creator_ids()
        if creator_id not in offsets:
            return self._json(404, {"error": "unknown creator"})
        index = offsets[creator_id]
        bg, fg = PALETTE[index % len(PALETTE)]
        initial = CREATORS[index][1][0].upper()
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240">'
            f'<rect width="240" height="240" fill="{bg}"/>'
            f'<circle cx="120" cy="120" r="96" fill="{fg}"/>'
            f'<text x="120" y="152" font-size="96" font-family="sans-serif" '
            f'text-anchor="middle" fill="{bg}">{initial}</text></svg>'
        )
        self._send(200, svg.encode(), "image/svg+xml")

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
