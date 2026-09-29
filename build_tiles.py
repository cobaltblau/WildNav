#!/usr/bin/env python3
"""
Build WildNav offline tiles from a Geofabrik extract.

Usage:
    python build_tiles.py niedersachsen-latest.osm.pbf niedersachsen.poly [out_dir]

Writes (default out_dir = "data"):
    data/index.json              list of covered tiles (merged with an existing index)
    data/12/<x>/<y>.bin          building centres, Float32 pairs (tile-relative Mercator x, y)
    data/12/<x>/<y>.json         points of interest [{c, lat, lon, n}]
    data/12/<x>/<y>.water.bin    rivers, streams and lake shores sampled every ~40 m,
                                 Uint16 pairs (tile-relative Mercator x, y scaled to 0..65535)

Same tile scheme and categories as index.html (zoom-12 tiles).
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
KEYS = ('building', 'amenity', 'tourism', 'natural', 'leisure', 'man_made', 'waterway')

# Surface water. Ditches/drains are left out: very common, often dry or polluted.
WATERWAYS = {'river', 'stream', 'canal'}
AREA_WATER_EXCLUDE = {'wastewater', 'basin', 'reflecting_pool', 'fountain', 'moat'}
WATER_STEP_M = 40            # sample spacing along lines and shores
WATER_GRID = 300             # de-duplicate samples on a 300x300 grid per tile (~20 m)
Q = 65535                    # Uint16 scale for water points


def classify(t):
    """Mirror of classify() in index.html."""
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


def is_water_line(t):
    return (t.get('waterway') in WATERWAYS and t.get('intermittent') != 'yes'
            and t.get('tunnel') not in ('yes', 'culvert'))


def is_water_area(t):
    if t.get('intermittent') == 'yes':
        return False
    if t.get('waterway') == 'riverbank':
        return True
    return t.get('natural') == 'water' and t.get('water') not in AREA_WATER_EXCLUDE


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


def sample_line(nodes, water):
    """Add points every WATER_STEP_M along a node list to water[(tx, ty)] (a set of grid cells)."""
    ll = [(n.location.lat, n.location.lon) for n in nodes if n.location.valid()]
    if not ll:
        return
    pts = [merc(lat, lon) for lat, lon in ll]
    # metres per zoom-12 tile unit at this latitude (constant enough within one feature)
    m_per_unit = 40075016.686 * math.cos(math.radians(ll[0][0])) / N
    step = WATER_STEP_M / m_per_unit
    add = lambda x, y: water[(int(x), int(y))].add((int((x - int(x)) * WATER_GRID), int((y - int(y)) * WATER_GRID)))
    add(*pts[0])
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        d = math.hypot(x1 - x0, y1 - y0)
        k = int(d / step)
        for i in range(1, k + 1):
            f = i / (k + 1)
            add(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f)
        add(x1, y1)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    pbf, poly = sys.argv[1], sys.argv[2]
    out = sys.argv[3] if len(sys.argv) > 3 else 'data'
    rings = read_poly(poly)

    buildings = defaultdict(lambda: array('f'))
    pois = defaultdict(list)
    water = defaultdict(set)                       # (tx, ty) -> {(gx, gy)} on a WATER_GRID grid
    t0, count, n_lines, n_areas = time.time(), 0, 0, 0

    # with_locations() stores node positions so way centres can be computed;
    # with_areas() assembles lake polygons (also multipolygon relations) for water areas only;
    # the key filter drops everything that is not relevant before it reaches Python.
    water_tags = (('natural', 'water'), ('waterway', 'riverbank'))
    fp = (osmium.FileProcessor(pbf)
          .with_locations()
          .with_areas(osmium.filter.TagFilter(*water_tags))
          .with_filter(osmium.filter.KeyFilter(*KEYS))
          .with_filter(osmium.filter.TagFilter(*water_tags).enable_for(osmium.osm.AREA)))

    for o in fp:
        if o.is_area():
            if is_water_area(o.tags):
                for outer in o.outer_rings():
                    sample_line(outer, water)
                    for inner in o.inner_rings(outer):
                        sample_line(inner, water)
                n_areas += 1
            continue
        if o.is_node():
            if not o.location.valid():
                continue
            lat, lon = o.location.lat, o.location.lon
        elif o.is_way():
            if is_water_line(o.tags):
                sample_line(o.nodes, water)
                n_lines += 1
                continue
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

    total_b = total_w = total_p = 0
    for tx, ty in covered:
        d = os.path.join(out, str(TILE_Z), str(tx))
        os.makedirs(d, exist_ok=True)
        arr = buildings.get((tx, ty), array('f'))
        total_b += len(arr) // 2
        with open(os.path.join(d, f'{ty}.bin'), 'wb') as f:
            arr.tofile(f)
        p = pois.get((tx, ty), [])
        total_p += len(p)
        with open(os.path.join(d, f'{ty}.json'), 'w', encoding='utf-8') as f:
            json.dump(p, f, ensure_ascii=False)
        wa = array('H')
        for gx, gy in sorted(water.get((tx, ty), ())):
            wa.append(round((gx + 0.5) / WATER_GRID * Q))
            wa.append(round((gy + 0.5) / WATER_GRID * Q))
        total_w += len(wa) // 2
        with open(os.path.join(d, f'{ty}.water.bin'), 'wb') as f:
            wa.tofile(f)

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
          f'{total_p:,} points of interest, {total_w:,} water points '
          f'({n_lines:,} rivers/streams, {n_areas:,} lakes) -> {out}/')


if __name__ == '__main__':
    main()
