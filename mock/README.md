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

Routes and field names mirror our production API; the data is fake. Two screens, four requests.

| Route | What it gives you |
| --- | --- |
| `GET /game/feed?limit=&refresh=` | The feed. **Bare array**, no envelope — that is what the real one does. `refresh` doubles as the page cursor. |
| `GET /content/<gameId>` | ~5 MB HTML (`game_url` on each item); exposes `sekaiPlay()` / `sekaiPause()`, autostarts nothing |
| `GET /api/user/info/v1/userProfile?user_id=` | Creator profile for the second-level page |
| `GET /api/game/list/v1/userGames?user_id=&page=&size=` | That creator's sekais, paged — **the same `game_id`s the feed serves** |
| `GET /avatar/<creatorId>` | Small SVG, so the exercise never needs the public internet |
| `POST /api/user/block/v1/blockUser` | `{"user_id": "..."}` — fails ~20% of the time on purpose |
| `POST /api/report/content/v1/reportContent` | `{"game_id": "...", "reason": "..."}` — same |
| `GET /health` | `{"ok": true}` |

Everything except `/game/feed` answers `{code, message, data}` with `code: 0` on success.
The wire is `snake_case`. Both quirks are inherited from the real API on purpose.

Creators repeat every 7 items (`creator_1` owns `game_0000`, `game_0007`, `game_0014`, …),
so one block has to take out several sekais — including ones no page has fetched yet.
