#!/usr/bin/env python3
"""
Build WildNav offline tiles from one or more Geofabrik extracts.

Usage:
    python build_tiles.py [--workers N] [--keep] region.osm.pbf region.poly [other.osm.pbf other.poly ...] [out_dir]
    python build_tiles.py [--workers N] --resume <temporary folder> [--keep] [--redo] [out_dir]

--workers: CPU cores to use (default: half of them; the build runs at low priority).
--resume:  if a build stopped during the hidden maps, finish only those (the command is printed
           when it stops; hidden maps that are already done are kept).
--palette: re-encode existing terrain maps as palette PNGs (lossless, ~25 % smaller), no OSM needed.
--b16:     convert an existing data folder from .bin (Float32) to .b16 (Uint16) buildings, no OSM needed.
--dem:     FABDEM folder for hills in the hidden maps (default raw/fabdem if it exists).
--keep:    keep the temporary files after the build, so the hidden maps can be tuned later with
           --resume <folder> --redo (recomputes all of them in a few minutes, without re-reading OSM).

Neighbouring regions should be built in one run: tiles on their shared border are then
filled from both extracts (without duplicates) instead of being left out.

Writes (default out_dir = "data"):
    data/index.json              list of covered tiles (merged with an existing index)
    data/12/<x>/<y>.b16          building centres, Uint16 pairs (tile-relative Mercator x, y x 65535,
                                 ~10 cm; index.json "bfmt": "u16"). Older builds: .bin with Float32 pairs.
    data/12/<x>/<y>.json         points of interest [{c, lat, lon, n}]
    data/12/<x>/<y>.water.bin    rivers, streams and lake shores sampled every ~40 m,
                                 Uint16 pairs (tile-relative Mercator x, y scaled to 0..65535)
    data/12/<x>/<y>.land.png     terrain map, 512x512 RGB PNG (~12 m per pixel):
                                 R = LAND class code (0 = not mapped),
                                 G/B = protected-area id, high/low byte (0 = none)
    data/12/<x>/<y>.hide.png     256x256 greyscale PNG (~24 m per pixel), see hide_tile():
                                 hidden (255 = nobody on a road, path or in a house sees or hears you),
                                 in steps of HIDE_Q (smaller files, ~1 %-point error)
    data/12/<x>/<y>.nb.png       128x128 RGB PNG (~47 m): R = road/railway noise in dB(A),
                                 G = distance to the nearest path/road you can cycle on, in 10 m
                                 (exact distance transform, 255 = 2.5 km or more); per 2x2 block the
                                 loudest noise and the shortest distance, B = 0
    data/protected.json          protected areas [{n: name, l: level, t: type}], id = index + 1

Same tile scheme and categories as index.html (zoom-12 tiles).
Requires: pip install "osmium>=4" pillow numpy
"""
import glob
import io
import json
import math
import os
import shutil
import sys
import tempfile
import time
from array import array
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import osmium
from PIL import Image, ImageDraw

TILE_Z = 12
N = 1 << TILE_Z
KEYS = ('building', 'amenity', 'tourism', 'natural', 'leisure', 'man_made', 'waterway', 'landuse', 'boundary',
        'highway', 'railway', 'barrier', 'shop', 'historic')

# ── Hidden / noise / access maps (see hide_tile) ─────────────────────────────────────────────
HP = 256                     # pixels per tile side (~24 m)
# Roads and paths: (sight = how likely someone there notices you 0..1,
#                   noise = night-time traffic noise in dB(A) at 10 m (0 = none), bike = usable to get to a spot)
#   Quiet forest tracks and paths count less than village streets and footways (calibrated so a spot
#   ~70 m from the tracks of a typical forest is ~80 % hidden, and a meadow 500 m from a path ~20 %).
WAYS = {
    'motorway': (0.3, 75, 0), 'trunk': (0.5, 70, 0), 'primary': (0.5, 67, 1), 'secondary': (0.6, 63, 1),
    'tertiary': (0.6, 59, 1), 'unclassified': (0.8, 52, 1), 'residential': (0.8, 50, 1), 'living_street': (1, 0, 1),
    'service': (0.5, 0, 1), 'track': (0.4, 0, 1), 'pedestrian': (1, 0, 1), 'footway': (0.8, 0, 1), 'path': (0.6, 0, 1),
    'cycleway': (0.8, 0, 1), 'bridleway': (0.5, 0, 1), 'steps': (0.6, 0, 0),
}
RAILS = {'rail': (0.2, 68, 0), 'light_rail': (0.3, 58, 0), 'tram': (0.5, 58, 0), 'narrow_gauge': (0.3, 55, 0)}
RAIL_SIDING = (0.1, 0)       # sidings and yards: (sight, bike); noise = line noise - 10 dB
# Sight weights are applied in hide_tile(), so they can be tuned with --resume --redo:
# the temporary rasters store a class code (index in SIGHT_CLASSES + 1), not the weight.
SIGHT_CLASSES = list(WAYS) + list(RAILS) + ['rail_siding']
SIGHT_W = [0.0] + [WAYS[k][0] for k in WAYS] + [RAILS[k][0] for k in RAILS] + [RAIL_SIDING[0]]
HOUSE_SIGHT = 0.5            # a house counts as half an observer (people inside, not always looking out)
# Per land class (LAND codes; 17 = hedge / tree row): sight blocking per metre (natural log),
# hearing damping per metre (natural log, for your voice), noise damping in dB per metre (for traffic).
# Forest: summer foliage and undergrowth let you see ~20-60 m.
K_SIGHT = {0: .002, 1: .002, 3: .012, 4: .002, 5: .025, 6: .04, 7: .065, 8: .05, 9: .005, 11: .003, 12: .02,
           13: .012, 14: .004, 15: .005, 17: .08}
K_HEAR = {5: .006, 6: .012, 7: .012, 8: .012, 12: .01, 17: .02}
K_NOISE = {5: .025, 6: .05, 7: .05, 8: .05, 12: .03, 17: .05}   # ISO 9613-2 foliage ~0.05 dB/m, capped below
HEDGE = 17
L_SIGHT = 800.0              # m: how quickly being seen fades with distance in open land
L_HEAR = 40.0                # m: same for being heard talking
HEAR_W = 0.3                 # being heard counts less than being seen (you're not talking all the time)
E0 = 0.12                    # exposure scale: hidden = exp(-exposure / E0)
AIR = 0.003                  # dB per metre: air + ground absorption of traffic noise
NOISE_CAP = 12.0             # dB: most damping forest/buildings can add
HIDE_ANGLES = 8              # grid rotations, each swept both ways -> 16 directions
HIDE_Q = 8                   # hidden is stored in steps of 8/255 (~3 %): files ~40 % smaller
# Terrain between you and the observers (from FABDEM, if --dem is available):
EYE_H, TENT_H = 1.7, 1.2     # m above ground: an observer's eyes, the top of a tent
DEM_TOL = 2.0                # m: a rise must stick out this much to block the view (DEM height errors)
HEAR_OVER_HILL = 0.3         # voices carry partly over a ridge (diffraction)
NOISE_HILL = 8.0             # dB less traffic noise behind a ridge
HOLLOW_R = 9                 # slope-map pixels (~420 m): "surroundings" for cold hollows
AREA_KEYS = ('landuse', 'natural', 'waterway', 'leisure', 'boundary')   # areas: terrain + protected areas

# Protected areas: 2 = strict (nature reserve, national park: no camping),
# 1 = restricted (landscape protection, Natura 2000: check local rules). Mirror of PROT in index.html.
PROT_STRICT, PROT_LIMITED = 2, 1

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
    """Night-mode category of an object (CATS in index.html); day categories: day_class()."""
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


# Day mode (riding): resupply and sights. (key, value) -> category; the value is stored as the type 't'
# (the app turns it into words, DAY_TYPES in index.html). Mirror of the day categories in CATS.
DAY_TAGS = {
    **{('shop', v): 'groceries' for v in ('supermarket', 'convenience', 'bakery', 'pastry', 'butcher', 'greengrocer',
                                          'farm', 'deli', 'general', 'kiosk')},
    ('amenity', 'fuel'): 'groceries',            # petrol stations: snacks and drinks, often open on Sundays
    **{('amenity', v): 'food' for v in ('restaurant', 'cafe', 'fast_food', 'pub', 'biergarten', 'ice_cream')},
    ('shop', 'bicycle'): 'bike', ('amenity', 'bicycle_repair_station'): 'bike', ('amenity', 'compressed_air'): 'bike',
    ('amenity', 'toilets'): 'toilets',
    **{('tourism', v): 'sights' for v in ('attraction', 'museum')},
    **{('historic', v): 'sights' for v in ('castle', 'ruins', 'monument', 'archaeological_site', 'fort')},
    ('natural', 'peak'): 'sights',               # named peaks only (see day_class)
    # break spots: somewhere to sit, to swim; train stations (to bail out, bikes go on regional trains)
    ('amenity', 'bench'): 'benches', ('leisure', 'picnic_table'): 'benches',
    ('leisure', 'bathing_place'): 'swim', ('leisure', 'swimming_area'): 'swim',
    ('railway', 'station'): 'stations', ('railway', 'halt'): 'stations',
}
DAY_KEYS = ('shop', 'amenity', 'tourism', 'historic', 'natural', 'leisure', 'railway')


def day_class(t):
    """(category, type) for resupply points and sights, or None."""
    for k in DAY_KEYS:
        v = t.get(k)
        cat = v and DAY_TAGS.get((k, v))
        if not cat or (k == 'natural' and not t.get('name')):
            continue
        if cat == 'stations' and t.get('station') in ('subway', 'light_rail', 'monorail', 'funicular'):
            continue                               # city transport, not for bikes
        return cat, v
    return None


def poi_cats(t):
    """All categories of an object: a shop in a building is both a house and a resupply point."""
    cats = []
    c = classify(t)
    if c:
        cats.append((c, None))
    d = day_class(t)
    if d:
        cats.append(d)
    return cats


def make_poi(cat, typ, lat, lon, t):
    p = {'c': cat, 'lat': round(lat, 6), 'lon': round(lon, 6), 'n': t.get('name', '')}
    if typ:
        p['t'] = typ
        oh = t.get('opening_hours')
        if oh:
            p['o'] = oh[:120]
    return p


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


def prot_level(t):
    """Protection level of an area (German tagging conventions), or 0."""
    b = t.get('boundary')
    if t.get('leisure') == 'nature_reserve' or b == 'national_park':
        return PROT_STRICT
    if b != 'protected_area':
        return 0
    title = ' '.join(t.get(k, '') for k in ('protection_title', 'designation', 'name')).lower()
    pc = t.get('protect_class', '')
    if 'naturpark' in title or 'nature park' in title or 'wasserschutz' in title:
        return 0                                   # huge, no camping ban as such
    if pc in ('1', '1a', '1b', '2', '3', '4') or 'naturschutzgebiet' in title or 'nationalpark' in title:
        return PROT_STRICT
    if pc in ('5', '97') or any(w in title for w in ('landschaftsschutz', 'ffh', 'natura 2000', 'vogelschutz')):
        return PROT_LIMITED
    return 0


def prot_title(t, level):
    title = t.get('protection_title') or t.get('designation') or ''
    if not title:
        title = ('National park' if t.get('boundary') == 'national_park' else 'Nature reserve') if level == PROT_STRICT \
            else 'Protected area'
    return title.replace('_', ' ')[:60]


def ring_merc(nodes, typecode='d'):
    """Node list -> flat array of Mercator x, y in zoom-12 tile units (same arithmetic as merc(),
    inlined: this runs for ~50 million nodes per extract)."""
    a = array(typecode)
    ext, tan, log, cos, rad, pi = a.extend, math.tan, math.log, math.cos, math.radians, math.pi
    for n in nodes:
        loc = n.location
        if loc.valid():
            r = rad(max(-85.0, min(85.0, loc.lat)))
            ext(((loc.lon + 180.0) / 360.0 * N, (1 - log(tan(r) + 1 / cos(r)) / pi) / 2 * N))
    return a


def tiles_of(ring):
    xs, ys = ring[0::2], ring[1::2]
    return [(tx, ty) for tx in range(int(min(xs)), int(max(xs)) + 1) for ty in range(int(min(ys)), int(max(ys)) + 1)]


def touches_covered(ring, owner):
    return len(ring) >= 6 and any(t in owner for t in tiles_of(ring))


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


def paint(imgs, tx, ty, rings, values):
    """Fill a polygon (outer ring + holes, in tile units) with values[k] on imgs[k]."""
    pts = [[((a[i] - tx) * LAND_PX, (a[i + 1] - ty) * LAND_PX) for i in range(0, len(a), 2)] for a in rings]
    if len(pts) == 1:
        for img, v in zip(imgs, values):
            ImageDraw.Draw(img).polygon(pts[0], fill=v)
        return
    # holes: draw through a mask limited to the bounding box
    x0 = max(0, int(min(p[0] for p in pts[0]))); x1 = min(LAND_PX, int(max(p[0] for p in pts[0])) + 1)
    y0 = max(0, int(min(p[1] for p in pts[0]))); y1 = min(LAND_PX, int(max(p[1] for p in pts[0])) + 1)
    if x1 <= x0 or y1 <= y0:
        return
    mask = Image.new('1', (x1 - x0, y1 - y0), 0)
    md = ImageDraw.Draw(mask)
    for j, p in enumerate(pts):
        md.polygon([(x - x0, y - y0) for x, y in p], fill=0 if j else 1)
    for img, v in zip(imgs, values):
        img.paste(v, (x0, y0, x1, y1), mask)


def draw_land(tx, ty, items):
    """Rasterise terrain items of one tile (greyscale, returned as raw bytes)."""
    img = Image.new('L', (LAND_PX, LAND_PX), 0)
    for code, is_line, rings, _ in sorted(items, key=lambda it: it[0]):
        if is_line:
            a = rings[0]
            ImageDraw.Draw(img).line([((a[i] - tx) * LAND_PX, (a[i + 1] - ty) * LAND_PX) for i in range(0, len(a), 2)],
                                     fill=code, width=1)
        else:
            paint([img], tx, ty, rings, [code])
    return img.tobytes()


def finish_tile(job):
    """Second step (runs on all cores): add protected-area ids to a terrain map and encode
    it as PNG. R = terrain class, G/B = protected-area id (high/low byte, 0 = none)."""
    tx, ty, land_raw, prot_items, tmp = job
    size = (LAND_PX, LAND_PX)
    land = Image.frombytes('L', size, land_raw) if land_raw else Image.new('L', size, 0)
    # terrain at the hide-map resolution, for hide_tile() of this tile and its neighbours
    np.save(os.path.join(tmp, f'l_{tx}_{ty}.npy'), np.asarray(land)[::LAND_PX // HP, ::LAND_PX // HP])
    hi, lo = Image.new('L', size, 0), Image.new('L', size, 0)
    for pid, rings in prot_items:                  # already sorted: weaker/larger areas first
        paint([hi, lo], tx, ty, rings, [pid >> 8, pid & 255])
    buf = io.BytesIO()
    return (tx, ty), palette_png(np.asarray(Image.merge('RGB', (land, hi, lo))))


# ── Hidden / noise / access (third build step, all cores) ─────────────────────────────────────
# For each tile, a 3x3-tile neighbourhood is rasterised (roads, paths, houses = observers and noise
# sources; terrain = what blocks sight and sound). Then the grid is swept along 16 directions,
# carrying along each ray (see IDEAS.md, section 1.3):
#   seen:  the strongest observer visible from here, fading with distance (L_SIGHT) and behind
#          forest/scrub/hedges (K_SIGHT): S = max(S * transmission, observer weight)
#   heard: the same with L_HEAR / K_HEAR (your voice carries less far than you can be seen)
#   noise: the loudest road/railway along the ray, -10 log10(d) (a line source seen through a
#          narrow angle), air absorption and forest/building damping (K_NOISE, capped);
#          the 16 directions are then added up as sound energy
#   bike:  distance to the nearest path or road you can cycle on
# Directions are made by rotating the grid (nearest neighbour), so every sweep runs along rows.
_rot = None

# Optional speed-up: with numba installed (pip install numba) the sweep runs as compiled code,
# ray by ray; without it the numpy version below is used (same results).
try:
    from numba import njit
except ImportError:
    njit = None


def _sweep_rays(Ts, Th, An, ws, wn, bike, z, u0, u1, px, first, s_sum, h_sum, n_sum, b_min,
                eye_h, tent_h, dem_tol, hear_over_hill, noise_cap, air, noise_hill):
    """Both sweep directions for every ray (column) of one rotation; same model as the numpy loop."""
    D, nv = ws.shape
    for rev in range(2):
        for v in range(nv):
            S = 0.0; H = 0.0; lv = -100.0; dn = 1e6; dmp = 0.0; db = 1e9
            zo = 0.0; dd = 1e6; mm = -1e9; zn = 0.0; mn = -1e9
            j = D - 1 if rev else 0
            stop = u0 - 1 if rev else u1 + 1
            step = -1 if rev else 1
            while j != stop:
                w = ws[j, v]; zc = z[j, v]
                dd += px
                vis = (zc + tent_h - zo) / dd >= mm
                Sd = S * Ts[j, v]
                if w > (Sd if vis else 0.0):
                    S = w; zo = zc + eye_h; dd = px / 2; mm = -1e9; vis = True
                else:
                    S = Sd
                    t = (zc - dem_tol - zo) / dd
                    if t > mm:
                        mm = t
                H = max(H * Th[j, v], w)
                if vis:
                    s_sum[j, v] += S
                    h_sum[j, v] += H
                else:
                    h_sum[j, v] += H * hear_over_hill
                dn += px
                dmp = min(dmp + An[j, v], noise_cap)
                visn = (zc + 1.5 - zn) / dn >= mn
                val = lv - 10 * math.log10(dn / 10) - dmp - air * dn - (0.0 if visn else noise_hill)
                wj = wn[j, v]
                if wj > 0 and wj - first > val:
                    lv = wj; dn = px / 2; dmp = 0.0; val = wj - first; zn = zc + 0.5; mn = -1e9
                else:
                    t = (zc - dem_tol - zn) / dn
                    if t > mn:
                        mn = t
                n_sum[j, v] += 10 ** (val / 10)
                db = 0.0 if bike[j, v] > 0 else db + px
                if db < b_min[j, v]:
                    b_min[j, v] = db
                j += step


if njit:
    _sweep_rays = njit(cache=False)(_sweep_rays)   # compiling takes ~1 s per process; a disk cache is fragile



def rot_maps():
    """Index maps: the 3x3 grid rotated by each angle (rays along axis 0), and back for the centre tile.
    Only the rays (columns) that cross the centre tile are kept, and the rows range [u0, u1] where the
    centre tile lies: a forward sweep can stop after u1, a backward sweep after u0."""
    n = 3 * HP
    D = int(math.ceil(n * math.sqrt(2))) + 2
    cs, cr = (n - 1) / 2, (D - 1) / 2
    u, v = np.mgrid[0:D, 0:D].astype(np.float32) - cr
    yy, xx = np.mgrid[HP:2 * HP, HP:2 * HP].astype(np.float32) - cs
    out = []
    for i in range(HIDE_ANGLES):
        a = i * math.pi / HIDE_ANGLES
        ca, sa = math.cos(a), math.sin(a)
        x = np.rint(ca * u - sa * v + cs).astype(np.int32)
        y = np.rint(sa * u + ca * v + cs).astype(np.int32)
        fwd = np.where((x >= 0) & (x < n) & (y >= 0) & (y < n), y * n + x, n * n)   # n*n = outside
        uu = np.rint(ca * xx + sa * yy + cr).astype(np.int32)
        vv = np.rint(-sa * xx + ca * yy + cr).astype(np.int32)
        v0, v1, u0, u1 = int(vv.min()), int(vv.max()), int(uu.min()), int(uu.max())
        nv = v1 - v0 + 1
        out.append((D, np.ascontiguousarray(fwd[:, v0:v1 + 1]), uu * nv + (vv - v0), nv, u0, u1))
    return out


def path_distance(mask, px, cap_px=110):
    """Exact distance (m) from each pixel of the centre tile to the nearest True pixel of a 3x3-tile mask,
    capped at cap_px pixels. Pass 1: vertical distance per column (sweeps down and up);
    pass 2: for each pixel the best (dx² + dy²) over horizontal offsets up to cap_px."""
    n = mask.shape[0]
    big = np.float32(cap_px + 1)
    g = np.full((n, n), big, np.float32)
    cur = np.full(n, big, np.float32)
    for y in range(n):
        cur = np.where(mask[y], 0, np.minimum(cur + 1, big)); g[y] = cur
    cur = np.full(n, big, np.float32)
    for y in range(n - 1, -1, -1):
        cur = np.where(mask[y], 0, np.minimum(cur + 1, big)); g[y] = np.minimum(g[y], cur)
    c = g[HP:2 * HP] ** 2                           # rows of the centre tile, all columns
    best = np.full((HP, HP), np.float32(big * big))
    for dx in range(-cap_px, cap_px + 1):
        best = np.minimum(best, c[:, HP + dx:2 * HP + dx] + np.float32(dx * dx))
    return np.sqrt(best) * px


def lut(table, default=0.0):
    a = np.full(256, default, np.float32)
    for k, v in table.items():
        a[k] = v
    return a


def hide_tile(job):
    """Compute the hide map of one tile and write data/12/<x>/<y>.hide.png."""
    global _rot
    tx, ty, tmp, out, n_regions, dem = job
    if _rot is None:
        _rot = rot_maps()
    n = 3 * HP
    land = np.zeros((n, n), np.uint8)
    roads = np.zeros((4, n, n), np.uint8)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            X, Y = tx + dx, ty + dy
            sl = (slice((dy + 1) * HP, (dy + 2) * HP), slice((dx + 1) * HP, (dx + 2) * HP))
            f = os.path.join(tmp, f'l_{X}_{Y}.npy')
            if os.path.exists(f):
                land[sl] = np.load(f)
            for ri in range(n_regions):
                f = os.path.join(tmp, f'r_{X}_{Y}_{ri}.npy')
                if os.path.exists(f):
                    roads[(slice(None),) + sl] = np.maximum(roads[(slice(None),) + sl], np.load(f))
    ws = np.array(SIGHT_W + [0.0] * (256 - len(SIGHT_W)), np.float32)[roads[0]]   # class code -> observer weight
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            b = load_buildings(os.path.join(out, str(TILE_Z), str(tx + dx)), ty + dy)
            if b is not None:                      # houses (building centres, tile-relative 0..1)
                bx = np.clip(((b[0::2] + dx + 1) * HP).astype(np.int32), 0, n - 1)
                by = np.clip(((b[1::2] + dy + 1) * HP).astype(np.int32), 0, n - 1)
                ws[by, bx] = np.maximum(ws[by, bx], HOUSE_SIGHT)
    code = np.where(roads[3] > 0, HEDGE, land)
    px = 40075016.686 * math.cos(math.radians(tile_lat(ty + 0.5))) / N / HP     # metres per pixel
    ks, kh, kn = lut(K_SIGHT)[code], lut(K_HEAR)[code], lut(K_NOISE)[code]
    ext = lambda a, pad: np.append(a.ravel().astype(np.float32), np.float32(pad))
    z = np.zeros((n, n), np.float32)            # ground elevation of the 3x3 tiles (flat if there is no DEM)
    if dem:
        i = np.arange(n, dtype=np.float64) + 0.5
        lon = (tx - 1 + i[None, :] / HP) / N * 360 - 180 + 0 * i[:, None]
        lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (ty - 1 + i[:, None] / HP) / N)))) + 0 * i[None, :]
        z = dem_sample(dem, lat, lon)
        z = np.where(np.isnan(z), np.nanmean(z) if not np.isnan(z).all() else 0, z)
    src = {'Ts': ext(np.exp(-(ks + 1 / L_SIGHT) * px), math.exp(-(K_SIGHT[0] + 1 / L_SIGHT) * px)),
           'Th': ext(np.exp(-(kh + 1 / L_HEAR) * px), math.exp(-px / L_HEAR)),
           'An': ext(kn * px, 0), 'ws': ext(ws, 0), 'wn': ext(roads[1], 0), 'bike': ext(roads[2], 0),
           'z': ext(z, float(z.mean()))}
    ES = np.zeros((HP, HP), np.float32)
    EH, EN = ES.copy(), ES.copy()
    DB = path_distance(roads[2] > 0, px)          # exact distance to the nearest bikeable path
    first = 10 * math.log10(px / 20)               # a road in the same pixel is ~px/2 away (level at 10 m)
    for D, fwd, back, nv, u0, u1 in _rot:
        R = {k: v[fwd] for k, v in src.items()}
        s_sum = np.zeros((D, nv), np.float32)
        h_sum, n_sum = s_sum.copy(), s_sum.copy()
        b_min = np.full((D, nv), 1e9, np.float32)
        if njit:
            _sweep_rays(R['Ts'], R['Th'], R['An'], R['ws'], R['wn'], R['bike'], R['z'], u0, u1, px, first,
                        s_sum, h_sum, n_sum, b_min, EYE_H, TENT_H, DEM_TOL, HEAR_OVER_HILL, NOISE_CAP, AIR, NOISE_HILL)
        for rev in (() if njit else (False, True)):
            S = np.zeros(nv, np.float32)
            H = S.copy()
            lv = np.full(nv, -100, np.float32)     # loudest source so far: level at 10 m, distance, damping
            dn = np.full(nv, 1e6, np.float32)
            dmp = np.zeros(nv, np.float32)
            db = np.full(nv, 1e9, np.float32)
            # terrain: the tracked observer's eye height, distance, and the steepest rise seen from it so far
            zo, dd, mm = np.zeros(nv, np.float32), np.full(nv, 1e6, np.float32), np.full(nv, -1e9, np.float32)
            zn, mn = np.zeros(nv, np.float32), np.full(nv, -1e9, np.float32)   # same for the noise source
            # only as far as the centre tile: beyond it nothing is read any more
            for j in (range(D - 1, u0 - 1, -1) if rev else range(u1 + 1)):
                w, zc = R['ws'][j], R['z'][j]
                dd += px
                vis = (zc + TENT_H - zo) / dd >= mm      # can the tracked observer see a tent here?
                Sd = S * R['Ts'][j]
                take = w > np.where(vis, Sd, 0)          # an observer here beats a fainter or hidden one
                S = np.where(take, w, Sd)
                zo = np.where(take, zc + EYE_H, zo)
                dd = np.where(take, px / 2, dd)
                mm = np.maximum(np.where(take, -1e9, mm), np.where(take, -1e9, (zc - DEM_TOL - zo) / dd))
                vis |= take
                H = np.maximum(H * R['Th'][j], w)
                s_sum[j] += np.where(vis, S, 0)
                h_sum[j] += np.where(vis, H, H * HEAR_OVER_HILL)
                dn += px
                dmp = np.minimum(dmp + R['An'][j], NOISE_CAP)
                visn = (zc + 1.5 - zn) / dn >= mn
                val = lv - 10 * np.log10(dn / 10) - dmp - AIR * dn - np.where(visn, 0, NOISE_HILL)
                wj = R['wn'][j]
                take = (wj > 0) & (wj - first > val)
                if take.any():
                    lv = np.where(take, wj, lv)
                    dn = np.where(take, px / 2, dn)
                    dmp = np.where(take, 0, dmp)
                    val = np.where(take, wj - first, val)
                    zn = np.where(take, zc + 0.5, zn)
                    mn = np.where(take, -1e9, mn)
                mn = np.maximum(mn, np.where(take, -1e9, (zc - DEM_TOL - zn) / dn))
                n_sum[j] += np.power(10, val / 10)
                db = np.where(R['bike'][j] > 0, 0, db + px)
                b_min[j] = np.minimum(b_min[j], db)
        ES += s_sum.ravel()[back]
        EH += h_sum.ravel()[back]
        EN += n_sum.ravel()[back]

    k = 2 * len(_rot)
    hidden = np.exp(-(ES / k + HEAR_W * EH / k) / E0)
    # energy mean over directions; + 10 log10(pi) so a straight road gives its level at 10 m - 10 log10(d / 10)
    noise = 10 * np.log10(EN / k + 1e-12) + 10 * math.log10(math.pi)
    h = np.clip(np.rint(hidden * 255 / HIDE_Q) * HIDE_Q, 0, 255).astype(np.uint8)
    # noise and path distance change slowly: 128 px is enough; per 2x2 block the loudest noise and the
    # shortest path distance, so the smaller map errs on the safe side
    n2 = noise.reshape(HP // 2, 2, HP // 2, 2).max(axis=(1, 3))
    d2 = DB.reshape(HP // 2, 2, HP // 2, 2).min(axis=(1, 3))
    nb = np.stack([np.clip(np.rint(n2), 0, 255), np.clip(np.rint(d2 / 10), 0, 255), np.zeros_like(n2)], -1).astype(np.uint8)
    d = os.path.join(out, str(TILE_Z), str(tx))
    return write_png(os.path.join(d, f'{ty}.hide.png'), h) + write_png(os.path.join(d, f'{ty}.nb.png'), nb)


def write_png(path, a):
    """Write an array as PNG, via a temp file and rename: a crash never leaves half a file."""
    buf = io.BytesIO()
    Image.fromarray(a).save(buf, format='PNG', optimize=True)
    with open(path + '.part', 'wb') as f:
        f.write(buf.getvalue())
    os.replace(path + '.part', path)
    return len(buf.getvalue())


def palette_png(rgb):
    """RGB array -> PNG bytes, as a palette image when it has <= 256 colours (lossless, ~25 % smaller)."""
    flat = rgb.reshape(-1, 3).astype(np.uint32)
    key = flat[:, 0] << 16 | flat[:, 1] << 8 | flat[:, 2]      # one number per colour: much faster than rows
    vals, inv = np.unique(key, return_inverse=True)
    buf = io.BytesIO()
    if len(vals) <= 256:
        cols = np.stack([vals >> 16, vals >> 8 & 255, vals & 255], -1)
        im = Image.fromarray(inv.reshape(rgb.shape[:2]).astype(np.uint8), 'P')
        im.putpalette(cols.astype(np.uint8).ravel().tolist())
        im.save(buf, format='PNG', optimize=True)
    else:
        Image.fromarray(rgb).save(buf, format='PNG', optimize=True)
    return buf.getvalue()


# ── Slope (separate step: --slope <folder with FABDEM tiles>) ──────────────────────────────────
# FABDEM V1-2 (University of Bristol / Fathom): Copernicus 30 m elevation with forests and buildings
# removed, 1x1 degree GeoTIFFs named like N51E007_FABDEM_V1-2.tif, pixel centres on whole
# arc-seconds (3600 per degree). Non-commercial licence, credit in the app and README.
_dem_cache = {}
SLOPE_PX = HP // 2           # slope map pixels per tile side (~47 m)


def dem_tile(folder, la, lo):
    k = (la, lo)
    if k not in _dem_cache:
        if len(_dem_cache) >= 4:
            _dem_cache.pop(next(iter(_dem_cache)))
        f = os.path.join(folder, f'N{la:02d}E{lo:03d}_FABDEM_V1-2.tif')
        _dem_cache[k] = np.asarray(Image.open(f), np.float32) if os.path.exists(f) else None
    return _dem_cache[k]


def dem_sample(folder, lat, lon):
    """Bilinear elevation (m) at arrays of lat/lon; NaN where there is no DEM tile."""
    z = np.full(lat.shape, np.nan, np.float32)
    fl, fo = np.floor(lat).astype(int), np.floor(lon).astype(int)
    for la, lo in set(zip(fl.ravel().tolist(), fo.ravel().tolist())):
        a = dem_tile(folder, la, lo)
        if a is None:
            continue
        m = (fl == la) & (fo == lo)
        r = np.clip((la + 1 - lat[m]) * 3600, 0, 3599)     # row 0 = north edge (la + 1)
        c = np.clip((lon[m] - lo) * 3600, 0, 3599)
        r0, c0 = np.minimum(r.astype(int), 3598), np.minimum(c.astype(int), 3598)
        fr, fc = r - r0, c - c0
        z[m] = (a[r0, c0] * (1 - fr) * (1 - fc) + a[r0, c0 + 1] * (1 - fr) * fc
                + a[r0 + 1, c0] * fr * (1 - fc) + a[r0 + 1, c0 + 1] * fr * fc)
    return z


def slope_tile(job):
    """Write data/12/<x>/<y>.slope.png (SLOPE_PX x SLOPE_PX RGB): R = slope in 0.25 degrees,
    G = direction the slope faces (downhill), 16 compass points x 16 (0 = N, 64 = E, ...),
    + low 4 bits: hollow depth in 2 m (how far below the mean ground within ~420 m, 0-30 m: cold air),
    B = elevation / 4 m (0-1020 m). Returns bytes, or 0 if there is no elevation data."""
    tx, ty, folder, out = job
    i = np.arange(-1, HP + 1, dtype=np.float64) + 0.5          # pixel centres, 1 pixel margin
    lon = (tx + i[None, :] / HP) / N * 360 - 180 + 0 * i[:, None]
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (ty + i[:, None] / HP) / N)))) + 0 * i[None, :]
    z = dem_sample(folder, lat, lon)
    if np.isnan(z[1:-1, 1:-1]).all():
        return 0
    z = np.where(np.isnan(z), np.nanmean(z), z)
    px = 40075016.686 * math.cos(math.radians(tile_lat(ty + 0.5))) / N / HP   # metres per pixel
    dzdx = (z[1:-1, 2:] - z[1:-1, :-2]) / (2 * px)             # towards east
    dzdn = (z[:-2, 1:-1] - z[2:, 1:-1]) / (2 * px)             # towards north (rows go south)
    # stored at SLOPE_PX (2x2 average, ~47 m): the DEM itself is 30 m, finer pixels only add noise
    half = lambda a: a.reshape(SLOPE_PX, 2, SLOPE_PX, 2).mean(axis=(1, 3))
    slope = half(np.degrees(np.arctan(np.hypot(dzdx, dzdn))))
    gx, gn = half(dzdx), half(dzdn)
    aspect = (np.degrees(np.arctan2(-gx, -gn)) + 360) % 360
    # hollows: mean ground within HOLLOW_R pixels (box) minus the ground here, on the SLOPE_PX grid
    ic = np.arange(-HOLLOW_R, SLOPE_PX + HOLLOW_R, dtype=np.float64) + 0.5
    lonc = (tx + ic[None, :] / SLOPE_PX) / N * 360 - 180 + 0 * ic[:, None]
    latc = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (ty + ic[:, None] / SLOPE_PX) / N)))) + 0 * ic[None, :]
    zc = dem_sample(folder, latc, lonc)
    zc = np.where(np.isnan(zc), np.nanmean(zc), zc)
    k = 2 * HOLLOW_R + 1
    cs = np.pad(zc, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    mean = (cs[k:, k:] - cs[:-k, k:] - cs[k:, :-k] + cs[:-k, :-k]) / (k * k)
    depth = mean - zc[HOLLOW_R:-HOLLOW_R, HOLLOW_R:-HOLLOW_R]
    cold = np.clip(np.rint(depth / 2), 0, 15)
    rgb = np.stack([np.clip(np.rint(slope * 4), 0, 255), np.rint(aspect / 22.5) % 16 * 16 + cold,
                    np.clip(np.rint(half(z[1:-1, 1:-1]) / 4), 0, 255)], -1).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format='PNG', optimize=True)
    path = os.path.join(out, str(TILE_Z), str(tx), f'{ty}.slope.png')
    with open(path + '.part', 'wb') as f:
        f.write(buf.getvalue())
    os.replace(path + '.part', path)
    return len(buf.getvalue())


def save_b16(path, xy):
    """Building centres (tile-relative 0..1 pairs) as little-endian Uint16 x 65535."""
    np.clip(np.rint(np.asarray(xy, np.float64) * 65535), 0, 65535).astype('<u2').tofile(path)


def load_buildings(folder_x, y):
    """Building centres of a tile as float pairs 0..1, from .b16 (current) or .bin (older builds)."""
    f = os.path.join(folder_x, f'{y}.b16')
    if os.path.exists(f):
        return np.fromfile(f, '<u2').astype(np.float32) / 65535
    f = os.path.join(folder_x, f'{y}.bin')
    return np.fromfile(f, np.float32) if os.path.exists(f) else None


def bump_version(out):
    """Update 'built' in index.json: the app uses it as ?v= on tile URLs, so browsers and the offline
    cache fetch the changed files instead of keeping old copies (needed after --slope / --resume)."""
    p = os.path.join(out, 'index.json')
    with open(p, encoding='utf-8') as f:
        idx = json.load(f)
    idx['built'] = time.strftime('%Y-%m-%dT%H:%M')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(idx, f)


def slope_step(folder, out, workers, redo=False):
    """Slope maps for every tile in out/index.json (skips existing ones unless redo)."""
    with open(os.path.join(out, 'index.json'), encoding='utf-8') as f:
        tiles = [tuple(map(int, k.split('/'))) for k in json.load(f)['tiles']]
    todo = [t for t in tiles if redo or not os.path.exists(os.path.join(out, str(TILE_Z), str(t[0]), f'{t[1]}.slope.png'))]
    # sorted by DEM tile, so each worker mostly reuses the elevation tiles it has already loaded
    todo.sort(key=lambda t: (math.floor(tile_lat(t[1] + 0.5)), math.floor(t[0] / N * 360 - 180), t))
    print(f'Slope maps: {len(tiles) - len(todo)} already done, {len(todo)} to go on {workers} cores', flush=True)
    t0, done, total, next_pct = time.time(), 0, 0, 0
    with ProcessPoolExecutor(max_workers=workers, initializer=low_priority) as ex:
        for size in ex.map(slope_tile, [(tx, ty, folder, out) for tx, ty in todo], chunksize=16):
            done += 1
            total += size
            pct = done * 100 // max(1, len(todo))
            if pct >= next_pct:
                left = (time.time() - t0) / done * (len(todo) - done)
                print(f'Slope maps: {done:,} / {len(todo):,} ({pct} %), about {left / 60:.0f} min left', flush=True)
                next_pct = pct + 10
    print(f'Slope maps done in {time.time() - t0:.0f} s ({total / 1e6:.1f} MB)', flush=True)
    if todo:
        bump_version(out)


def is_water_line(t):
    return (t.get('waterway') in WATERWAYS and t.get('intermittent') != 'yes'
            and t.get('tunnel') not in ('yes', 'culvert'))


def is_water_area(t):
    if t.get('intermittent') == 'yes':
        return False
    if t.get('waterway') == 'riverbank':
        return True
    return t.get('natural') == 'water' and t.get('water') not in AREA_WATER_EXCLUDE


def way_kind(t):
    """(sight class code, noise dB, bike, hedge) for roads, paths, railways and hedges, or None."""
    if t.get('tunnel') in ('yes', 'building_passage', 'culvert'):
        return None                                # underground: neither seen nor heard
    hw, rw = t.get('highway'), t.get('railway')
    if hw:
        base = hw[:-5] if hw.endswith('_link') else hw
        v = WAYS.get(base)
        if v:
            return (SIGHT_CLASSES.index(base) + 1, max(0, v[1] - 3) if hw.endswith('_link') and v[1] else v[1], v[2], 0)
    if rw in RAILS:
        s, n, b = RAILS[rw]
        if t.get('service'):                       # sidings and yards: little traffic
            return (SIGHT_CLASSES.index('rail_siding') + 1, n - 10, RAIL_SIDING[1], 0)
        return (SIGHT_CLASSES.index(rw) + 1, n, b, 0)
    if t.get('barrier') == 'hedge' or t.get('natural') == 'tree_row':
        return (0, 0, 0, 1)
    return None


def draw_lines(tx, ty, items):
    """Rasterise roads/paths/hedges of one tile (HP x HP): uint8 channels
    [sight class code, noise dB, bike 255, hedge 255]. Stronger values are drawn last, so they win
    (for the class code: the one with the highest sight weight)."""
    chans = [Image.new('L', (HP, HP), 0) for _ in range(4)]
    draws = [ImageDraw.Draw(c) for c in chans]
    order = [lambda k: SIGHT_W[k[0]], lambda k: k[1], lambda k: 1, lambda k: 1]
    for ch, value in ((0, lambda k: k[0]), (1, lambda k: k[1]), (2, lambda k: 255), (3, lambda k: 255)):
        for kind, a in sorted((it for it in items if it[0][ch]), key=lambda it: order[ch](it[0])):
            pts = [((a[i] - tx) * HP, (a[i + 1] - ty) * HP) for i in range(0, len(a), 2)]
            if len(pts) > 1:
                draws[ch].line(pts, fill=int(value(kind)), width=1)
    return np.stack([np.asarray(c) for c in chans])


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
    Tiles outside are not covered (the app shows no data there).
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


def read_region(ri, pbf, owner, tmp, part='all'):
    """Read one extract (runs in a worker process). Keeps only objects for tiles that
    region ri fills: tiles it owns, plus shared border tiles (returned with their OSM ids,
    so the parent can drop objects that appear in two extracts).
    Roads/paths/hedges are drawn here and saved to tmp/r_<x>_<y>_<ri>.npy for hide_tile().
    part: 'areas' (terrain, protected areas, lakes and rivers), 'items' (points of interest, buildings,
    roads/paths/hedges) or 'all'; the two parts run in parallel processes and are merged by the caller."""
    do_areas, do_items = part in ('all', 'areas'), part in ('all', 'items')
    lines = defaultdict(list)                      # (tx, ty) -> [(way_kind, Mercator coords)]
    n_ways = 0
    b = defaultdict(lambda: array('f'))            # owned tiles: building centres
    p = defaultdict(list)                          # owned tiles: points of interest
    sb = defaultdict(list)                         # shared tiles: (id, fx, fy)
    sp = defaultdict(list)                         # shared tiles: (id, poi)
    water = defaultdict(set)                       # (tx, ty) -> {(gx, gy)}; sets drop overlap duplicates
    t0, count, n_lines, n_areas = time.time(), 0, 0, 0
    name = os.path.basename(pbf)
    print(f'Reading {name}' + ('' if part == 'all' else f' ({part})'), flush=True)

    land = defaultdict(list)                       # (tx, ty) -> [(code, is_line, rings, id or None)]
    prot = {}                                      # protected areas touching covered tiles

    # with_locations() stores node positions so way centres can be computed;
    # with_areas() assembles polygons (also multipolygon relations) for terrain and lakes;
    # the key filters drop everything that is not relevant before it reaches Python.
    fp = osmium.FileProcessor(pbf).with_locations()
    if do_areas:
        fp = fp.with_areas(osmium.filter.KeyFilter(*AREA_KEYS))
    fp = fp.with_filter(osmium.filter.KeyFilter(*(KEYS if do_items else ('waterway',) + AREA_KEYS)))
    if do_areas:
        fp = fp.with_filter(osmium.filter.KeyFilter(*AREA_KEYS).enable_for(osmium.osm.AREA))
    if not do_items:                               # areas part: no nodes needed except for locations
        fp = fp.with_filter(osmium.filter.EntityFilter(osmium.osm.WAY | osmium.osm.AREA))

    for o in fp:
        if o.is_area():
            tags = o.tags
            if is_water_area(tags):
                for outer in o.outer_rings():
                    sample_line(outer, water)
                    for inner in o.inner_rings(outer):
                        sample_line(inner, water)
                n_areas += 1
            code, level = land_class(tags), prot_level(tags)
            if code or level:
                polys = [[ring_merc(outer)] + [ring_merc(inner) for inner in o.inner_rings(outer)]
                         for outer in o.outer_rings()]
                for rings in polys:
                    if code:
                        add_land(land, owner, ri, ('a', o.id), code, rings)
                if level and any(touches_covered(r[0], owner) for r in polys):
                    prot[('p', o.id)] = {'l': level, 'n': tags.get('name', ''), 't': prot_title(tags, level), 'polys': polys}
            continue
        if o.is_node():
            if not do_items or not o.location.valid():
                continue
            cats = poi_cats(o.tags)
            if not cats:
                continue
            lat, lon = o.location.lat, o.location.lon
        elif o.is_way():
            tags = o.tags
            if not do_items:                           # areas part: only rivers/streams as lines
                if not is_water_line(tags):
                    continue
            elif is_water_line(tags):
                if part == 'items':
                    continue                           # handled by the areas part
            if is_water_line(tags):
                sample_line(o.nodes, water)
                n_lines += 1
                if o.tags.get('waterway') in ('river', 'canal'):     # wide enough to show on the terrain map
                    add_land(land, owner, ri, ('w', o.id), LAND_WATER, [ring_merc(o.nodes)], is_line=True)
                continue
            kind = way_kind(tags)                      # (also for buildings: e.g. a roof over a road)
            if kind:                                   # road, path, railway or hedge -> hide maps
                a = ring_merc(o.nodes, 'f')
                if len(a) >= 4:
                    n_ways += 1
                    for t in tiles_of(a):
                        own = owner.get(t)
                        if own == ri or own == -1:     # shared tiles: both extracts draw it, merged with max()
                            lines[t].append((kind, a))
            cats = poi_cats(tags)
            if not cats:                               # e.g. landuse ways: only needed as areas
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

        mx, my = merc(lat, lon)
        tx, ty = int(mx), int(my)
        own = owner.get((tx, ty))
        if own is None or (own >= 0 and own != ri):
            continue                                   # not covered, or filled by another extract
        for cat, typ in cats:
            poi = None if cat == 'buildings' else make_poi(cat, typ, lat, lon, o.tags)
            if own == -1:
                key = (o.is_way(), o.id, cat)
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
    land_raw, land_shared = {}, {}
    for t, items in land.items():
        if owner.get(t) == ri:
            land_raw[t] = draw_land(t[0], t[1], items)
        else:
            land_shared[t] = items
    t1 = time.time()
    for (tx, ty), items in lines.items():
        np.save(os.path.join(tmp, f'r_{tx}_{ty}_{ri}.npy'), draw_lines(tx, ty, items))
    print(f'{name}: {n_ways:,} roads, paths and hedges drawn for {len(lines)} tiles in {time.time() - t1:.0f} s', flush=True)
    print(f'{name}: finished in {time.time() - t0:.0f} s (terrain for {len(land_raw)} tiles, '
          f'{len(prot)} protected areas)', flush=True)
    return {'b': dict(b), 'p': dict(p), 'sb': dict(sb), 'sp': dict(sp),
            'w': {t: s for t, s in water.items() if t in owner}, 'lines': n_lines, 'areas': n_areas,
            'land': land_raw, 'land_shared': land_shared, 'prot': prot}


def low_priority():
    """Run this process below normal priority, so the computer stays usable during a build."""
    try:
        if os.name == 'nt':
            import ctypes
            k = ctypes.windll.kernel32
            k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)     # BELOW_NORMAL_PRIORITY_CLASS
        else:
            os.nice(10)
    except Exception:
        pass


def hide_ok(path):
    """True if a hide map exists and can be read completely (not cut off by a crash)."""
    try:
        with Image.open(path) as im:
            im.load()
            return im.size == (HP, HP) and im.mode == 'L'
    except Exception:
        return False


def hide_step(tiles, tmp, out, n_regions, workers, redo=False, dem=None):
    """Third step: hide maps for all tiles that don't have a readable one yet (all with redo), with progress."""
    todo = tiles if redo else [t for t in tiles if not hide_ok(os.path.join(out, str(TILE_Z), str(t[0]), f'{t[1]}.hide.png'))]
    print(f'Hidden/noise/access maps: {len(tiles) - len(todo)} already done, {len(todo)} to go '
          f'on {workers} cores', flush=True)
    if not todo:
        return
    t0, done, total_bytes, next_pct = time.time(), 0, 0, 0
    with ProcessPoolExecutor(max_workers=workers, initializer=low_priority) as ex:
        for size in ex.map(hide_tile, [(tx, ty, tmp, out, n_regions, dem) for tx, ty in todo], chunksize=2):
            done += 1
            total_bytes += size
            pct = done * 100 // len(todo)
            if pct >= next_pct or done == len(todo):
                left = (time.time() - t0) / done * (len(todo) - done)
                print(f'Hidden maps: {done:,} / {len(todo):,} ({pct} %), about {left / 60:.0f} min left', flush=True)
                next_pct = pct + 5
    print(f'Hidden/noise/access maps done in {time.time() - t0:.0f} s ({total_bytes / 1e6:.1f} MB)', flush=True)


def merge_parts(a, b):
    """Combine the 'areas' and 'items' results of one extract (their contents don't overlap)."""
    out = {}
    for k in a:
        va, vb = a[k], b[k]
        if isinstance(va, int):
            out[k] = va + vb
        elif k == 'w':                             # water points per tile: sets
            out[k] = {t: va.get(t, set()) | vb.get(t, set()) for t in set(va) | set(vb)}
        else:
            out[k] = {**va, **vb}
    return out


def main():
    args = sys.argv[1:]
    workers = max(1, (os.cpu_count() or 2) // 2)   # half the cores: full load for minutes can overheat a PC
    if '--workers' in args:
        i = args.index('--workers')
        workers = max(1, int(args[i + 1]))
        del args[i:i + 2]
    keep, redo = '--keep' in args, '--redo' in args
    dem = 'raw/fabdem' if os.path.isdir('raw/fabdem') else None    # elevation for hills in the hide maps
    if '--dem' in args:
        i = args.index('--dem')
        dem = args[i + 1]
        del args[i:i + 2]
    print(f'Elevation (hills hide you): {dem or "none, terrain treated as flat"}', flush=True)
    args = [a for a in args if a not in ('--keep', '--redo')]
    low_priority()

    if '--palette' in args:                        # re-encode existing terrain maps as palette PNGs (lossless)
        args.remove('--palette')
        out = args[0] if args else 'data'
        before = after = 0
        for f in glob.glob(os.path.join(out, str(TILE_Z), '*', '*.land.png')):
            a = np.asarray(Image.open(f).convert('RGB'))
            data = palette_png(a)
            assert (np.asarray(Image.open(io.BytesIO(data)).convert('RGB')) == a).all()   # really lossless
            before += os.path.getsize(f); after += len(data)
            with open(f + '.part', 'wb') as fh:
                fh.write(data)
            os.replace(f + '.part', f)
        bump_version(out)
        print(f'Terrain maps: {before / 1e6:.1f} MB -> {after / 1e6:.1f} MB')
        return

    if '--b16' in args:                            # convert an existing data folder's .bin files to .b16
        args.remove('--b16')
        out = args[0] if args else 'data'
        n = 0
        for d in glob.glob(os.path.join(out, str(TILE_Z), '*')):
            for f in glob.glob(os.path.join(d, '*.bin')):
                if f.endswith('.water.bin'):
                    continue
                save_b16(f[:-4] + '.b16', np.fromfile(f, np.float32))
                os.remove(f)
                n += 1
        p = os.path.join(out, 'index.json')
        with open(p, encoding='utf-8') as fh:
            idx = json.load(fh)
        idx['bfmt'] = 'u16'
        with open(p, 'w', encoding='utf-8') as fh:
            json.dump(idx, fh)
        bump_version(out)
        print(f'{n} building files converted to .b16')
        return

    if '--slope' in args:                          # slope maps from FABDEM, for the tiles in index.json
        i = args.index('--slope')
        folder = args[i + 1]
        del args[i:i + 2]
        slope_step(folder, args[0] if args else 'data', workers, redo)
        return

    if '--resume' in args:                         # only the hide step, from a kept temporary folder
        i = args.index('--resume')
        tmp = args[i + 1]
        del args[i:i + 2]
        out = args[0] if args else 'data'
        names = [f[:-4].split('_') for f in os.listdir(tmp) if f.endswith('.npy')]
        tiles = sorted((int(p[1]), int(p[2])) for p in names if p[0] == 'l')
        n_regions = 1 + max((int(p[3]) for p in names if p[0] == 'r'), default=0)
        hide_step(tiles, tmp, out, n_regions, workers, redo, dem)
        bump_version(out)
        finish_tmp(tmp, keep, out)
        return

    pairs = [(a, b) for a, b in zip(args[::2], args[1::2]) if a.endswith('.pbf') and b.endswith('.poly')]
    if not pairs:
        print(__doc__)
        sys.exit(1)
    out = args[2 * len(pairs)] if len(args) > 2 * len(pairs) else 'data'
    regions = [read_poly(poly) for _, poly in pairs]
    owner = plan_tiles(regions)
    print(f'{len(owner)} tiles planned ({sum(v == -1 for v in owner.values())} on shared borders)', flush=True)

    # intermediate rasters for the hide maps; kept if the build stops, so it can be continued
    tmp = tempfile.mkdtemp(prefix='wildnav_')
    print(f'Temporary files: {tmp}', flush=True)
    try:
        build(pairs, owner, out, tmp, workers, dem)
    except BaseException:
        print(f'\nBuild stopped. To finish the hidden maps without starting over:\n'
              f'    python build_tiles.py --resume {tmp} {out}', flush=True)
        raise
    finish_tmp(tmp, keep, out)


def finish_tmp(tmp, keep, out):
    if keep:
        print(f'Temporary files kept. To recompute all hidden maps (e.g. after changing the weights):\n'
              f'    python build_tiles.py --resume {tmp} --keep --redo {out}', flush=True)
    else:
        shutil.rmtree(tmp, ignore_errors=True)
        print('Done. Temporary files removed.', flush=True)


def build(pairs, owner, out, tmp, workers, dem=None):
    # Each extract is read in its own process (one CPU core each), results are merged below.
    t0 = time.time()
    # two processes per extract (areas / items), merged per extract in region order
    jobs = [(ri, pbf, part) for ri, (pbf, _) in enumerate(pairs) for part in ('areas', 'items')]
    with ProcessPoolExecutor(max_workers=min(len(jobs), workers), initializer=low_priority) as ex:
        parts = list(ex.map(read_region, [j[0] for j in jobs], [j[1] for j in jobs], [owner] * len(jobs),
                            [tmp] * len(jobs), [j[2] for j in jobs]))
    results = [merge_parts(parts[2 * ri], parts[2 * ri + 1]) for ri in range(len(pairs))]

    buildings = defaultdict(lambda: array('f'))
    pois = defaultdict(list)
    water = defaultdict(set)
    seen = defaultdict(set)                        # shared tiles: ids already taken from an earlier extract
    n_lines = n_areas = 0
    land_raw, land_shared, land_seen, prot = {}, defaultdict(list), defaultdict(set), {}
    for res in results:                            # in region order, so the output does not depend on timing
        land_raw.update(res['land'])
        for k, a in res['prot'].items():
            prot.setdefault(k, a)
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
        land_raw[t] = draw_land(t[0], t[1], items)

    # Protected areas get ids 1..n (stable order); the list with names goes to protected.json.
    keys = sorted(prot)
    if len(keys) > 65535:
        sys.exit('Too many protected areas for 16-bit ids')
    prot_tiles = defaultdict(list)
    for pid, k in enumerate(keys, 1):
        a = prot[k]
        for rings in a['polys']:
            xs, ys = rings[0][0::2], rings[0][1::2]
            size = (max(xs) - min(xs)) * (max(ys) - min(ys))
            for t in tiles_of(rings[0]):
                if t in owner:
                    prot_tiles[t].append(((a['l'], -size), pid, rings))
    with open(os.path.join(out, 'protected.json'), 'w', encoding='utf-8') as f:
        json.dump([{'n': prot[k]['n'], 'l': prot[k]['l'], 't': prot[k]['t']} for k in keys], f, ensure_ascii=False)

    # Second step on all cores: add protected-area ids to the terrain maps and encode the PNGs.
    t1 = time.time()
    jobs = [(t[0], t[1], land_raw.get(t), [(pid, r) for _, pid, r in sorted(prot_tiles.get(t, []), key=lambda it: it[0])], tmp)
            for t in covered]
    with ProcessPoolExecutor(max_workers=workers, initializer=low_priority) as ex:
        land_png = dict(ex.map(finish_tile, jobs, chunksize=8))
    print(f'Terrain + protected areas encoded in {time.time() - t1:.0f} s ({len(keys)} protected areas)', flush=True)

    total_b = total_w = total_p = land_bytes = 0
    for tx, ty in covered:
        d = os.path.join(out, str(TILE_Z), str(tx))
        os.makedirs(d, exist_ok=True)
        arr = buildings.get((tx, ty), array('f'))
        total_b += len(arr) // 2
        save_b16(os.path.join(d, f'{ty}.b16'), np.frombuffer(arr, np.float32))
        old = os.path.join(d, f'{ty}.bin')
        if os.path.exists(old):
            os.remove(old)                         # replaced by .b16 (generated file, rebuilt from OSM)
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
        png = land_png[(tx, ty)]
        land_bytes += len(png)
        with open(os.path.join(d, f'{ty}.land.png'), 'wb') as f:
            f.write(png)

    # Third step: hidden / noise / access maps (needs the houses written above).
    hide_step(covered, tmp, out, len(pairs), workers, redo=True, dem=dem)   # new data: always recompute

    # Merge with an existing index so several regions can share one data folder.
    idx_path = os.path.join(out, 'index.json')
    tiles = set()
    if os.path.exists(idx_path):
        with open(idx_path, encoding='utf-8') as f:
            tiles.update(json.load(f).get('tiles', []))
    tiles.update(f'{x}/{y}' for x, y in covered)
    with open(idx_path, 'w', encoding='utf-8') as f:
        # 'built' doubles as the cache-busting version for tile URLs in the app, so include the time
        json.dump({'z': TILE_Z, 'built': time.strftime('%Y-%m-%dT%H:%M'), 'bfmt': 'u16', 'tiles': sorted(tiles)}, f)

    print(f'Done in {time.time() - t0:.0f} s: {len(covered)} tiles, {total_b:,} buildings, '
          f'{total_p:,} points of interest, {total_w:,} water points '
          f'({n_lines:,} rivers/streams, {n_areas:,} lakes), terrain {land_bytes / 1e6:.1f} MB -> {out}/')


if __name__ == '__main__':
    main()
