"""Tests for build_tiles.py (standard library only: python -m unittest discover tests).

They use small synthetic inputs (no OSM or DEM files needed) and check that the models still
behave as calibrated, so a speed-up or refactoring can't silently change the results."""
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
from array import array

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import build_tiles as bt  # noqa: E402

TX, TY = 2137, 1350          # any zoom-12 tile at ~52 N
MEADOW, FOREST = 2, 8


def code(name):
    return bt.SIGHT_CLASSES.index(name) + 1


class Scene:
    """A 3x3-tile neighbourhood in a temp folder: terrain, roads/paths, optional elevation."""

    def __init__(self, land=MEADOW):
        self.tmp, self.out = tempfile.mkdtemp(), tempfile.mkdtemp()
        os.makedirs(os.path.join(self.out, '12', str(TX)))
        self.lines = {(dx, dy): [] for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
        self.land = land
        self.z = None

    def ns_line(self, dx, x, kind):          # north-south line at tile-relative x (0..1) in tile (dx, *)
        for dy in (-1, 0, 1):
            X, Y = TX + dx, TY + dy
            self.lines[(dx, dy)].append((kind, array('f', [X + x, Y, X + x, Y + 1])))

    def grid(self, spacing, kind):           # path grid in every tile, every `spacing` pixels
        for (dx, dy) in self.lines:
            X, Y = TX + dx, TY + dy
            for k in range(0, bt.HP, spacing):
                self.lines[(dx, dy)].append((kind, array('f', [X + k / bt.HP, Y, X + k / bt.HP, Y + 1])))
                self.lines[(dx, dy)].append((kind, array('f', [X, Y + k / bt.HP, X + 1, Y + k / bt.HP])))

    def hidden(self, numba=True):
        for (dx, dy), items in self.lines.items():
            X, Y = TX + dx, TY + dy
            np.save(os.path.join(self.tmp, f'l_{X}_{Y}.npy'), np.full((bt.HP, bt.HP), self.land, np.uint8))
            np.save(os.path.join(self.tmp, f'r_{X}_{Y}_0.npy'), bt.draw_lines(X, Y, items))
        saved_sample, saved_njit = bt.dem_sample, getattr(bt, 'njit', None)
        try:
            if self.z is not None:
                bt.dem_sample = lambda folder, lat, lon: self.z
            if not numba and hasattr(bt, 'njit'):
                bt.njit = None
            bt.hide_tile((TX, TY, self.tmp, self.out, 1, 'fake' if self.z is not None else None))
        finally:
            bt.dem_sample = saved_sample
            if hasattr(bt, 'njit'):
                bt.njit = saved_njit
        return read_hide(self.out)

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.out, ignore_errors=True)


def read_hide(out):
    """hide.png (hidden, 256) + nb.png (noise, path distance, 128) -> 256x256x3 array like the app builds."""
    d = os.path.join(out, '12', str(TX))
    h = np.asarray(Image.open(os.path.join(d, f'{TY}.hide.png'))).astype(int)
    nb = np.asarray(Image.open(os.path.join(d, f'{TY}.nb.png'))).astype(int).repeat(2, 0).repeat(2, 1)
    return np.stack([h, nb[..., 0], nb[..., 1]], -1)


PX = 40075016.686 * math.cos(math.radians(bt.tile_lat(TY + 0.5))) / bt.N / bt.HP   # metres per pixel
pct = lambda im, x, y=128: im[y, x, 0] / 2.55


class TestWayKind(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(bt.way_kind({'highway': 'footway'})[0], code('footway'))
        self.assertEqual(bt.way_kind({'highway': 'track'})[2], 1)                 # bikeable
        self.assertEqual(bt.way_kind({'highway': 'motorway'})[2], 0)              # not bikeable
        self.assertEqual(bt.way_kind({'highway': 'motorway_link'})[1], bt.WAYS['motorway'][1] - 3)
        self.assertIsNone(bt.way_kind({'highway': 'residential', 'tunnel': 'yes'}))
        self.assertEqual(bt.way_kind({'barrier': 'hedge'})[3], 1)
        self.assertIsNone(bt.way_kind({'highway': 'proposed'}))

    def test_draw_keeps_most_visible(self):
        a = array('f', [TX + 0.5, TY, TX + 0.5, TY + 1])
        r = bt.draw_lines(TX, TY, [((code('track'), 0, 1, 0), a), ((code('footway'), 0, 1, 0), a)])
        col = r[0][:, 128]
        self.assertTrue((col[col > 0] == code('footway')).all())   # footway (0.8) beats track (0.4)


class TestHiddenModel(unittest.TestCase):
    """Calibration from the owner's session (see WAYS / E0 comments in build_tiles.py)."""

    def test_meadow_single_path(self):
        sc = Scene(MEADOW); sc.ns_line(0, 0.5, (code('footway'), 0, 1, 0))
        im = sc.hidden(); sc.cleanup()
        self.assertLess(pct(im, 129), 10)                        # next to the path: in plain view
        d500 = 128 + round(500 / PX)
        self.assertTrue(15 <= pct(im, d500) <= 45, pct(im, d500))  # 500 m over a meadow: still easy to spot

    def test_forest_track_grid(self):
        sc = Scene(FOREST); sc.grid(6, (code('track'), 0, 1, 0))   # tracks every ~140 m
        im = sc.hidden(); sc.cleanup()
        self.assertGreater(pct(im, 129, 129), 70)                # ~70 m from the tracks: mostly hidden
        self.assertLess(pct(im, 126, 129), 10)                   # on a track

    def test_ridge_blocks_small_rise_does_not(self):
        for h, behind_min, behind_max in ((15, 95, 100), (3, 0, 30)):
            sc = Scene(MEADOW); sc.ns_line(0, 0.5, (code('footway'), 0, 1, 0))
            sc.z = np.zeros((3 * bt.HP, 3 * bt.HP), np.float32)
            sc.z[:, 389:392] = h                                    # ridge ~120 m east of the path
            im = sc.hidden(); sc.cleanup()
            v = pct(im, 128 + 10)                                   # 235 m east, behind the ridge
            self.assertTrue(behind_min <= v <= behind_max, (h, v))
            self.assertLess(pct(im, 128 - 10), 30)                  # the open west side is unaffected

    def test_noise_line_source(self):
        sc = Scene(MEADOW); sc.ns_line(-1, 0.5, (code('primary'), bt.WAYS['primary'][1], 1, 0))
        im = sc.hidden(); sc.cleanup()
        for x in (0, 20, 40):                                      # up to ~4 km: the grid reaches ~6 km
            d = (x + 128) * PX                                     # road at the middle of the west tile
            expect = bt.WAYS['primary'][1] - 10 * math.log10(d / 10) - bt.AIR * d
            self.assertAlmostEqual(im[128, x, 1], expect, delta=3, msg=f'{d:.0f} m')

    def test_bike_distance_short_path_no_stripes(self):
        # regression: with 16 rays a short path could fall between two rays -> stripes of "2.5 km+"
        sc = Scene(MEADOW)
        X, Y = TX, TY
        seg = ((code('track'), 0, 1, 0), array('f', [X + 0.30, Y + 0.30, X + 0.33, Y + 0.31]))
        sc.lines[(0, 0)].append(seg)
        im = sc.hidden(); sc.cleanup()
        py, px_ = np.nonzero(bt.draw_lines(X, Y, [seg])[2])         # the path pixels as drawn
        yy, xx = np.mgrid[0:bt.HP, 0:bt.HP]
        d_true = np.sqrt(((yy[..., None] - py) ** 2 + (xx[..., None] - px_) ** 2).min(-1)) * PX
        near = d_true < 2000
        err = np.abs(im[..., 2] * 10 - d_true)[near]
        self.assertLess(err.max(), 60, err.max())                   # 2x2 blocks + 10 m steps + line width

    def test_bike_distance(self):
        sc = Scene(MEADOW); sc.ns_line(0, 0.5, (code('track'), 0, 1, 0))
        im = sc.hidden(); sc.cleanup()
        self.assertAlmostEqual(im[128, 128 + 20, 2] * 10, 20 * PX, delta=40)   # 2x2 minimum: up to 1 px shorter


@unittest.skipUnless(getattr(bt, 'njit', None), 'numba not installed')
class TestNumbaMatchesNumpy(unittest.TestCase):
    def test_identical(self):
        sc = Scene(FOREST); sc.grid(9, (code('path'), 0, 1, 0)); sc.ns_line(-1, 0.3, (code('secondary'), 63, 1, 0))
        sc.z = (np.indices((3 * bt.HP, 3 * bt.HP)).sum(0) % 97).astype(np.float32) * 0.4   # bumpy ground
        a = sc.hidden(numba=True)
        b = sc.hidden(numba=False); sc.cleanup()
        self.assertEqual(np.abs(a - b).max(), 0)


class TestSlope(unittest.TestCase):
    def run_slope(self, zfun):
        out = tempfile.mkdtemp(); os.makedirs(os.path.join(out, '12', str(TX)))
        saved = bt.dem_sample
        def fake(folder, lat, lon):              # elevation from metres east/north of the tile centre
            lat0, lon0 = bt.tile_lat(TY + 0.5), (TX + 0.5) / bt.N * 360 - 180
            east = (lon - lon0) * 111320 * math.cos(math.radians(lat0)); north = (lat - lat0) * 110574
            return zfun(east, north).astype(np.float32)
        try:
            bt.dem_sample = fake
            bt.slope_tile((TX, TY, 'fake', out))
        finally:
            bt.dem_sample = saved
        im = np.asarray(Image.open(os.path.join(out, '12', str(TX), f'{TY}.slope.png'))).astype(int)
        shutil.rmtree(out, ignore_errors=True)
        return im

    def test_tilted_plane(self):
        im = self.run_slope(lambda e, n: 200 + 0.1 * e)            # rises 10 % towards the east
        c = im[64, 64]
        self.assertAlmostEqual(c[0] / 4, math.degrees(math.atan(0.1)), delta=0.5)
        self.assertEqual((c[1] >> 4) * 22.5, 270)                   # faces (downhill) west
        self.assertEqual(c[1] & 15, 0)                              # a plane has no hollow

    def test_bowl_is_hollow(self):
        im = self.run_slope(lambda e, n: 100 + 20 * np.minimum(1, np.hypot(e, n) / 800))   # 20 m deep bowl
        self.assertGreaterEqual((im[64, 64, 1] & 15) * 2, 8)


class TestBumpVersion(unittest.TestCase):
    def test_bump(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, 'index.json'), 'w') as f:
            json.dump({'z': 12, 'built': 'old', 'tiles': ['1/2']}, f)
        bt.bump_version(d)
        with open(os.path.join(d, 'index.json'), encoding='utf-8') as f:
            idx = json.load(f)
        self.assertNotEqual(idx['built'], 'old'); self.assertEqual(idx['tiles'], ['1/2'])
        shutil.rmtree(d, ignore_errors=True)




class TestFormats(unittest.TestCase):
    def test_hide_files(self):
        sc = Scene(MEADOW); sc.ns_line(0, 0.5, (code('footway'), 0, 1, 0)); sc.hidden()
        d = os.path.join(sc.out, '12', str(TX))
        h, nb = Image.open(os.path.join(d, f'{TY}.hide.png')), Image.open(os.path.join(d, f'{TY}.nb.png'))
        self.assertEqual((h.mode, h.size), ('L', (bt.HP, bt.HP)))
        self.assertEqual((nb.mode, nb.size), ('RGB', (bt.HP // 2, bt.HP // 2)))
        self.assertTrue((np.asarray(h) % bt.HIDE_Q == 0).all() or (np.asarray(h) == 255).any())
        sc.cleanup()

    def test_palette_png_lossless(self):
        rng = np.random.default_rng(1)
        a = rng.integers(0, 17, (64, 64, 3)).astype(np.uint8); a[..., 1:] = 0; a[5:9, 5:9, 2] = 7
        back = np.asarray(Image.open(__import__('io').BytesIO(bt.palette_png(a))).convert('RGB'))
        self.assertTrue((back == a).all())


class TestDayPoints(unittest.TestCase):
    """Resupply points and sights: read from a tiny OSM file, a shop in a building counts twice."""

    def test_read_region(self):
        lon0 = bt.tile_lon(TX) + 0.01
        lat0 = bt.tile_lat(TY) - 0.01
        nodes = [  # id, dlon, dlat, tags
            (1, 0, 0, {'shop': 'supermarket', 'name': 'Markt', 'opening_hours': 'Mo-Sa 07:00-21:00'}),
            (2, .001, 0, {'natural': 'peak'}),                        # unnamed peak: left out
            (3, .002, 0, {'natural': 'peak', 'name': 'Berg'}),
            (4, .003, 0, {'amenity': 'drinking_water'}),              # night category 'water' only
            (5, .004, 0, {'amenity': 'fuel', 'name': 'Tanke'}),
        ] + [(10 + k, .005 + dx, dy, {}) for k, (dx, dy) in enumerate([(0, 0), (.0002, 0), (.0002, .0002), (0, .0002)])]
        xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6">']
        for i, dx, dy, tags in nodes:
            xml.append(f'<node id="{i}" version="1" lat="{lat0 + dy:.7f}" lon="{lon0 + dx:.7f}">'
                       + ''.join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items()) + '</node>')
        xml.append('<way id="100" version="1">' + ''.join(f'<nd ref="{i}"/>' for i in (10, 11, 12, 13, 10))
                   + '<tag k="building" v="yes"/><tag k="shop" v="bakery"/></way>')
        xml.append('</osm>')
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, 'a.osm')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(xml))
            res = bt.read_region(0, path, {(TX, TY): 0}, tmp, part='items')
        finally:
            shutil.rmtree(tmp)
        pois = res['p'][(TX, TY)]
        got = sorted((p['c'], p.get('t'), p['n']) for p in pois)
        self.assertEqual(got, [('groceries', 'bakery', ''), ('groceries', 'fuel', 'Tanke'),
                               ('groceries', 'supermarket', 'Markt'), ('sights', 'peak', 'Berg'), ('water', None, '')])
        self.assertEqual(next(p for p in pois if p['n'] == 'Markt')['o'], 'Mo-Sa 07:00-21:00')
        self.assertNotIn('o', next(p for p in pois if p['c'] == 'water'))
        self.assertEqual(len(res['b'][(TX, TY)]), 2)             # the bakery building is still a house

    def test_day_class(self):
        self.assertEqual(bt.day_class({'amenity': 'cafe'}), ('food', 'cafe'))
        self.assertEqual(bt.day_class({'shop': 'bicycle'}), ('bike', 'bicycle'))
        self.assertIsNone(bt.day_class({'shop': 'clothes'}))
        self.assertEqual(bt.poi_cats({'building': 'yes', 'amenity': 'toilets'}), [('buildings', None), ('toilets', 'toilets')])


if __name__ == '__main__':
    unittest.main()
