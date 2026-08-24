# Sekai take-home — feed moderation (block & report)

Build a small app around one screen we actually ship: an infinite, snap-scrolling feed of
WebView-hosted content, with creator blocking and content reporting.

**Pick one platform — Android or iOS.** Do not build both; we would rather see one platform
done with judgment than two done in a hurry.

**Time budget: ~6 focused hours.** If you run out of time, ship less scope and write down
what you cut and why. A short honest README beats a large unfinished app.

---

## The scenario

Sekai's main surface is a vertical feed. Each item is a small interactive experience rendered
in a **WebView**, not a video and not a native view. Two facts about that content shape
everything else:

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

- Tapping the creator on a feed item opens their page.
- The page has a **`⋯` button in the top-right**; it opens a panel containing **Block**.
- After blocking, **every** work by that creator is invisible — in the feed, in the creator
  page, and in anything else you built.

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

`mock/server.py` is a dependency-free Python 3 server. Start it with:

```bash
python3 mock/server.py            # listens on http://127.0.0.1:8787
```

Use `--help` for flags: page size, artificial latency, failure injection, payload size.
Android emulator reaches the host at `http://10.0.2.2:8787`.

### `GET /feed?cursor=<cursor>&limit=<n>`

```json
{
  "items": [
    {
      "id": "item_0042",
      "creatorId": "creator_1",
      "creatorName": "mejikoOV_80",
      "title": "Yaya's Room",
      "contentUrl": "http://127.0.0.1:8787/content/item_0042"
    }
  ],
  "nextCursor": "43"
}
```

`nextCursor` is `null` when the mock decides the feed has ended (it does not, by default —
it is infinite on purpose).

### `GET /content/<id>`

An HTML page of roughly **5 MB**. It defines:

- `window.sekaiPlay()` — starts the animation, sets a visible "PLAYING" state.
- `window.sekaiPause()` — stops it.
- It **does not** start on its own, and it reports its state in the page so you can see from a
  screen recording whether your app got the contract right.

### `GET /creator/<creatorId>`

```json
{
  "id": "creator_7",
  "name": "web_weaver",
  "bio": "webs, weavers, and the flies between",
  "avatarUrl": "http://127.0.0.1:8787/avatar/creator_7",
  "stats": { "following": 52, "followers": 198, "likes": 1262 }
}
```

The avatar is a small SVG served by the mock, so nothing on this exercise reaches the public
internet.

### `GET /creator/<creatorId>/items?cursor=&limit=`

That creator's works, paged like the feed.

**These are the same items the feed serves** — `creator_1` owns `item_0000`, `item_0007`,
`item_0014`, … in both places. That is deliberate: after blocking, this page is where a
feed-only filter falls over.

### `POST /moderation`

```json
{ "action": "block_creator", "creatorId": "creator_7" }
{ "action": "report_item",  "itemId": "item_0042", "reason": "spam" }
```

Returns `202` after a delay, and **fails ~20% of the time** (`--fail-rate` to change it).
That failure is deliberate: decide what the user should see when the optimistic local change
cannot be confirmed, and defend your choice in the README.

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
