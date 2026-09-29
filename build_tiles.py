#!/usr/bin/env python3
"""
Build WildNav offline tiles from a Geofabrik extract.

Usage:
    python build_tiles.py niedersachsen-latest.osm.pbf niedersachsen.poly [out_dir]

Writes (default out_dir = "data"):
    data/index.json              list of covered tiles (merged with an existing index)
    data/12/<x>/<y>.bin          building centres, Float32 pairs (tile-relative Mercator x, y)
    data/12/<x>/<y>.json         points of interest [{c, lat, lon, n}]

Same tile scheme and categories as wildnav-map.html (zoom-12 tiles).
Requires: pip install "osmium>=4"
"""
import json
import math
import os
import sys
import time
from array import array
from collections import defaultdict

import osmium

TILE_Z = 12
N = 1 << TILE_Z
KEYS = ('building', 'amenity', 'tourism', 'natural', 'leisure', 'man_made')


def classify(t):
    """Mirror of classify() in wildnav-map.html."""
    b, am, tour = t.get('building'), t.get('amenity'), t.get('tourism')
    nat, leis, mm = t.get('natural'), t.get('leisure'), t.get('man_made')
    if am == 'hunting_stand':
        return 'hunting'
    if am == 'shelter' or tour in ('wilderness_hut', 'alpine_hut'):
        return 'shelters'
    if nat == 'spring' or am == 'drinking_water' or mm == 'water_well':
        return 'water'
    if tour == 'camp_site':
        return 'campsites'
    if tour == 'viewpoint':
        return 'viewpoints'
    if leis == 'firepit' or am == 'bbq':
        return 'firepits'
    if b and b not in ('no', 'ruins'):
        return 'buildings'
    return None


def merc(lat, lon):
    """lat/lon -> Mercator coordinates in zoom-12 tile units."""
    r = math.radians(max(-85.0, min(85.0, lat)))
    x = (lon + 180.0) / 360.0 * N
    y = (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * N
    return x, y


def read_poly(path):
    """Read outer rings of a Geofabrik .poly boundary file (holes are ignored)."""
    rings, cur, hole = [], None, False
    with open(path, encoding='utf-8') as f:
        lines = [l.strip() for l in f][1:]          # first line is the name
    for l in lines:
        if not l:
            continue
        if cur is None:
            if l == 'END':
                break
            hole, cur = l.startswith('!'), []
        elif l == 'END':
            if not hole:
                rings.append(cur)
            cur = None
        else:
            lon, lat = map(float, l.split()[:2])
            cur.append((lon, lat))
    return rings


def inside(rings, lon, lat):
    hit = False
    for ring in rings:
        j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i]
            xj, yj = ring[j]
            if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                hit = not hit
            j = i
    return hit


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    pbf, poly = sys.argv[1], sys.argv[2]
    out = sys.argv[3] if len(sys.argv) > 3 else 'data'
    rings = read_poly(poly)

    buildings = defaultdict(lambda: array('f'))
    pois = defaultdict(list)
    t0, count = time.time(), 0

    # with_locations() stores node positions so way centres can be computed;
    # the key filter drops everything that is not relevant before it reaches Python.
    fp = (osmium.FileProcessor(pbf)
          .with_locations()
          .with_filter(osmium.filter.KeyFilter(*KEYS)))

    for o in fp:
        if o.is_node():
            if not o.location.valid():
                continue
            lat, lon = o.location.lat, o.location.lon
        elif o.is_way():
            s_lat = s_lon = 0.0
            k = 0
            for nd in o.nodes:
                if nd.location.valid():
                    s_lat += nd.location.lat
                    s_lon += nd.location.lon
                    k += 1
            if not k:
                continue
            lat, lon = s_lat / k, s_lon / k
        else:
            continue                                   # multipolygon relations skipped (rare for buildings)

        cat = classify(o.tags)
        if not cat:
            continue
        mx, my = merc(lat, lon)
        tx, ty = int(mx), int(my)
        if cat == 'buildings':
            a = buildings[(tx, ty)]
            a.append(mx - tx)
            a.append(my - ty)
        else:
            pois[(tx, ty)].append({'c': cat, 'lat': round(lat, 6), 'lon': round(lon, 6),
                                   'n': o.tags.get('name', '')})
        count += 1
        if count % 500_000 == 0:
            print(f'{count:,} objects  {time.time() - t0:.0f} s', flush=True)

    # Only tiles lying entirely inside the extract boundary count as covered
    # (all corners inside, no boundary vertex within the tile). Border tiles would
    # otherwise be partly empty; the app loads those from Overpass instead.
    lons = [p[0] for r in rings for p in r]
    lats = [p[1] for r in rings for p in r]
    x0, x1 = int(merc(0, min(lons))[0]), int(merc(0, max(lons))[0])
    y0, y1 = int(merc(max(lats), 0)[1]), int(merc(min(lats), 0)[1])
    tile_lon = lambda tx: tx / N * 360 - 180
    tile_lat = lambda ty: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / N))))
    verts = [p for r in rings for p in r]
    covered = []
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            w, e, n, s = tile_lon(tx), tile_lon(tx + 1), tile_lat(ty), tile_lat(ty + 1)
            if not all(inside(rings, lo, la) for lo in (w, e) for la in (n, s)):
                continue
            if any(w <= lo <= e and s <= la <= n for lo, la in verts):
                continue
            covered.append((tx, ty))

    total_b = 0
    for tx, ty in covered:
        d = os.path.join(out, str(TILE_Z), str(tx))
        os.makedirs(d, exist_ok=True)
        arr = buildings.get((tx, ty), array('f'))
        total_b += len(arr) // 2
        with open(os.path.join(d, f'{ty}.bin'), 'wb') as f:
            arr.tofile(f)
        with open(os.path.join(d, f'{ty}.json'), 'w', encoding='utf-8') as f:
            json.dump(pois.get((tx, ty), []), f, ensure_ascii=False)

    # Merge with an existing index so several regions can share one data folder.
    idx_path = os.path.join(out, 'index.json')
    tiles = set()
    if os.path.exists(idx_path):
        with open(idx_path, encoding='utf-8') as f:
            tiles.update(json.load(f).get('tiles', []))
    tiles.update(f'{x}/{y}' for x, y in covered)
    with open(idx_path, 'w', encoding='utf-8') as f:
        json.dump({'z': TILE_Z, 'built': time.strftime('%Y-%m-%d'), 'tiles': sorted(tiles)}, f)

    print(f'Done in {time.time() - t0:.0f} s: {len(covered)} tiles, {total_b:,} buildings, '
          f'{sum(len(v) for v in pois.values()):,} points of interest -> {out}/')


if __name__ == '__main__':
    main()
