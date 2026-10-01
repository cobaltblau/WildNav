---
name: app-ui
description: "WildNav app UI in index.html. The four activity tabs (Sleep, Break, Resupply, Route; ACTS, setActivity), map layers and the Why this score chips (LAYERS, renderChips, setLayer), the Sleep score (scorer, houses, hunting stands, hidden, quiet night, slope, useful places), the Break score (breakScorer, breakMix, shade, view, bench), Resupply and opening hours (DAY_SUPPLY, ohState, clock chip), spot types (SPOT_TYPES, typeKey), markers and badge clusters, legend (drawLegend), tool strip, locate and Find spots here (findNearby), the Settings sheet (makeSlider, makeToggle), the topoIc slope icon and the My spots window (renderSpotList, reorder, inRoute). Use before changing or explaining any of these."
---

# WildNav app UI (index.html)

## Activity details
(Continues "Four activities" in CLAUDE.md.)
- **"Why this score ▸" chips** in the legend (`renderChips`, `ACTS[a].why`) hold the factor layers of the current activity
  (Sleep: Hidden, Traffic noise, Topography, Terrain & rules, Houses, Useful places; Break: Shade, View, Path access, Traffic
  noise, Topography, Sights & views, Swimming spots; Resupply: Groceries, Cafés & food, Water, Toilets, Bike repair = "Only show").
  They explain, they don't switch activity; "← Best spots" goes back to the home layer. `setLayer(l)` picks the activity that
  owns the layer (`actOwns`). Layers are in `LAYERS`; the legend text of Topography / Traffic noise differs by day (`DAY_NAMES`).
- **Clock chip** next to the tabs (`#btn-when`, `settings.ohAt`, `ohWhen()`, `ohChanged()`): the time for opening hours and shade
  (now, or a weekday + time); "now" views refresh every 5 min. The route plan has its own start day / time.
- **Resupply** (`DAY_SUPPLY`: groceries incl. petrol stations, cafés & food, water, bike shops & repair, toilets, train
  stations): `NEAREST` distance maps (cut-off "Worth a detour" `settings.cutS`); card (`updateDayCard`, type supply): tiles
  with the nearest **open** place of each kind at the clock time (no hours in the map = maybe open, closer closed ones are
  counted) + a list of everything within 1 km with hours. Opening hours: `ohState()` ('open' / 'closed' / null when not
  understood: months, sunrise, "+" …; later rules replace earlier ones, PH ignored).
- **Break**: `breakScorer` = close to a path (`breakAccess`: full at the path, ~half at 35 m, ~0 from 60 m) × ground × traffic ×
  extras (`breakMix`, owner's rule): 0.15 + 0.65 × (shade at the clock time, water, view; weights `settings.bShade/bWater/bView`)
  = **at most 80 % without somewhere to sit**, + 0.1 for a bench/table/shelter, + another 0.1 with a bench **and** a view or
  sight nearby (= 100 %); `bQuiet` for traffic. `shadeGrid`: forest (`SHADE_H`, 20 m) and hills (elevation `g.elv`) towards
  the sun from SunCalc; not buildings. View = height above the land within ~350 m (`promGrid` / `promAt`) outside forest, or
  a viewpoint. Markers: dense categories only when zoomed in (`CATS[k].minZ`); from zoom 13 overlapping badges are merged
  (`rebuildMarkers`, `CLUSTER_PX` = 28: a pill with the icons of the three most common kinds + count, convex hull outline).
- **Spot types** (`SPOT_TYPES`: only three on purpose — camp, break, supply; anything else is a renamed break; old name
  'shop' = supply via `typeKey`). The spot button in the tool strip shows the kind's icon (`syncSpotTool`). Saved spots store
  `type` and `inRoute` (pin icon, GPX `<sym>`/`<type>`, share link `&t=`). Camp card = the camping information below (owner:
  no day information in it); break / resupply cards: `updateDayCard`.
- Day categories have `group: 'day'` in `CATS` and are not part of the night score. Types (`p.t`) are shown in words via `DAY_TYPES`.
- Tool strip: spot button, "Find spots here" (hidden in Route), locate. Search first looks for a saved spot, then Nominatim.
- Settings order: Sleep: houses · Sleep: hidden & quiet · Sleep: useful places · Break · Resupply & places · Display · Map data · Offline.

## What it shows (Sleep)
One overlay at a time (`LAYERS`, `settings.layer`): Best spots, and the factors behind it as chips in the legend:
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
(Colour-blind colours: CLAUDE.md, "Owner's design rules". Side panel and "Here": skill `spot-card`.)
- Top bar: logo (pine + tent, inline SVG `<symbol id="logo">`, also the favicon), search, the four activity tabs (a second
  row on phones), clock chip, status, "★ My spots (n)", Settings.
- Map: tool strip (`.tools`, `setTool`: `inspect` = the spot button, click opens the spot card of the chosen kind, saving
  happens from the card; `near` = "Find spots here"; add future tools here) and a legend box. Legend: the colour bar is drawn as it
  looks on the grey map (`drawLegend` blends over map grey at the layer's opacity); a white marker (`setLegendMark`) shows
  the value under the cursor; the explanation folds away behind the title (`wn:legOpen`). Points of interest are dots
  up to zoom 12 and round icon badges from `BADGE_MIN_Z` (13).
  Below the tools: the locate button (`#btn-locate`, not a click mode). It starts `watchPosition`
  (blue dot); the first fix jumps to zoom `NEAR_Z` (12), where the drawn grid holds the whole 5 km circle.
  `findNearby()` runs after each redraw at that zoom and lists the best `NEAR_N` cells ≥ `NEAR_SEP`
  apart as numbered pins + "Best spots near you" in the panel. Tap again: back to you, then off.
  Tool `near` ("Find spots here"): a map click sets the search centre manually (`setNearAt(…, manual)`),
  shown as a draggable ✥ marker with a dashed 5 km circle; GPS updates don't move a manual centre,
  the locate button brings it back to you. First step towards GPX route planning (IDEAS.md 4).
  Messages for phones go through `showHint()` (the status text is hidden there).
- All sliders live in the Settings sheet (`#settings`): on desktop floating cards like the panel (the panel is hidden
  while it is open, `body.set-open`), full screen on phones. `makeSlider({label, hint, …})`: short label + quiet hint left,
  value fixed at the right (no moving bubble); `makeToggle(parent, label, key, onChange, hint)`; long explanations go
  into `<details class="more">` ("How it works").

## Slope icon (Topography)
The ground is called "Topography" in the UI: `topoIc(deg, hollow)` draws the ramp on the fly for every whole degree
(same base line always, drawn at 2× the real angle, max 40°; 0° = a flat line) or shows the cold hollow;
coloured green -> red by `slopeCol(deg)` (red from 15°).

## My spots (`#spots-win`, `renderSpotList`)
Opened by "★ My spots (n)" in the top bar (a full-screen sheet on phones). All saved spots, filters All / Sleep / Break /
Resupply, stars, where each is relative to the loaded route (`routeProj`: "in the route · km 41 · night 1" / "5 km from the
route"), click = fly there + open the card, **drag** the handle to reorder (pointer events, works on touch; the order is the
order of `spots`), tick box = `inRoute` (a fixed stop of the route). Buttons: "Route through N ticked spots, in this order"
(`routeThrough`), Export GPX, Backup, Import. Spots are stored only in `localStorage['wn:spots']`.
