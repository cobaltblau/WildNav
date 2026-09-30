# WildNav

A web map for planning wild camping and bikepacking trips. It shows building density
(places to stay away from) and how close useful places are: water sources, rivers and lakes,
shelters, camp sites, viewpoints and fire pits.

Live version: https://cobaltblau.github.io/WildNav/

Wild camping is not allowed everywhere. Check the local rules and respect private land.

## On your phone
Open the live version in Safari (iPhone) or Chrome (Android) and add it to the home screen.
It then works offline for everything you've viewed; under Settings → "Offline use" you can
store a whole area before a trip.

## Run locally
Double-click `start.bat`, or run `python -m http.server 8765` and open http://localhost:8765/.

## Offline data
`data/` holds pre-built tiles made from [Geofabrik](https://download.geofabrik.de/) extracts:

    pip install "osmium>=4" pillow numpy
    python build_tiles.py raw/<region>.osm.pbf raw/<region>.poly
    python build_tiles.py --slope raw/fabdem      # slopes, from FABDEM tiles in raw/fabdem/

The generated data is not kept in the `main` branch: `python publish_site.py` publishes the app and
`data/` to the `gh-pages` branch (one commit, replaced each time), which GitHub Pages serves.

Several regions can be built into the same `data/` folder. Outside those regions the app has no data.

## Credits and licences
- Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), ODbL.
  The derived data in `data/` is also under the ODbL, see [data/LICENSE.md](data/LICENSE.md).
- [Leaflet](https://leafletjs.com/) (BSD-2-Clause), [SunCalc](https://github.com/mourner/suncalc)
  (BSD-2-Clause, sun and moon times), fonts Barlow / Barlow Semi Condensed
  (SIL Open Font License) via [Fontsource](https://fontsource.org/) and jsDelivr.
- Slopes: [FABDEM V1-2](https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn) (Hawker et al. 2022,
  University of Bristol), CC BY-NC-SA 4.0 (non-commercial), based on the Copernicus DEM
  (© DLR e.V. 2010-2014, © Airbus Defence and Space GmbH 2014-2018). The slope maps in `data/` are
  under the same licence, see [data/LICENSE.md](data/LICENSE.md).
- Base maps: [OpenStreetMap](https://www.openstreetmap.org/) (Grey and Streets),
  [OpenTopoMap](https://opentopomap.org/) (CC-BY-SA), and
  [Sentinel-2 cloudless](https://s2maps.eu) by EOX IT Services GmbH (CC BY-NC-SA 4.0).
- Road noise levels calibrated against the DLR *Road Traffic Noise (AI Prediction) - Germany, 2017*
  ([Noise2NAKO](https://geoservice.dlr.de/web/datasets/n2nnoise_ai), Staab et al. 2025, CC BY 4.0);
  the DLR data itself is not included.
- Weather forecast by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0), fetched only for an opened spot.
- Search by [Nominatim](https://nominatim.org/).
