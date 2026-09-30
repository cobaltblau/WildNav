# WildNav

A static web map for planning wild camping and bikepacking trips. The owner is not a
developer: explain changes briefly and ask before anything destructive.

## What it shows
One overlay at a time, chosen with the layer picker in the top bar (`#layer-btn` / `#layer-menu`, `LAYERS`, `settings.layer`),
grouped as "Plan" (Best spots) and "Why a spot scores" (the factors behind the score):
- **Hidden** (`hidden`), **Quiet night** (`quiet`, road/rail dB), **Topography** (`topo`, slope + cold hollows in blue) and
  **Terrain & rules** (`terrain`, ground for a tent by `LAND_FACTOR` class + protected areas hatched red/orange):
  `FACTOR_LAYERS`, drawn in `composite()` from the grid (`R.hid/hdb/slp/hol/land/prot`, only from zoom `LAND_MIN_Z`),
  colour tables in `buildFactorLuts()` (colour-blind variants too).
- Workflow links: factor rows in the spot card ("Score", "Nearby") and in "Here" carry `data-layer`; clicking one shows
  that layer, the current layer's rows are highlighted (`markLayerRows`), the legend has "← Best spots" (`#leg-back`).
The original three:
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
  from the card; `off` = clicks ignored; add future tools here) and a legend box. Legend: the colour bar is drawn as it
  looks on the grey map (`drawLegend` blends over map grey at the layer's opacity); a white marker (`setLegendMark`) shows
  the value under the cursor; the explanation folds away behind the title (`wn:legOpen`). Points of interest are dots
  up to zoom 12 and round icon badges from `BADGE_MIN_Z` (13).
  Colour-blind friendly colours (Settings → Display, `settings.cbSafe`): `buildLuts()` swaps the map tables `LUT_S`/`LUT_D`
  (orange ↔ blue-green instead of red ↔ green) and `body.cb` swaps the CSS meaning colours `--good/--mid/--bad`
  (use these variables, not fixed greens/reds, for anything that means good/bad).
  Below the tools: the locate button (`#btn-locate`, not a click mode). It starts `watchPosition`
  (blue dot); the first fix jumps to zoom `NEAR_Z` (12), where the drawn grid holds the whole 5 km circle.
  `findNearby()` runs after each redraw at that zoom and lists the best `NEAR_N` cells ≥ `NEAR_SEP`
  apart as numbered pins + "Best spots near you" in the panel. Tap again: back to you, then off.
  Tool `near` ("Find spots here"): a map click sets the search centre manually (`setNearAt(…, manual)`),
  shown as a draggable ✥ marker with a dashed 5 km circle; GPS updates don't move a manual centre,
  the locate button brings it back to you. First step towards GPX route planning (IDEAS.md 4).
  Messages for phones go through `showHint()` (the status text is hidden there).
- Side panel (`#panel`, headings short: "Here", "Near you", "Route", "Your spots"): spot card (when open), "Here"
  (under the cursor, hidden on touch devices), your spots. On desktop the panel floats over the map (the map is full
  width) and each section is its own rounded card, like the tool strip and legend (owner's wish).
  "Under the cursor" (`updateHover`) always shows the same rows ("—", "zoom in" or "…" when a value is missing),
  a reserved line under each place name, and one two-line hint at the bottom (also names a protected area),
  so the panel never changes height while the mouse moves.
  On phones (≤720 px) it is a bottom sheet: 150 px showing the card summary, tap/swipe the
  handle for 75 %.
- All sliders live in the Settings sheet (`#settings`): on desktop floating cards like the panel (the panel is hidden
  while it is open, `body.set-open`), full screen on phones. `makeSlider({label, hint, …})`: short label + quiet hint left,
  value fixed at the right (no moving bubble); `makeToggle(parent, label, key, onChange, hint)`; long explanations go
  into `<details class="more">` ("How it works").

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
big score + a one-line reason ("Held back most by …") + bar (`#sc-top`), a 2-column grid of six key-fact tiles
(`updateCardSummary`: terrain + protected area, hidden, nearest house, slope + cold hollow, noise, tonight's weather),
buttons, then collapsible `<details>` sections made with
`cardSec(key, icon, title, body)`, each with a summary line under the title (`setSum(key, html)`). No information twice:
"Score" (`updateCardScore`) = everything about the spot itself with its effect on the score (terrain, slope +
cold hollow, houses, hunting stands, hidden, quiet night, access, terrain within 100 m, protected area);
"Nearby" (`updateCardAround`) = compass map `radarSVG` from `analyseSpot` over the 3×3 tiles + the useful
places and their bonus; "Night" (key `wx`) = sunset, moon, next sunrise (SunCalc from cdnjs) then the weather per
night; "Notes" (rating + text). Section titles: one or two words. Rows are built with `cardRow(icon, title, value, second line)`.
Open sections are remembered in `localStorage['wn:cardOpen']`.
**Nothing may jump when data arrives** (the owner asked for this): every tile and row is always there in the
same place, showing "…" (or "no data" outside the offline regions, `cardWait()`) until its value is loaded; rows keep
their second line (e.g. "no name on the map", "none within 2 km"); conditional info goes into an existing tile/row
(cold hollow in the slope tile, protected area in the terrain tile) instead of adding one. Within a section, rows whose
second line is always one short line come first; rows that can grow (terrain mix, protected-area name, place names,
gust warning) come last. Keep second lines under ~38 characters (one line at the 340 px panel width).
Weather: Open-Meteo (free, no key, CC BY 4.0, credited in the section), fetched only when a card
opens (`loadWeather`, cached 30 min per ~1 km, up to 3 tries before it shows "offline"), one row per night 20:00–08:00 (`weatherNights`). Saved spots live only in
`localStorage['wn:spots']`; export as GPX or JSON backup, import GPX/JSON, share via `#spot=lat,lon,name`.

## Icons
No emoji: all icons come from one inline SVG sprite in index.html (`<symbol id="i-…">`), used with
`ic('name', colour)` in JavaScript or `<svg class="ic"><use href="#i-name"/></svg>` in HTML.
Style: **Phosphor Icons duotone** (MIT, credited in a comment above the sprite), copied from
`@phosphor-icons/core@2.1.1/assets/duotone/<name>-duotone.svg` (jsDelivr). `hollow`, `stand` (hunting high seat)
and `shelter` are drawn for WildNav in the same style (256 grid, 16 px round lines, 20 % fill).
The ground is called "Topography" in the UI: `topoIc(deg, hollow)` draws the ramp on the fly for every whole degree
(same base line always, drawn at 2× the real angle, max 40°; 0° = a flat line) or shows the cold hollow;
coloured green -> red by `slopeCol(deg)` (red from 15°).
Category icons are in `CATS[k].icon`, terrain icons in `LAND[c][1]` (`landIc(c)` colours by suitability),
`protIc(level)` for protected areas. Colour icons by meaning (category colour), not decoration. Text should
wrap instead of being cut off with "…".

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
    Uint16 pairs (tile-relative Mercator x,y × 65535). Category `rivers` ("Open water") in the
    app; no markers. Ditches, drains, intermittent
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
  `data/12/x/y.b16` + `.json` (all buildings as points), terrain/hidden/slope maps from zoom 11 (`ensureLand`).
- There is **no data outside the prepared regions**. The old Overpass download fallback was removed
  (owner's decision, 2026-09-30): without the terrain, hidden, noise and slope maps its score was misleading.
  Its IndexedDB cache ('wildnav') is deleted once on start (`wn:idbGone`). Category rules live only in
  `classify()` in `build_tiles.py`.

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
