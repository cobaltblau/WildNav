#!/usr/bin/env python3
"""
Build WildNav offline tiles from one or more Geofabrik extracts.

Usage:
    python build_tiles.py region.osm.pbf region.poly [other.osm.pbf other.poly ...] [out_dir]

Neighbouring regions should be built in one run: tiles on their shared border are then
filled from both extracts (without duplicates) instead of being left out.

Writes (default out_dir = "data"):
    data/index.json              list of covered tiles (merged with an existing index)
    data/12/<x>/<y>.bin          building centres, Float32 pairs (tile-relative Mercator x, y)
    data/12/<x>/<y>.json         points of interest [{c, lat, lon, n}]
    data/12/<x>/<y>.water.bin    rivers, streams and lake shores sampled every ~40 m,
                                 Uint16 pairs (tile-relative Mercator x, y scaled to 0..65535)
    data/12/<x>/<y>.land.png     terrain map, 512x512 greyscale PNG (~12 m per pixel);
                                 each pixel value is a LAND class code (0 = not mapped)

Same tile scheme and categories as index.html (zoom-12 tiles).
Requires: pip install "osmium>=4" pillow
"""
import io
import json
import math
import os
import sys
import time
from array import array
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

import osmium
from PIL import Image, ImageDraw

TILE_Z = 12
N = 1 << TILE_Z
KEYS = ('building', 'amenity', 'tourism', 'natural', 'leisure', 'man_made', 'waterway', 'landuse')
AREA_KEYS = ('landuse', 'natural', 'waterway', 'leisure')   # areas that can become terrain

# Terrain classes. The code is also the drawing order: higher codes are drawn on top
# (a pond inside a forest ends up as water). Mirror of LAND in index.html.
LAND = {1: 'Field / farmland', 2: 'Meadow / grassland', 3: 'Orchard, vineyard or allotments',
        4: 'Heath', 5: 'Scrub', 6: 'Forest (broadleaved)', 7: 'Forest (conifer)', 8: 'Forest (mixed)',
        9: 'Park or sports ground', 10: 'Sand, beach or rock', 11: 'Quarry, landfill or construction',
        12: 'Built-up area / farmyard', 13: 'Cemetery', 14: 'Wetland / marsh', 15: 'Military area', 16: 'Water'}
LAND_PX = 512                                # terrain pixels per tile side
LAND_WATER = 16

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


def land_class(t):
    """Terrain class code of an area (see LAND), or 0."""
    lu, nat, ww, le = t.get('landuse'), t.get('natural'), t.get('waterway'), t.get('leisure')
    if nat == 'water' or ww == 'riverbank' or lu in ('reservoir', 'basin'):
        return 16
    if lu == 'military':
        return 15
    if nat == 'wetland':
        return 14
    if lu == 'cemetery':
        return 13
    if lu in ('residential', 'industrial', 'commercial', 'retail', 'farmyard', 'railway', 'garages'):
        return 12
    if lu in ('quarry', 'landfill', 'construction', 'brownfield'):
        return 11
    if nat in ('sand', 'beach', 'bare_rock', 'scree', 'shingle'):
        return 10
    if le in ('park', 'pitch', 'golf_course', 'sports_centre', 'playground') or lu == 'recreation_ground':
        return 9
    if lu == 'forest' or nat == 'wood':
        return {'broadleaved': 6, 'needleleaved': 7}.get(t.get('leaf_type'), 8)
    if nat == 'scrub':
        return 5
    if nat == 'heath':
        return 4
    if lu in ('orchard', 'vineyard', 'allotments', 'plant_nursery'):
        return 3
    if lu in ('meadow', 'grass', 'village_green') or nat == 'grassland':
        return 2
    if lu == 'farmland':
        return 1
    return 0


def ring_merc(nodes):
    """Node list -> flat array('d') of Mercator x, y in zoom-12 tile units."""
    a = array('d')
    for n in nodes:
        if n.location.valid():
            a.extend(merc(n.location.lat, n.location.lon))
    return a


def add_land(land, owner, ri, key, code, rings, is_line=False):
    """Register a terrain area (outer ring + inner rings) or a river line with every
    covered tile it touches. Shared border tiles keep the OSM id for de-duplication."""
    outer = rings[0]
    if len(outer) < 4:
        return
    xs, ys = outer[0::2], outer[1::2]
    for tx in range(int(min(xs)), int(max(xs)) + 1):
        for ty in range(int(min(ys)), int(max(ys)) + 1):
            own = owner.get((tx, ty))
            if own == ri or own == -1:
                land[(tx, ty)].append((code, is_line, rings, key if own == -1 else None))


def draw_land(tx, ty, items):
    """Rasterise terrain items of one tile into a PNG (bytes)."""
    img = Image.new('L', (LAND_PX, LAND_PX), 0)
    draw = ImageDraw.Draw(img)
    px = lambda a: [((a[i] - tx) * LAND_PX, (a[i + 1] - ty) * LAND_PX) for i in range(0, len(a), 2)]
    for code, is_line, rings, _ in sorted(items, key=lambda it: it[0]):
        if is_line:
            draw.line(px(rings[0]), fill=code, width=1)
        elif len(rings) == 1:
            draw.polygon(px(rings[0]), fill=code)
        else:                                          # holes: draw through a mask limited to the bounding box
            pts = [px(r) for r in rings]
            x0 = max(0, int(min(p[0] for p in pts[0]))); x1 = min(LAND_PX, int(max(p[0] for p in pts[0])) + 1)
            y0 = max(0, int(min(p[1] for p in pts[0]))); y1 = min(LAND_PX, int(max(p[1] for p in pts[0])) + 1)
            if x1 <= x0 or y1 <= y0:
                continue
            mask = Image.new('1', (x1 - x0, y1 - y0), 0)
            md = ImageDraw.Draw(mask)
            for j, p in enumerate(pts):
                md.polygon([(x - x0, y - y0) for x, y in p], fill=0 if j else 1)
            img.paste(code, (x0, y0, x1, y1), mask)
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


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


def tile_lon(tx):
    return tx / N * 360 - 180


def tile_lat(ty):
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / N))))


def plan_tiles(regions):
    """Decide which region fills which tile.

    owner[tile] = i   tile lies entirely inside region i (all corners inside, no boundary
                      vertex within the tile) -> filled from that extract only
    owner[tile] = -1  tile is not inside a single region but inside their union
                      (checked on a 9x9 grid) -> filled from all extracts, de-duplicated
    Tiles outside are not covered; the app can load those from Overpass instead.
    """
    lons = [p[0] for r in regions for ring in r for p in ring]
    lats = [p[1] for r in regions for ring in r for p in ring]
    x0, x1 = int(merc(0, min(lons))[0]), int(merc(0, max(lons))[0])
    y0, y1 = int(merc(max(lats), 0)[1]), int(merc(min(lats), 0)[1])
    verts = [[p for ring in r for p in ring] for r in regions]
    in_any = lambda lo, la: any(inside(r, lo, la) for r in regions)
    owner = {}
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            w, e, n, s = tile_lon(tx), tile_lon(tx + 1), tile_lat(ty), tile_lat(ty + 1)
            if not all(in_any(lo, la) for lo in (w, e) for la in (n, s)):
                continue
            for i, r in enumerate(regions):
                if (all(inside(r, lo, la) for lo in (w, e) for la in (n, s))
                        and not any(w <= lo <= e and s <= la <= n for lo, la in verts[i])):
                    owner[(tx, ty)] = i
                    break
            else:
                if len(regions) > 1 and all(in_any(tile_lon(tx + a / 8), tile_lat(ty + b / 8))
                                            for a in range(9) for b in range(9)):
                    owner[(tx, ty)] = -1
    return owner


def read_region(ri, pbf, owner):
    """Read one extract (runs in a worker process). Keeps only objects for tiles that
    region ri fills: tiles it owns, plus shared border tiles (returned with their OSM ids,
    so the parent can drop objects that appear in two extracts)."""
    b = defaultdict(lambda: array('f'))            # owned tiles: building centres
    p = defaultdict(list)                          # owned tiles: points of interest
    sb = defaultdict(list)                         # shared tiles: (id, fx, fy)
    sp = defaultdict(list)                         # shared tiles: (id, poi)
    water = defaultdict(set)                       # (tx, ty) -> {(gx, gy)}; sets drop overlap duplicates
    t0, count, n_lines, n_areas = time.time(), 0, 0, 0
    name = os.path.basename(pbf)
    print(f'Reading {name}', flush=True)

    land = defaultdict(list)                       # (tx, ty) -> [(code, is_line, rings, id or None)]

    # with_locations() stores node positions so way centres can be computed;
    # with_areas() assembles polygons (also multipolygon relations) for terrain and lakes;
    # the key filters drop everything that is not relevant before it reaches Python.
    fp = (osmium.FileProcessor(pbf)
          .with_locations()
          .with_areas(osmium.filter.KeyFilter(*AREA_KEYS))
          .with_filter(osmium.filter.KeyFilter(*KEYS))
          .with_filter(osmium.filter.KeyFilter(*AREA_KEYS).enable_for(osmium.osm.AREA)))

    for o in fp:
        if o.is_area():
            tags = o.tags
            if is_water_area(tags):
                for outer in o.outer_rings():
                    sample_line(outer, water)
                    for inner in o.inner_rings(outer):
                        sample_line(inner, water)
                n_areas += 1
            code = land_class(tags)
            if code:
                for outer in o.outer_rings():
                    rings = [ring_merc(outer)] + [ring_merc(inner) for inner in o.inner_rings(outer)]
                    add_land(land, owner, ri, ('a', o.id), code, rings)
            continue
        if o.is_node():
            if not o.location.valid():
                continue
            lat, lon = o.location.lat, o.location.lon
        elif o.is_way():
            if is_water_line(o.tags):
                sample_line(o.nodes, water)
                n_lines += 1
                if o.tags.get('waterway') in ('river', 'canal'):     # wide enough to show on the terrain map
                    add_land(land, owner, ri, ('w', o.id), LAND_WATER, [ring_merc(o.nodes)], is_line=True)
                continue
            if not classify(o.tags):                   # e.g. landuse ways: only needed as areas
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
        own = owner.get((tx, ty))
        if own is None or (own >= 0 and own != ri):
            continue                                   # not covered, or filled by another extract
        poi = None if cat == 'buildings' else {'c': cat, 'lat': round(lat, 6), 'lon': round(lon, 6),
                                                'n': o.tags.get('name', '')}
        if own == -1:
            key = (o.is_way(), o.id)
            if poi is None:
                sb[(tx, ty)].append((key, mx - tx, my - ty))
            else:
                sp[(tx, ty)].append((key, poi))
        elif poi is None:
            a = b[(tx, ty)]
            a.append(mx - tx)
            a.append(my - ty)
        else:
            p[(tx, ty)].append(poi)
        count += 1
        if count % 1_000_000 == 0:
            print(f'{name}: {count:,} objects  {time.time() - t0:.0f} s', flush=True)

    # Terrain: owned tiles are drawn here (in this worker); shared border tiles are returned
    # as raw items, because the parent has to merge them with the other extract first.
    land_png, land_shared = {}, {}
    for t, items in land.items():
        if owner.get(t) == ri:
            land_png[t] = draw_land(t[0], t[1], items)
        else:
            land_shared[t] = items
    print(f'{name}: finished in {time.time() - t0:.0f} s (terrain for {len(land_png)} tiles)', flush=True)
    return {'b': dict(b), 'p': dict(p), 'sb': dict(sb), 'sp': dict(sp),
            'w': {t: s for t, s in water.items() if t in owner}, 'lines': n_lines, 'areas': n_areas,
            'land': land_png, 'land_shared': land_shared}


def main():
    args = sys.argv[1:]
    pairs = [(a, b) for a, b in zip(args[::2], args[1::2]) if a.endswith('.pbf') and b.endswith('.poly')]
    if not pairs:
        print(__doc__)
        sys.exit(1)
    out = args[2 * len(pairs)] if len(args) > 2 * len(pairs) else 'data'
    regions = [read_poly(poly) for _, poly in pairs]
    owner = plan_tiles(regions)
    print(f'{len(owner)} tiles planned ({sum(v == -1 for v in owner.values())} on shared borders)', flush=True)

    # Each extract is read in its own process (one CPU core each), results are merged below.
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=min(len(pairs), os.cpu_count() or 1)) as ex:
        results = list(ex.map(read_region, range(len(pairs)), [p for p, _ in pairs], [owner] * len(pairs)))

    buildings = defaultdict(lambda: array('f'))
    pois = defaultdict(list)
    water = defaultdict(set)
    seen = defaultdict(set)                        # shared tiles: ids already taken from an earlier extract
    n_lines = n_areas = 0
    land_png, land_shared, land_seen = {}, defaultdict(list), defaultdict(set)
    for res in results:                            # in region order, so the output does not depend on timing
        land_png.update(res['land'])
        for t, items in res['land_shared'].items():
            for it in items:
                if it[3] not in land_seen[t]:
                    land_seen[t].add(it[3])
                    land_shared[t].append(it)
        for t, a in res['b'].items():
            buildings[t].extend(a)
        for t, lst in res['p'].items():
            pois[t].extend(lst)
        for t, lst in res['sb'].items():
            for key, fx, fy in lst:
                if key not in seen[t]:
                    seen[t].add(key)
                    buildings[t].extend((fx, fy))
        for t, lst in res['sp'].items():
            for key, p in lst:
                if key not in seen[t]:
                    seen[t].add(key)
                    pois[t].append(p)
        for t, s in res['w'].items():
            water[t] |= s
        n_lines += res['lines']
        n_areas += res['areas']

    covered = sorted(owner)
    for t, items in land_shared.items():
        land_png[t] = draw_land(t[0], t[1], items)
    empty_png = draw_land(0, 0, [])

    total_b = total_w = total_p = land_bytes = 0
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
        png = land_png.get((tx, ty), empty_png)
        land_bytes += len(png)
        with open(os.path.join(d, f'{ty}.land.png'), 'wb') as f:
            f.write(png)

    # Merge with an existing index so several regions can share one data folder.
    idx_path = os.path.join(out, 'index.json')
    tiles = set()
    if os.path.exists(idx_path):
        with open(idx_path, encoding='utf-8') as f:
            tiles.update(json.load(f).get('tiles', []))
    tiles.update(f'{x}/{y}' for x, y in covered)
    with open(idx_path, 'w', encoding='utf-8') as f:
        # 'built' doubles as the cache-busting version for tile URLs in the app, so include the time
        json.dump({'z': TILE_Z, 'built': time.strftime('%Y-%m-%dT%H:%M'), 'tiles': sorted(tiles)}, f)

    print(f'Done in {time.time() - t0:.0f} s: {len(covered)} tiles, {total_b:,} buildings, '
          f'{total_p:,} points of interest, {total_w:,} water points '
          f'({n_lines:,} rivers/streams, {n_areas:,} lakes), terrain {land_bytes / 1e6:.1f} MB -> {out}/')


if __name__ == '__main__':
    main()
