# WildNav

A static web map for planning wild camping and bikepacking trips. The owner is not a
developer: explain changes briefly and ask before anything destructive.

## What it shows
- **Places to avoid** (red): distance to, or density of, houses (buildings) and hunting stands.
- **Good to have nearby** (blue): closeness to water, shelters/huts, camp sites, viewpoints, fire pits.

## Files
- `index.html` – the whole app in one file (Leaflet 1.9.4 from CDN + plain JavaScript, no build step).
- `build_tiles.py` – converts a Geofabrik `.osm.pbf` + `.poly` into offline tiles in `data/`.
  Needs `pip install "osmium>=4"`.
  Usage: `python build_tiles.py raw/<region>.osm.pbf raw/<region>.poly [data]`
  (merges into an existing `data/index.json`, so several regions can share one folder).
- `data/` – generated offline tiles (committed, served as static files):
  - `data/index.json` – `{z: 12, built, tiles: ["x/y", ...]}`
  - `data/12/<x>/<y>.bin` – Float32 pairs, tile-relative Mercator x,y (0..1) of building centres
  - `data/12/<x>/<y>.json` – points of interest `[{c, lat, lon, n}]`
- `raw/` – Geofabrik downloads. Large – never commit (in `.gitignore`).
- `start.bat` – starts a local server on port 8765 and opens the app.

## How data loading works
- Data is organised in zoom-12 tiles (~6×6 km at 52°N).
- At startup the app loads `data/index.json`. Tiles listed there are read from
  `data/12/x/y.bin` + `.json` (all buildings as points, no built-up shortcut).
- Tiles outside the offline data fall back to the Overpass API: buildings are first
  counted per ~750 m cell (8×8 per tile); cells above the "built-up" threshold are stored
  as counts instead of points. Overpass results are cached in IndexedDB for 30 days.
- `classify()` in `index.html` and in `build_tiles.py` must stay in sync.

## Run locally
Double-click `start.bat`, or run `python -m http.server 8765` in this folder and open
http://localhost:8765/ (opening index.html directly as a file will not load `data/`).

## Rules for all work
- Keep the app static: it must run from any static host (GitHub Pages). No server code.
- Keep `index.html` self-contained apart from CDN libraries and the `data/` folder.
- After every change: test in the preview, check the browser console, then tell the owner
  what to look at. Commit with a clear message only after they say it looks good, then push.
