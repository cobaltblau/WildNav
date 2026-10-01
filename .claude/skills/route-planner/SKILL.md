---
name: route-planner
description: "WildNav Route tab in index.html. The route window (rwin), corridor or exact GPX route (ensureCorridor, parseRoute, geocode, DETOUR), trip planning (planTrip, planSupply, planNights, planBreaks, finishPlan, loadProfile, fillWeather, scanPiece, candidates, buildGrid), nights and backups, breaks every N hours, the timeline (drawTimeline, selectStop, dragNight, tripStart, passTime), the bikerouter.de link and GPX export of a plan. Use before changing route planning, the timeline or the route window."
---

# WildNav Route tab (index.html)

## Route window (Route tab, `#rwin`)
A collapsible window along the bottom (a sheet on phones; `--rw` = its height, the panel, legend and map controls move up).
The loaded route stays on the map in every tab (faded, `drawRoute`); the pins of the plan show in the tab of their kind.
- **Route** (`route`, `localStorage['wn:route']` = {name, pts, exact}; plan in `wn:plan`): either a **corridor** (straight
  lines From → (via spots) → To or Direction + total km, points every ~1 km, all distances × `DETOUR` = 1.3, `exact:false`,
  `ensureCorridor`; start / destination by place name (`geocode`, Nominatim) or by pin on the map) or the **exact track** of a
  GPX (`parseRoute`, thinned to ~25 m; it replaces the corridor and re-plans if there already was a plan). Old saved routes
  count as exact.
- **Plan** (`planTrip`): `planSupply` (place files along the track → stops per village ≤ 1.5 km apart, `open` = some shop or café
  looks open when you pass), `planNights`, `planBreaks`, `finishPlan` (days, longest stretch without open food > 30 km),
  `loadProfile` (elevations from the slope tiles, blue channel × 4 m), `fillWeather` (Open-Meteo per night, same cache as the
  spot card). Ticked saved spots are **fixed**: saved nights (> 2 km from start / end) split the trip and each part gets
  `round(length / km per day) − 1` generated nights (best camp cell in a window around the target km, plus a **backup** ≥ 1.5 km
  away); breaks every `settings.breakEvery` hours of riding, counted again each day (best break cell, shade at the time you
  pass), skipped when a ticked break is within 0.3 × the interval. Scoring is `scanPiece` / `candidates`: off-screen
  6 km pieces (`ROUTE_STEP`), `buildGrid` at `NEAR_Z`, night (`scorer`, ≤ 2 km off a GPX, 5 km off a corridor) and break
  (`breakScorer`, ≤ 200 m / 1 km) cells share one grid, best per km, cached per piece (`scan`) so swapping a stop doesn't rescan.
- **Timeline** (`drawTimeline`): km left to right, one box per day (km, ↑ climb, arrival vs sunset from SunCalc, late = red
  when within 30 min of sunset), elevation profile, tents with score + weather, break / shop icons with closed ones grey;
  stops in lanes so they never overlap (up to 3, horizontal scroll on phones); saved = ★, generated = dashed ring.
  Hover highlights on the map (`hi`); click a stop (`selectStop`): generated → Keep (save) / Other spot (`alts`), saved →
  Take out of the route; drag a generated night (`dragNight`, `plan.moved`). Times: `tripStart()` (`settings.rideDay` /
  `rideStart`), `passTime(km)` = day start + riding time + 30 min per break.
- Buttons: Plan · bikerouter.de (`#map=9/LAT/LON/standard&lonlats=lon,lat;…` with start, nights, saved break / resupply stops,
  destination; max 40 points) · Load GPX · Export GPX (track + stops as waypoints) · Clear.
- `buildGrid(z, S, x0, y0, gw, gh)` is the view-independent grid builder (`computeBase` uses it for the view).

## Mockup in `mockups/`
- `route_concept.html`: the four activities (Sleep · Break · Resupply · Route), "My spots" list and the route window
  with a timeline mixing saved and generated stops (IDEAS.md section 7); it is now built into the app.
