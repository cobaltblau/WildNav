# WildNav – feature ideas

Ideas that are not built yet. Each one should stay compatible with the rules in CLAUDE.md
(static site, heavy work done in `build_tiles.py`, results stored in `data/`).

---

## 1. "How well hidden is this spot?" (hidden score)

**Goal:** a second part of the spot score that says how likely it is that someone on a road,
path or in a house **sees or hears** you. It is added to the "Best spots" map as a factor
("Stay hidden" slider), and can also be shown as its own layer.

### 1.1 What decides it

| Factor | Effect | Where the data comes from |
|---|---|---|
| Who could notice you | Roads, tracks, paths, buildings, railways, car parks. Weighted by how busy they are (motorway ≫ path; path at night ≈ empty) | OSM `highway=*`, `railway=*`, buildings (already in data) |
| Sight blocking | Forest, scrub, hedges, tree rows, walls block the view; fields, meadows, water do not | OSM `landuse=forest`, `natural=wood/scrub`, `barrier=hedge`, `natural=tree_row` |
| Sight distance | How far a person or tent can be noticed | Rough values below |
| Sound | Your voice/cooking vs. distance to the listener; forest adds a little damping | ISO 9613-2 (below) |
| Terrain (later) | Hills and dips hide you completely | Digital elevation model (DEM) |
| Season / time (later) | Deciduous forest is see-through in winter; at night only lights matter | OSM `leaf_type`, a day/night switch |

### 1.2 How far can you see and hear? (rough values, to be checked)

**Seeing**
- **Open field:** a person or tent is easy to notice from several hundred metres, and still
  visible from more than 1 km. At night a headlamp or campfire can be seen from several km.
  → Open land gives practically no cover.
- **Forest:** the horizontal view is only **tens of metres**. Dense young stands and spruce
  thickets let you see about 10–30 m; open old forest (beech, pine) with little undergrowth
  about 50–150 m. Deciduous forest in winter is much more open. Research on "horizontal
  visibility in forests" confirms the view depends on how many trees there are per hectare,
  how thick they are and how much undergrowth there is (see sources).
- **Hedge / tree row:** a single dense hedge blocks the view almost completely, but only
  directly behind it.
- **Model:** treat each land type as a "fog" with a **sight blocking per metre** `k`. How much
  view gets through is `T = exp(−Σ k·s)`, where `s` is the length of each land type the line
  of sight crosses. Example values to start with:
  open 0, crops 0.002 (summer), scrub 0.03, open forest 0.02, dense forest 0.05, hedge 0.5
  (a 5 m hedge lets through `exp(−2.5)` ≈ 8 %). With forest 0.03/m, only about 5 % of the view
  gets through 100 m of forest.

**Hearing** (ISO 9613-2, sound spreading outdoors)
- **Spreading:** from a point source the level drops by **6 dB for every doubling of
  distance**. Air absorbs a little extra, which only matters for high tones over long distances.
- **Your noise:** normal talking is about 60 dB(A) at 1 m, raised voice about 70, shouting about
  85. A quiet rural night is about 25–35 dB(A). Normal talking therefore stays audible for
  roughly **30–100 m**, shouting for several hundred metres.
- **Forest damping (ISO 9613-2, table A.1):** 10–20 m of foliage gives 0–3 dB. From 20 to 200 m,
  per metre, by frequency: 63 Hz 0.02 · 125 Hz 0.03 · 250 Hz 0.04 · 500 Hz 0.05 · 1 kHz 0.06 ·
  2 kHz 0.08 · 4 kHz 0.09 · 8 kHz 0.12 dB/m (the standard counts at most 200 m).
  For speech (≈ 0.05–0.06 dB/m), 100 m of forest gives only **≈ 5–6 dB**. That shortens the
  hearing distance by about 45 %, but not more.
- **Conclusion:** for sound, **distance to paths and houses** matters most. Forest helps a
  little. A simple model is enough: `level = L_you − 20·log10(d) − 0.05·(metres of forest)`.
- **The other direction (comfort):** road noise that *you* hear (motorway ~75–80 dB at 25 m,
  a line source: −3 dB per doubling of distance) carries for kilometres. That is a separate
  "Quiet night" factor and is easy to add with the same machinery.

> Check before building: the exact table values from ISO 9613-2 Annex A, and sight distances
> for Central European forest types (e.g. in the papers listed below).

### 1.3 Algorithm (efficient, precalculated)

The expensive part is the lines of sight: for each spot, how much forest lies between it and
every path nearby? Tracing thousands of rays per spot would be far too slow. A **sweep along
a small number of directions** gives the same answer in linear time.

1. **Grid per tile:** a zoom-12 tile (~6 km) with 10 m cells = 600 × 600 cells, plus a margin
   of ~500 m (the reach of sight and sound) so the tile edges are correct → ~700 × 700 cells.
2. **Rasterise from OSM:**
   - `source[c]` = how often someone passes by (path 0.3, track 0.2, minor road 1, main road 3,
     house 1, …);
   - `k_sight[c]` and `k_sound[c]` = blocking per metre in that cell.
3. **Directional sweep (the key trick).** For each of D directions (16–32), walk through the
   grid in that direction and carry an "exposure" value from cell to cell:

       E[c] = E[previous] · exp(−k[c]·step) · exp(−step / L) + source[c]

   This is a running filter: every source sends its "being seen" along the ray, and the ray
   gets weaker behind forest (`k`) and with distance (`L` ≈ 300 m for sight, ~100 m for talking
   voices). Summed over all directions, `E[c]` ≈ how exposed cell c is. The cost is
   **O(cells × D)**, independent of how many paths there are.
   - Diagonal directions: shear the grid (shift each row) so every sweep runs along rows. This
     keeps it fast with NumPy (a whole row at once) or Numba.
   - Sound uses the same sweep with `k_sound` and its own `L`. It's cheap to run both.
4. **Terrain (phase 2):** in the same sweep, also carry the "highest slope seen so far" (the
   horizon). A cell below the horizon of a source is completely hidden. This is the well-known
   directional horizon/viewshed method, just as fast.
5. **Hidden score:** `hidden = exp(−E / E0)`, stored as 0–255.
6. **Parallel precalculation:** each tile is independent (thanks to the margin), so
   `build_tiles.py` can hand the tiles to all CPU cores (`ProcessPoolExecutor`, as it already
   does for regions).
   Estimate: ~700² cells × 32 directions × 2 (sight + sound) ≈ 30 million steps per tile. That's
   about 0.1–0.5 s with NumPy, and 2,343 tiles on 24 cores take **about 1 minute**.
7. **Output:** `data/12/x/y.hide.png`, a greyscale PNG at 20 m resolution (300 × 300). Browsers
   decode PNGs natively and the files are small, maybe 5–30 KB per tile (≈ 20–60 MB in total).
   Optionally store sight and sound as the red and green channels of one PNG.
8. **App:** decode the PNG into the render grid (it's already in Mercator tile coordinates),
   then multiply into `scorer()`: `score *= 1 − wHide·(1 − hidden)`. Add a "Stay hidden" slider,
   an optional "Hidden" layer, and a hover row "Hidden: 80 % (seen from path 140 m away,
   120 m forest between)".

### 1.4 Phases

1. **v1 (OSM only):** sight + sound from roads, paths and houses, with forest/scrub/hedges
   blocking the view. No terrain. This already covers most of the value in flat areas like
   Lower Saxony.
2. **v2 terrain:** add a DEM, e.g. Copernicus DEM GLO-30 (30 m, free with attribution). Hills
   matter in NRW (Sauerland, Eifel).
3. **v3 better forest data:** Copernicus High Resolution Layer "Tree Cover Density" (10–20 m,
   free) instead of OSM forest outlines. It shows real density, and gaps inside forests.
4. **v4 season / night:** a summer/winter switch (deciduous forest more open in winter); at
   night count only lit places and light that can be seen.

### 1.5 Open questions
- How to weight paths without usage data? Maybe from the OSM tags `highway` class,
  `sac_scale`, `surface`, or nearby car parks (hiking car parks mean busy paths).
- Licences: Copernicus data is free, but needs to be credited in the README/footer (like ODbL).
- Data size: 20 m resolution might be too coarse for hedges → possibly 10 m with WebP/PNG.

---

## 2. Saved spots

- **Click on the map → "Save spot"**, with a name, a note and a star rating. Stored in the
  browser (`localStorage`/IndexedDB), so no server is needed.
- **List in the panel:** jump to a spot, rename, delete, sort by score.
- **Export / import** as GPX (for Garmin, Komoot, OsmAnd) and as JSON (backup, moving to
  another device).
- **Share link:** the spot is stored in the URL (`#spot=52.31,8.12,Name`). Anyone with the link
  sees the same spot and its breakdown, still without a server.
- Note: browser storage can be lost (clearing browser data, private window) → remind people to
  export regularly.

## 3. Spot detail card ("breakdown")

Opens when you click a spot (saved or not). Everything is computed in the browser from the data
that's already loaded, plus one small library.

| Section | Content | How |
|---|---|---|
| Score | Spot score and the part each factor contributes ("houses −40 %, water +25 %") | `scorer()`, split into its parts |
| Nearest things | House, hunting stand, water source, river/lake, shelter, path/road: **distance + compass direction** ("💧 spring 320 m NE") | Distance fields + nearest point; bearing from lat/lon |
| Sun | Sunset/sunrise time, **direction** (azimuth) and golden hour; an arrow on the map | [SunCalc](https://github.com/mourner/suncalc) (BSD, via CDN), runs offline |
| Sun blocked? | Is the evening/morning sun hidden by forest or hills in that direction? | Look along the sun direction in the forest grid; later the DEM horizon (see 1.3 step 4) |
| Moon | Phase, moonrise/moonset (dark nights = less visible, stars) | SunCalc |
| Terrain (with DEM) | Height, slope (flat enough for a tent?), valley bottom = cold air and dew | Copernicus DEM, precomputed |
| Ground | Forest, meadow, field, scrub, wetland, from OSM land use | New per-tile land-use raster |
| Rules | ⚠ Nature reserve / national park / water protection area, where camping is often forbidden | OSM `boundary=protected_area`, `leisure=nature_reserve` → warning layer |
| Weather | Forecast for the next nights: rain, wind, minimum temperature | [Open-Meteo](https://open-meteo.com/) (free, no key, CC BY 4.0), fetched only on click |

## 4. Trip planning along a route (bikepacking)

- **Import a GPX route** → show the best spots within X km of the route, ranked
  ("every ~80 km, a spot in the evening").
- **Daily stages:** enter km per day → suggest one good spot near each day's end point.
- **Supply points along the route:** supermarkets, bakeries, water taps, bike shops
  (OSM `shop=*`, `amenity=drinking_water`), with opening hours if tagged.

## 5. More factors for the score

- **Protected areas** as a hard "no" (or a strong minus); see the table above. Probably the most
  important addition for legal reasons.
- **Quiet night:** road/railway noise *you* hear (same sweep as in 1.3, looked at the other way round).
- **Mosquitoes:** a slight minus right next to standing water (ponds, marsh) in summer.
- **Wind shelter:** forest edges on the windward side (with the weather forecast's wind direction).
- **Access:** distance to the nearest track/path, since you need to get there with a bike.

## 6. App quality

- **Works offline (PWA):** a service worker caches the app and the tiles you've viewed, so the
  map works without mobile signal in the forest. It stays a static site, so it works on GitHub Pages.
- **Mobile layout:** the panel as a drawer that slides up from the bottom, with bigger buttons.
- ✅ **Locate me:** GPS button, "best spots within 5 km of here".
- **Search:** also search saved spots.

### Suggested order
1. ✅ Saved spots + detail card (distances, directions, sun), plus terrain at the spot.
2. ✅ Protected-area warning (nature reserves 0 %, landscape protection 50 %). Note: landscape
   protection areas are only patchily mapped in OSM (106 vs. 4,641 nature reserves in NS + NRW).
3. PWA offline + mobile layout → makes it usable on the road.
4. Hidden score (section 1) and DEM-based terrain factors.
5. GPX route planning and weather.

---

### Sources (section 1)
- ISO 9613-2:1996, *Acoustics – Attenuation of sound during propagation outdoors*, Annex A
  (foliage), e.g. [public copy](https://puc.sd.gov/commission/dockets/electric/2019/el19-003/KMExhibit9.pdf)
- [Arup Strutt – dense foliage excess attenuation (ISO 9613-2 and Hoover method)](https://strutt.arup.com/help/Environmental_Noise/EnviroFoliageAtten.htm)
- [Horizontal Visibility in Forests (Remote Sensing 13(21), 2021)](https://doi.org/10.3390/rs13214455)
- [How Far Can You See in a Forest?](https://www.researchgate.net/publication/281808611_How_Far_Can_You_See_in_a_Forest)
- [Forest landscape shield models for assessing audio-visual shielding (J. Environ. Management, 2024)](https://www.sciencedirect.com/science/article/pii/S0301479724000562) – closely related, worth reading before building
- [The technique of distance decayed visibility for forest landscape visualization](https://www.tandfonline.com/doi/abs/10.1080/13658810500104880)
