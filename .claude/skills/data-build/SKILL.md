---
name: data-build
description: "WildNav offline data. build_tiles.py (Geofabrik .osm.pbf + .poly, classify, read_region, merge_parts, --workers, --resume, --redo, --slope, numba, FABDEM), calibrate_noise.py (DLR noise map, WAYS), tests/test_build_tiles.py, and the data/ file formats (index.json, .b16 buildings, .json places, .water.bin, protected.json, .land.png terrain, .hide.png hidden map, .nb.png noise and path distance, .slope.png slope, cold hollow, elevation; LAND_FACTOR, HIDE_Q, K_SIGHT, E0), plus how index.html reads them (ensureLand, decodeHide, hideAt, decodeSlope, slopeAt). Use before changing the build, the calibration or code that reads data files."
---

# WildNav offline data: build and file formats

## build_tiles.py
(One-line summary in the CLAUDE.md file list; the unit tests are in CLAUDE.md, "Run locally and test".)
Needs `pip install "osmium>=4" pillow numpy`; optional `pip install numba` makes the hidden-map
step ~7x faster (compiled sweep, identical output; without numba the numpy version is used).
Usage: `python build_tiles.py raw/a.osm.pbf raw/a.poly [raw/b.osm.pbf raw/b.poly ...] [data]`
Build neighbouring regions **in one run**: each extract is read by two parallel processes
(`read_region(part='areas')`: terrain, protected areas, lakes, rivers; `part='items'`: points of
interest incl. shops/cafés/sights, buildings, roads/paths/hedges; merged by `merge_parts`), and tiles on shared borders are
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

## Data files (`data/`)
- `data/index.json` – `{z: 12, built, bfmt: "u16", tiles: ["x/y", ...]}` (`built` = cache version)
- `data/12/<x>/<y>.b16` – building centres, Uint16 pairs (tile-relative Mercator x,y × 65535, ~10 cm).
  Older data had `.bin` with Float32 pairs; the app reads `.b16` when index.json says `bfmt: "u16"`.
- `data/12/<x>/<y>.json` – points of interest `[{c, lat, lon, n}]`; day places (`day_class()`: groceries, food, bike,
  toilets, sights) also have `t` (OSM type, e.g. `bakery`, `fuel`) and `o` (opening_hours, if tagged). A shop in a
  building is stored twice: as a house (for the night score) and as a place (`poi_cats()`). The app ignores
  categories it doesn't know, but older app versions fail on them: publish app and data together.
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
  Road noise levels per road type (`WAYS`, night dB at 10 m) are calibrated against the DLR Noise2NAKO AI
  road noise map (Lden 2017, CC BY 4.0, Lden = night + 8 dB) with `calibrate_noise.py` (needs `tifffile`,
  `imagecodecs`, `pyproj` and `raw/dlr/RF_RoadLden_DE_2017.tif`; only for calibration, the app never uses it):
  motorway 72, trunk 66, primary 59, secondary 50 fitted; tertiary 44, unclassified 37, residential 35 continue
  the steps (DLR's 55 dB Lden threshold only gives an upper bound there). Railways are not calibrated.
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

## How data loading works
- Data is organised in zoom-12 tiles (~6×6 km at 52°N).
- At startup the app loads `data/index.json`. Tiles listed there are read from
  `data/12/x/y.b16` + `.json` (all buildings as points), terrain/hidden/slope maps from zoom 11 (`ensureLand`).
- No data outside the prepared regions, Overpass fallback removed: CLAUDE.md, `data/` in the file list.
