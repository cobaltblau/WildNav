# WildNav

A static web map for planning wild camping and bikepacking trips. The owner is not a
developer: explain changes briefly and ask before anything destructive.

## Four activities (top bar tabs)
One choice answers "what am I doing?": **Sleep · Break · Resupply · Route** (`ACTS`, `setActivity`, `settings.activity`; owner's
concept, IDEAS.md section 7; the old Night/Day switch, the layer menu and the spot-type pop-out are gone). The tab sets
- the map layer (`home`: Best spots · Break spots · Resupply · route without colour overlay),
- the markers (`ACTS[a].cats`), "Here" (`updateHover` for Sleep, `updateHoverDay` for Break / Resupply, nothing in Route),
- the kind of spot a map click opens and what "Find spots here" / "Locate me" look for (`settings.spotType` = camp / break /
  supply, set by the tab; the Route tab keeps the last kind): best camp spots (`scorer`), best break spots (`breakScorer`),
  the nearest resupply places (`findNearbyDay`).
`isDay()` = "not Sleep" (day places need the day distance fields in `buildGrid`). Switching a spot's type in its card, or opening
a saved spot of another kind, switches the tab (not while in Route). Saved pins of other kinds are faded.
Details (legend chips, clock, Resupply, Break, spot types, tool strip, settings order): skill `app-ui`;
Route tab: skill `route-planner`.

## Owner's design rules (all UI work)
- Spot cards are compact on purpose (the owner asked for it); details in skill `spot-card`.
- **Nothing may jump when data arrives** (the owner asked for this): every tile and row is always there in the
  same place, showing "…" (or "no data" outside the offline regions, `cardWait()`) until its value is loaded; rows keep
  their second line (e.g. "no name on the map", "none within 2 km"); conditional info goes into an existing tile/row
  (cold hollow in the slope tile, protected area in the terrain tile) instead of adding one. Within a section, rows whose
  second line is always one short line come first; rows that can grow (terrain mix, protected-area name, place names,
  gust warning) come last. Keep second lines under ~38 characters (one line at the 340 px panel width).
- Colour-blind friendly colours (Settings → Display, `settings.cbSafe`): `buildLuts()` swaps the map tables `LUT_S`/`LUT_D`
  (orange ↔ blue-green instead of red ↔ green) and `body.cb` swaps the CSS meaning colours `--good/--mid/--bad`
  (use these variables, not fixed greens/reds, for anything that means good/bad).
- No emoji: all icons come from one inline SVG sprite in index.html (`<symbol id="i-…">`), used with
  `ic('name', colour)` in JavaScript or `<svg class="ic"><use href="#i-name"/></svg>` in HTML.
  Style: **Phosphor Icons duotone** (MIT, credited in a comment above the sprite), copied from
  `@phosphor-icons/core@2.1.1/assets/duotone/<name>-duotone.svg` (jsDelivr). `hollow`, `stand` (hunting high seat)
  and `shelter` are drawn for WildNav in the same style (256 grid, 16 px round lines, 20 % fill).
  Category icons are in `CATS[k].icon`, terrain icons in `LAND[c][1]` (`landIc(c)` colours by suitability),
  `protIc(level)` for protected areas. Colour icons by meaning (category colour), not decoration. Text should
  wrap instead of being cut off with "…".

## Where to find details
Details live in project skills (`.claude/skills/<name>/SKILL.md`). Load the matching skill before working on that part;
the last column lists names to search for in the code.

| Part | Skill | Search for |
|---|---|---|
| Activity tabs, map layers and chips, Sleep / Break / Resupply scores, opening hours, clock, legend, markers, tool strip, locate, Settings sheet, My spots | `app-ui` | `ACTS`, `setActivity`, `LAYERS`, `scorer`, `breakScorer`, `ohState`, `drawLegend`, `makeSlider`, `renderSpotList` |
| Spot card, side panel, "Here", weather in the card, saved-spot storage | `spot-card` | `openSpot`, `updateCardSummary`, `cardSec`, `cardRow`, `updateHover`, `loadWeather` |
| Route tab: route window, trip plan, timeline, GPX route | `route-planner` | `planTrip`, `planNights`, `drawTimeline`, `parseRoute`, `scanPiece` |
| `build_tiles.py`, `calibrate_noise.py`, data file formats, terrain / hidden / noise / slope maps and how the app reads them | `data-build` | `classify`, `read_region`, `hide_tile`, `WAYS`, `ensureLand`, `hideAt`, `slopeAt` |
| `sw.js`, offline use, installable app, gh-pages history | `publish-offline` | `VERSION`, `wn-data`, `wn-map` |

## Files
- `index.html` – the whole app in one file (Leaflet 1.9.4 from CDN + plain JavaScript, no build step).
- `sw.js`, `manifest.webmanifest`, `icons/` – offline / installable app (skill `publish-offline`).
- `build_tiles.py` – converts a Geofabrik `.osm.pbf` + `.poly` into offline tiles in `data/`.
  Current data: Niedersachsen + Nordrhein-Westfalen (Geofabrik 2026-09-28).
- `calibrate_noise.py` – recalibrates the road noise levels in `WAYS` against the DLR road noise map
  (`collect` samples per region from a .osm.pbf, `fit` prints the shift per road type). Run after a build.
- `publish_site.py` – publishes the website to the `gh-pages` branch (see "Publishing").
- `data/` – generated offline tiles, served as static files. **Not committed on `main`** (only
  `data/LICENSE.md`; the rest is in .gitignore): `publish_site.py` publishes them, see "Publishing".
  There is **no data outside the prepared regions**. The old Overpass download fallback was removed
  (owner's decision, 2026-09-30): without the terrain, hidden, noise and slope maps its score was misleading.
  Its IndexedDB cache ('wildnav') is deleted once on start (`wn:idbGone`). Category rules live only in
  `classify()` in `build_tiles.py`.
- `tests/` – unit tests for build_tiles.py (`test_build_tiles.py`) and the app smoke test (`smoke_app.py`).
- `.claude/` – Claude Code setup: `skills/` (details per part of the app), `launch.json` (preview server),
  `settings.json` (hooks, permissions), `hooks/check.py` (syntax check after every edit: Python, JavaScript via
  Node.js incl. the inline script of index.html, JSON; silent when fine).
- `mockups/` – clickable concept mockups with invented data (not published, not part of the app).
- `raw/` – Geofabrik downloads. Large – never commit (in `.gitignore`).
- `start.bat` – starts a local server on port 8765 and opens the app.
- `IDEAS.md` – planned features (e.g. the "hidden spot" score) with research notes and algorithm sketches.
- `HANDOFF.md` – note from the last session for the next one (not in git, see "Working efficiently").

## Run locally and test
- Double-click `start.bat`, or run `python -m http.server 8765` in this folder and open
  http://localhost:8765/ (opening index.html directly as a file will not load `data/`).
- Tests: `python -m unittest discover tests` (synthetic inputs, no OSM/DEM files; the numba test is
  skipped without numba). They pin the calibration (meadow/forest/ridge/noise/path distance), check
  numba == numpy, the file formats and the slope/hollow maths. Run them after changing build_tiles.py.
- App smoke test: `python tests/smoke_app.py` (~5 s, headless Chromium via Playwright): clicks the four tabs, Settings,
  My spots and the map (spot card with score), checks `typeKey` / `ohState` and catches console errors and uncaught
  exceptions. Prints only "OK" or the problems; skipped without Playwright or `data/`. One-time install:
  `pip install playwright`, then `python -m playwright install chromium`.

## Publishing (GitHub Pages serves the `gh-pages` branch)
- `main` holds the code and its history; `gh-pages` holds the website: `index.html`, `sw.js`,
  `manifest.webmanifest`, `README.md`, `icons/`, `data/` and `.nojekyll`, always as ONE commit that is
  replaced on every publish, so data builds don't grow the repository history.
- `python publish_site.py` builds that commit straight from this folder (git database in
  `%LOCALAPPDATA%\WildNav-publish`, outside OneDrive) and force-pushes it; only changed files upload.
- After every change: commit + push `main` (code) **and** run `publish_site.py` (site), otherwise the
  live site doesn't change. Pages setting: Settings → Pages → Deploy from a branch → `gh-pages` / root.

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
- Before showing the owner a change: run the smoke test (`python tests/smoke_app.py`, and the unit tests after
  changing build_tiles.py). The preview is for showing the owner things.
- After every change: test in the preview, check the browser console, then tell the owner
  what to look at. Commit with a clear message only after they say it looks good, then push `main`
  and publish the site (`python publish_site.py`).

## Working efficiently (rules for Claude)
- Never read `index.html` (~90,000 tokens), `build_tiles.py` or `mockups/` whole. Grep for the function or constant
  name (CLAUDE.md and the skills name them), then Read ~50–150 lines around it (offset/limit). Read the mockup only
  when the owner asks for it.
- Don't re-read a file after editing it. Never read files in `raw/` or `data/12/` (blocked in `.claude/settings.json`,
  `ls` too; to see which files are there use a short Python command that prints only names).
- Keep command output short: unit tests as `python -m unittest discover -q -b tests` (only failures print); add
  `| tail -n 30` to anything long; run long builds in the background writing to a log and read only its end;
  scripts print summaries, never whole arrays or JSON.
- Check the app with console messages and small JavaScript checks in the page, not screenshots. Take a screenshot
  only to show the owner something.
- Bigger changes: plan first (plan mode), then small steps with a test after each.
- One task per session. When a phase is done, write a short handoff note in `HANDOFF.md` (replace the old one:
  what's done, what's next, decisions, open questions), then tell the owner to type `/clear` and what to type next,
  e.g. "Read HANDOFF.md and continue with …".
- Before showing the owner a result: run the checks named in "Rules for all work" and read your own `git diff` once,
  looking for mistakes.

# Compact instructions
When this conversation is summarised, keep: the current task and the step it is at; the owner's decisions and their
reasons; which files were changed and how; test results (unit tests, smoke test, console errors); open questions and
the next step. Leave out file contents that were read, long command output and screenshots.
