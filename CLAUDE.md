# WildNav

A static web map for planning wild camping and bikepacking trips. The owner is not a
developer: explain changes briefly and ask before anything destructive.

## What it shows
One overlay at a time, chosen with the "Map" switch (`settings.layer`):
- **Best spots** (default, `score`): one score per cell, see `scorer()` in index.html.
  quietness (distance to nearest house vs. "keep at least", density vs. "too busy above",
  penalty within 150 m of hunting stands) × (0.4 + 0.6 × bonus for useful places nearby).
  Where a hide map is loaded, quietness = √(house part) × hunting × hidden factor
  (`1 − hideW × (1 − hidden)`, "Stay hidden") × noise factor (`1 − quietW × penalty`, 35 → 55 dB,
  "Quiet night"): being hidden counts more than house distance, as the owner asked.
  The whole score is also × slope factor (`1 − slopeW × slopePenalty`: 0 up to 5°, 0.8 at 15°,
  max 0.95 from 25° — a slope alone never rules a spot out, the 30 m model misses small flat patches).
  Red = avoid, clear = ok, green = great spot.
- **Houses** (`density`): houses per km².
- **Useful places** (`nearby`, blue): closeness to water, rivers & lakes, shelters/huts, camp sites,
  viewpoints, fire pits, weighted 0–100 % per category.

## Layout
- Top bar: logo (pine + tent, inline SVG `<symbol id="logo">`, also the favicon), search,
  layer dropdown (`#layer-select`), status, Settings button.
- Map: tool strip (`.tools`, `setTool`: `inspect` = click opens the spot card, saving happens
  from the card; `off` = clicks ignored; add future tools here) and a legend box.
  Below the tools: the locate button (`#btn-locate`, not a click mode). It starts `watchPosition`
  (blue dot); the first fix jumps to zoom `NEAR_Z` (12), where the drawn grid holds the whole 5 km circle.
  `findNearby()` runs after each redraw at that zoom and lists the best `NEAR_N` cells ≥ `NEAR_SEP`
  apart as numbered pins + "Best spots near you" in the panel. Tap again: back to you, then off.
  Tool `near` ("Find spots here"): a map click sets the search centre manually (`setNearAt(…, manual)`),
  shown as a draggable ✥ marker with a dashed 5 km circle; GPS updates don't move a manual centre,
  the locate button brings it back to you. First step towards GPX route planning (IDEAS.md 4).
  Messages for phones go through `showHint()` (the status text is hidden there).
- Side panel (`#panel`): spot card (when open), "Under the cursor" (hidden on touch devices), your spots.
  On phones (≤720 px) it is a bottom sheet: 150 px showing the card summary, tap/swipe the
  handle for 75 %.
- All sliders live in the Settings sheet (`#settings`), over the panel on desktop, full screen on phones.

## Offline / installable app
- `sw.js` (service worker, registered from index.html): app files network-first, CDN files
  cache-first (`wn-lib`), `data/12/*` cache-first (`wn-data`, URLs carry `?v=<build>`; the app
  posts the current build and older tiles are deleted), map images network-first with offline
  fallback (`wn-map`, capped at 3000; no bulk downloads — OSM tile policy).
  **Bump `VERSION` in sw.js when its caching rules change.**
- CDN tags and base-map tiles use CORS (`crossorigin`) so responses can be cached;
  opaque responses are never cached (they inflate the storage quota).
- Settings → "Offline use": "Save this area" stores the data files of the visible tiles.
- `manifest.webmanifest` + `icons/` (PNG, made from the logo shapes with Pillow; iPhone needs
  `apple-touch-icon.png`). iOS keeps storage for home-screen apps; plain Safari may clear it.
- Status bar style must stay `black`, not `black-translucent`: iOS 26 has a bug that shifts the
  whole page up and leaves a gap at the bottom in home-screen apps drawn under the status bar.

## Saved spots
Clicking the map opens a spot card (`openSpot`). Compact on purpose (the owner asked for it): name,
big score + bar (`#sc-top`), key-fact chips (`updateCardSummary`: terrain, protection, hidden, slope,
nearest house, tonight's weather), buttons, then collapsible `<details>` sections made with
`cardSec(key, title, body)`, each with a one-line summary (`setSum(key, html)`): "Why this score"
(`scorer(i, out)` breakdown; summary = the limiting factor), "Around the spot" (compass map `radarSVG`
from `analyseSpot` over the 3×3 tiles), "Weather at night", "Sun & moon" (SunCalc from cdnjs),
"Notes & rating". Open sections are remembered in `localStorage['wn:cardOpen']`.
Weather: Open-Meteo (free, no key, CC BY 4.0, credited in the section), fetched only when a card
opens (`loadWeather`, cached 30 min per ~1 km), one row per night 20:00–08:00 (`weatherNights`). Saved spots live only in
`localStorage['wn:spots']`; export as GPX or JSON backup, import GPX/JSON, share via `#spot=lat,lon,name`.

## Route (GPX)
Panel section "Route": load a GPX track (also via Import when the file has a track but no waypoints),
stored in `localStorage['wn:route']` (thinned to ~25 m). "Find spots" (`analyseRoute`) scores the route
off-screen in 6 km pieces: `tilesReady` waits for the offline tiles, `buildGrid` + `computeDensity(g)` +
`scorer(g)` at `NEAR_Z`, a distance field from the track limits cells to "up to X off route"; the best
cell per 2 km of route is kept. `showRouteRes`: one night per "per day" km (best spot between 85 % and
105 % of each day's distance) plus other good spots ≥ 3 km apart; purple numbered pins = nights.
`buildGrid(z, S, x0, y0, gw, gh)` is the view-independent grid builder (`computeBase` uses it for the view).

## Files
- `index.html` – the whole app in one file (Leaflet 1.9.4 from CDN + plain JavaScript, no build step).
- `build_tiles.py` – converts a Geofabrik `.osm.pbf` + `.poly` into offline tiles in `data/`.
  Needs `pip install "osmium>=4" pillow numpy`; optional `pip install numba` makes the hidden-map
  step ~7x faster (compiled sweep, identical output; without numba the numpy version is used).
  Usage: `python build_tiles.py raw/a.osm.pbf raw/a.poly [raw/b.osm.pbf raw/b.poly ...] [data]`
  Build neighbouring regions **in one run**: each extract is read by two parallel processes
  (`read_region(part='areas')`: terrain, protected areas, lakes, rivers; `part='items'`: points of
  interest, buildings, roads/paths/hedges; merged by `merge_parts`), and tiles on shared borders are
  filled from both extracts with duplicates removed by OSM id.
  Tiles inside no region are left out. Merges into an existing `data/index.json`.
  **Safety:** the owner's PC powered off at full load on all 24 cores. The build therefore uses half
  the cores by default at low priority (`--workers N` to change; 8 has worked well). Hidden maps are
  written atomically and existing readable ones are skipped; if a build stops, it prints a
  `--resume <temp folder>` command that finishes only the hidden maps. With `--keep` the temp folder
  stays, and `--resume <folder> --keep --redo` recomputes all hidden maps (~3 min on 8 cores with
  numba) after tuning `WAYS` sight weights, `K_*`, `E0` etc. — no need to re-read the OSM files.
  Full build of both states: ~13 min on 8 cores with numba (reading ~8 min, hidden maps ~3 min).
  Converters for an existing data folder (no OSM needed): `--b16` (buildings .bin -> .b16),
  `--palette` (terrain maps -> palette PNG, lossless).
  Tests: `python -m unittest discover tests` (synthetic inputs, no OSM/DEM files; the numba test is
  skipped without numba). They pin the calibration (meadow/forest/ridge/noise/path distance), check
  numba == numpy, the file formats and the slope/hollow maths. Run them after changing build_tiles.py.
  Current data: Niedersachsen + Nordrhein-Westfalen (Geofabrik 2026-09-28).
- `data/` – generated offline tiles, served as static files. **Not committed on `main`** (only
  `data/LICENSE.md`; the rest is in .gitignore): `publish_site.py` publishes them, see "Publishing".
  - `data/index.json` – `{z: 12, built, bfmt: "u16", tiles: ["x/y", ...]}` (`built` = cache version)
  - `data/12/<x>/<y>.b16` – building centres, Uint16 pairs (tile-relative Mercator x,y × 65535, ~10 cm).
    Older data had `.bin` with Float32 pairs; the app reads `.b16` when index.json says `bfmt: "u16"`.
  - `data/12/<x>/<y>.json` – points of interest `[{c, lat, lon, n}]`
  - `data/12/<x>/<y>.water.bin` – rivers/streams/canals and lake shores sampled every ~40 m,
    Uint16 pairs (tile-relative Mercator x,y × 65535). Category `rivers` ("Rivers & lakes") in the
    app; offline data only (no Overpass equivalent), no markers. Ditches, drains, intermittent
    and culverted water are excluded on purpose.
  - `data/protected.json` – protected areas `[{n: name, l: level, t: type}]`, id = index + 1.
    Level 2 = nature reserve / national park (score × 0), 1 = landscape protection / Natura 2000
    (score × 0.5); nature parks and water protection areas are ignored (`prot_level()`).
    **The ids are global: always build all regions in one run**, or ids and tiles won't match.
  - `data/12/<x>/<y>.land.png` – terrain map, 512×512 PNG (~12 m/pixel), stored as a palette PNG
    when a tile has ≤ 256 colour combinations (always so far; lossless, browsers decode it to RGB). Green/blue =
    protected-area id (high/low byte, 0 = none), added in a second build step on all cores.
    Red = terrain class (`LAND` in build_tiles.py and index.html, keep in sync; code = drawing order,
    16 = water on top). Drawn with Pillow. The app loads these only from zoom 11 (`LAND_MIN_Z`)
    and around an open spot card (~260 KB each once decoded). The score is multiplied by
    `LAND_FACTOR` (water/built-up/cemetery/military 0, quarry 0.1, wetland/orchard/park 0.3,
    field 0.7, scrub/sand 0.8, forest/meadow/heath/unmapped 1).
  - `data/12/<x>/<y>.hide.png` – 256×256 greyscale PNG (~24 m/pixel): hidden 0–255 in steps of
    `HIDE_Q` = 8 (~1 %-point error, files ~40 % smaller), third build step (`hide_tile()`).
    `data/12/<x>/<y>.nb.png` – 128×128 RGB (~47 m): R = road/railway noise in dB(A) (loudest of each
    2×2), G = distance to the nearest path/road you can cycle on in 10 m (shortest of each 2×2,
    255 = 2.5 km+; exact distance transform `path_distance`, not rays). Computed on the 3×3 tiles around
    each tile by sweeping 16 directions: observers = roads/paths (`WAYS`, weighted by how busy) and
    houses; forest, scrub, hedges and buildings block sight/sound (`K_SIGHT`, `K_HEAR`, `K_NOISE`);
    tunables `L_SIGHT`, `L_HEAR`, `E0` etc. at the top of build_tiles.py (see IDEAS.md 1.3 for the idea).
    Hills: with FABDEM (`--dem`, default `raw/fabdem`) each ray also tracks the terrain between the
    observer (eyes 1.7 m) and a tent (1.2 m); a rise must stick out `DEM_TOL` = 2 m to block (DEM errors),
    voices carry 30 % over a ridge, traffic noise −8 dB behind one.
    Only the rays through the centre tile are swept, and only as far as needed (`rot_maps`).
    Loaded with the terrain maps (`ensureLand`, `decodeHide` merges both files, `hideAt`).
  - `data/12/<x>/<y>.slope.png` – 128×128 RGB PNG (~47 m/pixel) from FABDEM V1-2 (30 m elevation with
    forests and buildings removed; 1°×1° GeoTIFFs in `raw/fabdem/`, from the LINKS Foundation mirror
    on Hugging Face). R = slope in ¼°, G = direction the slope faces (16 compass points × 16) + in the
    low 4 bits the hollow depth in 2 m (mean ground within ~420 m minus the ground here), B = elevation / 4 m.
    The app shows a "cold hollow" (≥ 6 m deep, < 6° slope: cold air and dew at night) as information
    only, not in the score (`isHollow`, `slopeAt`). Separate step, no OSM needed: `python build_tiles.py --slope raw/fabdem [data]`
    (skips existing, `--redo` for all). `--slope` and `--resume` update `built` in index.json
    (`bump_version`), otherwise browsers keep serving their cached old tiles. **Licence: non-commercial only** (credit in the app footer,
    README and data/LICENSE.md). Loaded with the terrain maps (`decodeSlope`, `slopeAt`).
- `raw/` – Geofabrik downloads. Large – never commit (in `.gitignore`).
- `start.bat` – starts a local server on port 8765 and opens the app.
- `IDEAS.md` – planned features (e.g. the "hidden spot" score) with research notes and algorithm sketches.

## How data loading works
- Data is organised in zoom-12 tiles (~6×6 km at 52°N).
- At startup the app loads `data/index.json`. Tiles listed there are read from
  `data/12/x/y.bin` + `.json` (all buildings as points, no built-up shortcut).
- Tiles outside the offline data can fall back to the Overpass API. This is **off by default**
  (setting `overpass`, toggle "Download other areas from OpenStreetMap"). Buildings are first
  counted per ~750 m cell (8×8 per tile); cells above the "built-up" threshold are stored
  as counts instead of points. Overpass results are cached in IndexedDB for 30 days.
- `classify()` in `index.html` and in `build_tiles.py` must stay in sync.

## Publishing (GitHub Pages serves the `gh-pages` branch)
- `main` holds the code and its history; `gh-pages` holds the website: `index.html`, `sw.js`,
  `manifest.webmanifest`, `README.md`, `icons/`, `data/` and `.nojekyll`, always as ONE commit that is
  replaced on every publish, so data builds don't grow the repository history.
- `python publish_site.py` builds that commit straight from this folder (git database in
  `%LOCALAPPDATA%\WildNav-publish`, outside OneDrive) and force-pushes it; only changed files upload.
- After every change: commit + push `main` (code) **and** run `publish_site.py` (site), otherwise the
  live site doesn't change. Pages setting: Settings → Pages → Deploy from a branch → `gh-pages` / root.
- The old data versions are still in `main`'s history from before the switch (~660 MB); removing them
  would mean rewriting history (only with the owner's explicit OK).

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
- Keep `index.html` self-contained apart from CDN libraries, the `data/` folder and the
  installable-app files (`sw.js`, `manifest.webmanifest`, `icons/`), which browsers require as
  separate files.
- After every change: test in the preview, check the browser console, then tell the owner
  what to look at. Commit with a clear message only after they say it looks good, then push `main`
  and publish the site (`python publish_site.py`).
