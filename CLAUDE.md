# WildNav

A static web map for planning wild camping and bikepacking trips. The owner is not a
developer: explain changes briefly and ask before anything destructive.

## What it shows
One overlay at a time, chosen with the "Map" switch (`settings.layer`):
- **Best spots** (default, `score`): one score per cell, see `scorer()` in index.html.
  quietness (distance to nearest house vs. "keep at least", density vs. "too busy above",
  penalty within 150 m of hunting stands) × (0.4 + 0.6 × bonus for useful places nearby).
  Red = avoid, clear = ok, green = great spot.
- **Houses** (`density`): houses per km².
- **Useful places** (`nearby`, blue): closeness to water, rivers & lakes, shelters/huts, camp sites,
  viewpoints, fire pits, weighted 0–100 % per category.

## Files
- `index.html` – the whole app in one file (Leaflet 1.9.4 from CDN + plain JavaScript, no build step).
- `build_tiles.py` – converts a Geofabrik `.osm.pbf` + `.poly` into offline tiles in `data/`.
  Needs `pip install "osmium>=4"`.
  Usage: `python build_tiles.py raw/a.osm.pbf raw/a.poly [raw/b.osm.pbf raw/b.poly ...] [data]`
  Build neighbouring regions **in one run**: each extract is read in its own process (parallel),
  and tiles on shared borders are filled from both extracts with duplicates removed by OSM id.
  Tiles inside no region are left out. Merges into an existing `data/index.json`.
  Current data: Niedersachsen + Nordrhein-Westfalen (Geofabrik 2026-09-28).
- `data/` – generated offline tiles (committed, served as static files):
  - `data/index.json` – `{z: 12, built, tiles: ["x/y", ...]}`
  - `data/12/<x>/<y>.bin` – Float32 pairs, tile-relative Mercator x,y (0..1) of building centres
  - `data/12/<x>/<y>.json` – points of interest `[{c, lat, lon, n}]`
  - `data/12/<x>/<y>.water.bin` – rivers/streams/canals and lake shores sampled every ~40 m,
    Uint16 pairs (tile-relative Mercator x,y × 65535). Category `rivers` ("Rivers & lakes") in the
    app; offline data only (no Overpass equivalent), no markers. Ditches, drains, intermittent
    and culverted water are excluded on purpose.
- `raw/` – Geofabrik downloads. Large – never commit (in `.gitignore`).
- `start.bat` – starts a local server on port 8765 and opens the app.

## How data loading works
- Data is organised in zoom-12 tiles (~6×6 km at 52°N).
- At startup the app loads `data/index.json`. Tiles listed there are read from
  `data/12/x/y.bin` + `.json` (all buildings as points, no built-up shortcut).
- Tiles outside the offline data can fall back to the Overpass API. This is **off by default**
  (setting `overpass`, toggle "Download other areas from OpenStreetMap"). Buildings are first
  counted per ~750 m cell (8×8 per tile); cells above the "built-up" threshold are stored
  as counts instead of points. Overpass results are cached in IndexedDB for 30 days.
- `classify()` in `index.html` and in `build_tiles.py` must stay in sync.

## Run locally
Double-click `start.bat`, or run `python -m http.server 8765` in this folder and open
http://localhost:8765/ (opening index.html directly as a file will not load `data/`).

## Licences (keep in mind when adding services)
- Only use base maps / APIs that are free without an API key and show their attribution
  (currently OSM, OpenTopoMap, EOX Sentinel-2 cloudless). No Esri or CARTO tiles (need a key).
- No Google Fonts (GDPR); fonts come from Fontsource via jsDelivr.
- `data/` is ODbL (see `data/LICENSE.md`); keep the OSM/ODbL credit in the app footer.

## Rules for all work
- Keep the app static: it must run from any static host (GitHub Pages). No server code.
- Keep `index.html` self-contained apart from CDN libraries and the `data/` folder.
- After every change: test in the preview, check the browser console, then tell the owner
  what to look at. Commit with a clear message only after they say it looks good, then push.
