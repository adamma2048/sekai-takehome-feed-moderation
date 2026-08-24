# Mock backend

Dependency-free Python 3. No install step.

```bash
python3 server.py                       # http://127.0.0.1:8787
python3 server.py --help                # all flags
python3 server.py --item-bytes 1048576  # 1 MB items, if you want a lighter loop while iterating
python3 server.py --fail-rate 1.0       # every moderation call fails — useful once
python3 server.py --total 40            # make the feed finite, to test the end-of-feed state
```

Android emulator reaches the host at `http://10.0.2.2:8787`.
iOS simulator can use `http://127.0.0.1:8787` directly.

Both platforms block cleartext HTTP by default. That is expected — configure the ATS /
network-security exception for localhost in your app, the same way you would for any local
mock, and say so in your README.

## Endpoints

| Route | What it gives you |
| --- | --- |
| `GET /feed?cursor=&limit=` | Paged feed, infinite by default |
| `GET /content/<itemId>` | ~5 MB HTML; exposes `sekaiPlay()` / `sekaiPause()`, autostarts nothing |
| `GET /creator/<creatorId>` | Profile for the creator page (name, bio, avatar, stats) |
| `GET /creator/<creatorId>/items` | That creator's works — **the same item ids the feed serves** |
| `GET /avatar/<creatorId>` | Small SVG, so the exercise never needs the public internet |
| `POST /moderation` | `202` after a delay; fails ~20% of the time on purpose |
| `GET /health` | `{"ok": true}` |

Creators repeat every 7 items (`creator_1` owns `item_0000`, `item_0007`, `item_0014`, …),
so one block has to take out several items — including items no page has fetched yet.
