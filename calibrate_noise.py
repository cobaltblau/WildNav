#!/usr/bin/env python3
"""Calibrate the road noise levels (WAYS in build_tiles.py) against a reference noise map.

Compares WildNav's road noise (data/12/x/y.nb.png, night dB) with the DLR Noise2NAKO AI road noise
(Lden bands 55/60/65/70/75, 0 = below 55) at open-country points where one road class clearly dominates,
and fits a level shift per road class (interval-censored, Lden = night + DELTA).

Needs: pip install tifffile imagecodecs pyproj, a built data/ folder, and the DLR file
raw/dlr/RF_RoadLden_DE_2017.tif (https://download.geoservice.dlr.de/NOISE2NAKO/files/NOISE/AI_Prediction/,
CC BY 4.0). Build the data first, then:

usage: python calibrate_noise.py collect <pbf> <lat0> <lat1> <lon0> <lon1> <out.json>
       python calibrate_noise.py fit <samples.json> [...]
"""
import json, math, os, random, sys, time
import numpy as np
from PIL import Image

W = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, W)
import build_tiles as bt

N12 = 4096
DLR_TIF = os.path.join(W, 'raw', 'dlr', 'RF_RoadLden_DE_2017.tif')
ROAD_CLASSES = [k for k, v in bt.WAYS.items() if v[1] > 0]          # classes that make noise
OPEN_LAND = {0, 1, 2, 4}                                             # unmapped, field, meadow, heath
DELTA = 8.0                                                          # Lden - Lnight for road traffic (typical 7..10)


def merc(lat, lon):
    x = (lon + 180) / 360 * N12
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * N12
    return x, y


class Tiles:
    """our nb.png noise at a point (terrain classes: land_class() in collect)"""
    def __init__(self): self.c = {}
    def get(self, kind, tx, ty):
        k = (kind, tx, ty)
        if k not in self.c:
            p = os.path.join(W, 'data', '12', str(tx), f'{ty}.{kind}.png')
            self.c[k] = np.array(Image.open(p)) if os.path.exists(p) else None
        return self.c[k]
    def noise(self, lat, lon):
        x, y = merc(lat, lon); tx, ty = int(x), int(y)
        a = self.get('nb', tx, ty)
        return None if a is None else int(a[min(127, int((y - ty) * 128)), min(127, int((x - tx) * 128)), 0])


class Dlr:
    def __init__(self):
        import tifffile, pyproj
        self.t = tifffile.TiffFile(DLR_TIF); self.p = self.t.pages[0]
        self.tr = pyproj.Transformer.from_crs(4326, 3035, always_xy=True)
        g = self.t.geotiff_metadata; self.x0, self.y0 = g['ModelTiepoint'][3], g['ModelTiepoint'][4]
        self.tw = self.p.tilewidth; self.ntx = -(-self.p.shape[1] // self.tw); self.cache = {}
    def band(self, lat, lon):
        e, n = self.tr.transform(lon, lat)
        col, row = int((e - self.x0) / 10), int((self.y0 - n) / 10)
        if not (0 <= row < self.p.shape[0] and 0 <= col < self.p.shape[1]): return None
        i = (row // self.tw) * self.ntx + col // self.tw
        if i not in self.cache:
            fh = self.t.filehandle; fh.seek(self.p.dataoffsets[i])
            self.cache[i] = np.squeeze(self.p.decode(fh.read(self.p.databytecounts[i]), i)[0])
            if len(self.cache) > 4000: self.cache.pop(next(iter(self.cache)))
        v = int(self.cache[i][row % self.tw, col % self.tw])
        return None if v == 255 else v


def read_ways(pbf, bbox):
    import osmium
    lat0, lat1, lon0, lon1 = bbox
    roads, rails = [], []
    fp = osmium.FileProcessor(pbf).with_locations().with_filter(osmium.filter.KeyFilter('highway', 'railway'))
    for o in fp:
        if not o.is_way(): continue
        t = {k: v for k, v in o.tags}
        try: pts = [(n.lat, n.lon) for n in o.nodes]
        except Exception: continue
        if not pts or not any(lat0 - .05 < a < lat1 + .05 and lon0 - .08 < b < lon1 + .08 for a, b in pts): continue
        kind = bt.way_kind(t)
        if not kind or not kind[1]: continue
        hw = t.get('highway')
        if hw:
            base = hw[:-5] if hw.endswith('_link') else hw
            if base in ROAD_CLASSES: roads.append((base, hw.endswith('_link'), pts))
        elif t.get('railway'):
            rails.append(pts)
    return roads, rails


def densify(pts, step=15):
    out = []
    for (a0, b0), (a1, b1) in zip(pts, pts[1:]):
        ky = 110574; kx = 111320 * math.cos(math.radians(a0))
        L = math.hypot((a1 - a0) * ky, (b1 - b0) * kx); n = max(1, int(L / step))
        for j in range(n): out.append((a0 + (a1 - a0) * j / n, b0 + (b1 - b0) * j / n, (b1 - b0) * kx, (a1 - a0) * ky))
    return out


def collect(pbf, bbox, out, per_class=900):
    t0 = time.time(); roads, rails = read_ways(pbf, bbox)
    print(f'{len(roads)} roads, {len(rails)} railways read in {time.time() - t0:.0f} s', flush=True)
    # spatial hash of all road points (for "which road is loudest here") and railway points (exclusion)
    CELL = 0.01
    grid = {}
    lvl = {c: bt.WAYS[c][1] for c in ROAD_CLASSES}
    for c, link, pts in roads:
        L = lvl[c] - (3 if link else 0)
        for a, b, *_ in densify(pts):
            grid.setdefault((int(a / CELL), int(b / CELL)), []).append((a, b, c, L))
    rgrid = {}
    for pts in rails:
        for a, b, *_ in densify(pts, 30): rgrid.setdefault((int(a / CELL), int(b / CELL)), []).append((a, b))
    T, D = Tiles(), Dlr()
    # land.png is a palette PNG: PIL gives palette indices -> map them to the class (red channel)
    def land_class(lat, lon):
        x, y = merc(lat, lon); tx, ty = int(x), int(y)
        k = ('landrgb', tx, ty)
        if k not in T.c:
            p = os.path.join(W, 'data', '12', str(tx), f'{ty}.land.png')
            T.c[k] = np.array(Image.open(p).convert('RGB'))[..., 0] if os.path.exists(p) else None
        a = T.c[k]
        return None if a is None else int(a[min(511, int((y - ty) * 512)), min(511, int((x - tx) * 512))])
    def loudest(lat, lon):
        """(class, distance, predicted level) of the loudest road within ~1.5 km, and the 2nd loudest level"""
        ky, kx = 110574, 111320 * math.cos(math.radians(lat)); best = {}
        ci, cj = int(lat / CELL), int(lon / CELL)
        for i in range(ci - 2, ci + 3):
            for j in range(cj - 2, cj + 3):
                for a, b, c, L in grid.get((i, j), ()):
                    d = max(5.0, math.hypot((a - lat) * ky, (b - lon) * kx))
                    v = L - 10 * math.log10(d / 10) - bt.AIR * d
                    if c not in best or v > best[c][2]: best[c] = (c, d, v)
        if not best: return None, None
        s = sorted(best.values(), key=lambda r: -r[2])
        return s[0], (s[1][2] if len(s) > 1 else -99)
    def near_rail(lat, lon, r=1200):
        ky, kx = 110574, 111320 * math.cos(math.radians(lat))
        ci, cj = int(lat / CELL), int(lon / CELL)
        return any(math.hypot((a - lat) * ky, (b - lon) * kx) < r
                   for i in range(ci - 2, ci + 3) for j in range(cj - 2, cj + 3) for a, b in rgrid.get((i, j), ()))
    lat0, lat1, lon0, lon1 = bbox
    rng = random.Random(7); res = []
    by_class = {c: [] for c in ROAD_CLASSES}
    for c, link, pts in roads:
        if not link: by_class[c].append(pts)
    for c in ROAD_CLASSES:
        segs = [p for pts in by_class[c] for p in densify(pts, 50)]
        segs = [s for s in segs if lat0 < s[0] < lat1 and lon0 < s[1] < lon1]
        if not segs: continue
        got = 0
        for _ in range(per_class * 12):
            if got >= per_class: break
            a, b, dx, dy = rng.choice(segs)
            d = math.exp(rng.uniform(math.log(15), math.log(900)))          # log-uniform 15..900 m
            L = math.hypot(dx, dy) or 1; side = rng.choice((-1, 1))
            nx, ny = -dy / L * side, dx / L * side                         # unit normal (east, north)
            lat = a + ny * d / 110574; lon = b + nx * d / (111320 * math.cos(math.radians(a)))
            if land_class(lat, lon) not in OPEN_LAND: continue
            top, second = loudest(lat, lon)
            if not top or top[0] != c or top[2] - second < 6: continue      # this class must clearly dominate
            if near_rail(lat, lon): continue
            ours, band = T.noise(lat, lon), D.band(lat, lon)
            if ours is None or band is None: continue
            res.append({'c': c, 'd': round(top[1], 1), 'pred': round(top[2], 1), 'ours': ours, 'dlr': band, 'lat': lat, 'lon': lon})
            got += 1
        print(f'{c:13s} {got} samples', flush=True)
    json.dump(res, open(out, 'w'))


def fit(files):
    from math import erf, sqrt
    res = [r for f in files for r in json.load(open(f))]
    Phi = lambda z: 0.5 * (1 + erf(z / sqrt(2)))
    def nll(rows, shift, sig):
        s = 0.0
        for r in rows:
            m = r['ours'] + DELTA + shift                                   # predicted Lden
            lo, hi = (-1e9, 55) if r['dlr'] == 0 else (r['dlr'], r['dlr'] + 5)
            p = Phi((hi - m) / sig) - Phi((lo - m) / sig)
            s -= math.log(max(p, 1e-9))
        return s
    def best(rows):
        cands = [(nll(rows, sh, sg), sh, sg) for sh in np.arange(-15, 10.1, 0.5) for sg in (2, 3, 4, 5, 6)]
        return min(cands)[1:]
    print(f'{"class":13s} {"n":>5s} {"shift":>6s} {"sigma":>5s}   shift by distance: 15-50 / 50-150 / 150-400 / 400-900 m')
    out = {}
    for c in ROAD_CLASSES + ['ALL']:
        rows = res if c == 'ALL' else [r for r in res if r['c'] == c]
        if len(rows) < 30: continue
        sh, sg = best(rows)
        bins = []
        for lo, hi in ((15, 50), (50, 150), (150, 400), (400, 900)):
            rb = [r for r in rows if lo <= r['d'] < hi]
            bins.append(f'{best(rb)[0]:+5.1f} ({len(rb)})' if len(rb) >= 25 else '   -     ')
        print(f'{c:13s} {len(rows):5d} {sh:+6.1f} {sg:5.1f}   ' + ' / '.join(bins))
        out[c] = sh
    return out


if __name__ == '__main__':
    if sys.argv[1] == 'collect':
        collect(sys.argv[2], tuple(map(float, sys.argv[3:7])), sys.argv[7])
    else:
        fit(sys.argv[2:])
