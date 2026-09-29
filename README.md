# WildNav

A web map for planning wild camping and bikepacking trips. It shows building density
(places to stay away from) and how close useful places are: water sources, rivers and lakes,
shelters, camp sites, viewpoints and fire pits.

Live version: https://cobaltblau.github.io/WildNav/

Wild camping is not allowed everywhere. Check the local rules and respect private land.

## Run locally
Double-click `start.bat`, or run `python -m http.server 8765` and open http://localhost:8765/.

## Offline data
`data/` holds pre-built tiles made from [Geofabrik](https://download.geofabrik.de/) extracts:

    pip install "osmium>=4" pillow
    python build_tiles.py raw/<region>.osm.pbf raw/<region>.poly

Several regions can be built into the same `data/` folder. Outside those regions the app can
optionally download data from the Overpass API (off by default).

## Credits and licences
- Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), ODbL.
  The derived data in `data/` is also under the ODbL, see [data/LICENSE.md](data/LICENSE.md).
- [Leaflet](https://leafletjs.com/) (BSD-2-Clause), [SunCalc](https://github.com/mourner/suncalc)
  (BSD-2-Clause, sun and moon times), fonts Barlow / Barlow Semi Condensed
  (SIL Open Font License) via [Fontsource](https://fontsource.org/) and jsDelivr.
- Base maps: [OpenStreetMap](https://www.openstreetmap.org/) (Grey and Streets),
  [OpenTopoMap](https://opentopomap.org/) (CC-BY-SA), and
  [Sentinel-2 cloudless](https://s2maps.eu) by EOX IT Services GmbH (CC BY-NC-SA 4.0).
- Search by [Nominatim](https://nominatim.org/), optional downloads via the
  [Overpass API](https://overpass-api.de/).
