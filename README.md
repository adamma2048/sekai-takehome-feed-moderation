# Sekai take-home — feed moderation (block & report)

Build a small app around one screen we actually ship: an infinite, snap-scrolling feed of
WebView-hosted content, with creator blocking and content reporting.

**Pick one platform — Android or iOS.** Do not build both; we would rather see one platform
done with judgment than two done in a hurry.

**Time budget: ~6 focused hours.** If you run out of time, ship less scope and write down
what you cut and why. A short honest README beats a large unfinished app.

---

## The scenario

Sekai's main surface is a vertical feed. Each item — we call one a **sekai** — is a small
interactive experience rendered in a **WebView**, not a video and not a native view. Two facts
about that content shape everything else:

- Each item is **~5 MB**. You cannot keep them all alive.
- Content only runs when the app tells it to. The page exposes `window.sekaiPlay()` and
  `window.sekaiPause()`; nothing plays on its own.

Users must be able to get abusive content and abusive creators out of their feed
**immediately** — this is an App Store / Play compliance surface, not a nice-to-have. When a
user blocks a creator, that creator's work has to disappear everywhere in the app, not just
from the card that happened to be on screen.

---

## What to build

### 1. The feed

- Vertical, one item per screen, **snaps** to an item (no resting between two items).
- **Infinite scroll**: when the user approaches the end, fetch the next page and append.
- Content is rendered in a WebView from the mock backend (see below). Each page is ~5 MB.
- **Exactly one item plays at a time**: when the feed settles on item *n*, call
  `sekaiPlay()` on it and `sekaiPause()` on whatever was playing before. Scrolling past an
  item must not leave it running.

### 2. Moderation actions

From the feed item, the user can **report the content** or **block its creator**.

- The item disappears **immediately** — no waiting for the network round trip.
- A toast (or platform equivalent) confirms what happened.
- Scrolling **back up must not show it again**. Neither must a later page fetch that happens
  to include it.

### 3. Creator page

- Tapping the creator on a feed item opens their page: avatar, name, and **their sekais,
  paged** (`userProfile` + `userGames` — see below).
- The page has a **`⋯` button in the top-right**; it opens a panel containing **Block**.
- After blocking, **every** sekai by that creator is invisible — in the feed, on this page,
  and anywhere else you built.

---

## Hard requirements

These are the three things we will look at first.

1. **Data flows through a stream, not through mutable shared state.**
   Kotlin `Flow` / `StateFlow` on Android; Combine or `AsyncSequence` on iOS. The visible
   feed should be *derived* from its inputs, not patched by hand at each call site.

2. **Scrolling does not drop frames.** With 5 MB items, this is the requirement with teeth.
   Tell us **how you measured it** — a frame-timing trace, `FrameMetrics`, Instruments, an
   overlay, whatever you trust. "It felt smooth" is not a measurement.

3. **Autoplay follows the settled item**, per the play/pause contract above.

---

## Mock backend

`mock/server.py` is a dependency-free Python 3 server:

```bash
python3 mock/server.py            # http://127.0.0.1:8787
python3 mock/server.py --help     # page size, latency, payload size, failure rate
```

Android emulator reaches the host at `http://10.0.2.2:8787`.

**The routes and field names mirror our production API; the data behind them is fake.** That
is on purpose — the shapes you will meet on the job are the shapes you get here, including
the two inconsistencies we live with: the feed returns a **bare array**, everything else is
wrapped in `{code, message, data}`, and the wire is `snake_case`.

Two screens, four requests.

### The feed — `GET /game/feed?limit=<n>&refresh=<n>`

`refresh` doubles as the page cursor here: `refresh=1` gives the next batch.

```json
[
  {
    "game_id": "game_0000",
    "title": "Lo-Fi Vibe Mixer",
    "game_url": "http://127.0.0.1:8787/content/game_0000",
    "cover_url": "http://127.0.0.1:8787/avatar/creator_1",
    "creator_id": "creator_1",
    "creator_name": "mejikoOV_80",
    "like_count": 7
  }
]
```

### The creator page — `GET /api/user/info/v1/userProfile?user_id=<id>`

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "user_id": "creator_1",
    "nick_name": "mejikoOV_80",
    "avatar": "http://127.0.0.1:8787/avatar/creator_1",
    "bio": "lo-fi loops and small machines",
    "following_count": 10,
    "follower_count": 60,
    "like_count": 440
  }
}
```

### The creator's sekais — `GET /api/game/list/v1/userGames?user_id=<id>&page=<n>&size=<n>`

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "list": [
      {
        "game_id": "game_0000",
        "title": "Lo-Fi Vibe Mixer",
        "game_url": "http://127.0.0.1:8787/content/game_0000",
        "cover_url": "http://127.0.0.1:8787/avatar/creator_1",
        "creator_id": "creator_1",
        "creator_name": "mejikoOV_80",
        "like_count": 7
      }
    ],
    "page": 0,
    "size": 2,
    "has_more": true
  }
}
```

**These are the same `game_id`s the feed serves** — `creator_1` owns `game_0000`,
`game_0007`, `game_0014`, … in both places. Creators repeat every 7 items, so one block has
to take out several sekais, including ones no page has fetched yet. After blocking, this
screen is where a feed-only filter falls over.

### The content — `GET /content/<gameId>`

An HTML page of roughly **5 MB** (the `game_url` on each item). It defines:

- `window.sekaiPlay()` — starts the animation, shows a visible `PLAYING` state.
- `window.sekaiPause()` — stops it.

It **does not** start on its own, and it renders its own play state plus a frame counter, so a
screen recording shows whether your app got the contract right.

### Moderation

```
POST /api/user/block/v1/blockUser          {"user_id": "creator_1"}
POST /api/report/content/v1/reportContent  {"game_id": "game_0042", "reason": "spam"}
```

Both answer `{"code": 0}` after a delay — and **fail ~20% of the time** with a non-zero
`code` (`--fail-rate` to change it). That failure is deliberate: decide what the user should
see when the optimistic local change cannot be confirmed, and defend the choice in your README.

### Avatars — `GET /avatar/<creatorId>`

A small SVG served by the mock, so nothing in this exercise reaches the public internet.

---

## What we are looking at

| Area | What earns points |
| --- | --- |
| **Correctness of the hidden set** | Blocking a creator hides *all* their items, including ones already fetched and ones that arrive in a later page. Reported items stay gone across a scroll back and an app restart. |
| **Where that logic lives** | One source of truth that the UI derives from, versus removal code sprinkled at each call site. We care about this more than about which library you used. |
| **Memory under 5 MB items** | What is alive while scrolling, and why that number. Show us. |
| **Frame timing** | Your measurement, your numbers, and what you changed because of them. |
| **The play/pause contract** | Exactly one playing item; nothing keeps running off screen. |
| **Tests** | A few tests on the parts that carry the rules. We do not expect UI test coverage. |
| **Your README** | Trade-offs, what you cut, what you would do next, what you are unsure about. |

We would rather see a small, honest, well-argued submission than a large one that hides its
soft spots.

---

## Out of scope

- Real authentication, real backend, real reporting pipeline.
- Design polish. Use platform defaults; we are not scoring visuals.
- Both platforms. Pick one.
- Localisation, accessibility beyond a sensible content description / label on the `⋯` button.

---

## Submitting

Send us a repository (or a zip) containing:

1. The app source.
2. A **short README** covering: how to run it, what you cut, your frame-timing measurement
   (numbers, not adjectives), and what happens when the moderation call fails.
3. A **screen recording**, roughly a minute: scroll a few items, block a creator, scroll back
   past where their item was, and open a creator page and block from the `⋯` panel.

The recording matters. Several of the requirements above (immediate removal, no reappearance,
one item playing) are only visible in motion.

---

## Notes / hints

Three things we have seen candidates trip over — flagged deliberately, not as gotchas:

- **"Remove from the list" is not the same as "hide".** A removal that mutates the visible
  list gets undone by the next page fetch, which happily re-adds the same item.
- **The 5 MB is the whole point of the frame-rate requirement.** How many WebViews you keep
  alive, and when you create and destroy them, is the interesting decision on this exercise.
- **Settling is not the same as "the item is on screen".** Fast scrolling passes over many
  items; only the one the feed came to rest on should play.
