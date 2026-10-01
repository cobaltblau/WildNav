"""Smoke test of the app in a headless browser (Playwright + Chromium).

Starts a local server, opens the app, clicks the four tabs, opens Settings and My spots, clicks the map so a spot card
opens, checks a few pure functions in the page and collects console errors and uncaught exceptions.
Prints only "OK" or the problems (exit 1). Skipped (exit 0) without Playwright or without data/.

    python tests/smoke_app.py

One-time install: pip install playwright && python -m playwright install chromium
Map tiles are answered with an empty image (no load on the OSM tile servers); CDN libraries and the weather API are
used for real. The service worker is blocked so the current files are tested, not cached ones.
"""
import base64
import functools
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START = (52.272, 8.047)   # the app's default view (Osnabrück), inside the offline data
EMPTY_PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')
TILE_HOSTS = ('tile.openstreetmap.org', 'tile.opentopomap.org', 'tiles.maps.eox.at')

# pure functions: [JavaScript expression, expected value]
PURE = [
    ["typeKey('shop')", 'supply'],
    ["typeKey('break')", 'break'],
    ["typeKey('nonsense')", 'camp'],
    # 2026-09-30 is a Wednesday, 2026-10-03 a Saturday, 2026-10-04 a Sunday
    ["ohState('Mo-Fr 08:00-18:00', new Date(2026, 8, 30, 10, 0))", 'open'],
    ["ohState('Mo-Fr 08:00-18:00', new Date(2026, 8, 30, 19, 0))", 'closed'],
    ["ohState('Mo-Fr 08:00-18:00', new Date(2026, 9, 4, 10, 0))", 'closed'],
    ["ohState('Fr 18:00-02:00', new Date(2026, 9, 3, 1, 0))", 'open'],
    ["ohState('sunrise-sunset', new Date(2026, 8, 30, 10, 0))", None],
    ["ohState('', new Date(2026, 8, 30, 10, 0))", None],
]


def skip(why):
    print('SKIPPED:', why)
    sys.exit(0)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def main():
    try:
        from playwright.sync_api import sync_playwright, Error as PwError
    except ImportError:
        skip('Playwright is not installed (pip install playwright && python -m playwright install chromium)')
    if not os.path.isfile(os.path.join(ROOT, 'data', 'index.json')):
        skip('no data/ folder (data/index.json is missing)')

    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=ROOT))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_address[1]}/'
    problems = []

    def step(name, fn):
        try:
            fn()
        except Exception as e:  # a failed step is reported, the test goes on
            problems.append(f'{name}: {str(e).splitlines()[0]}')

    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except PwError as e:
                skip('Chromium for Playwright is missing (python -m playwright install chromium): ' + str(e).splitlines()[0])
            ctx = browser.new_context(viewport={'width': 1280, 'height': 800}, service_workers='block')
            ctx.route(lambda url: any(h in url for h in TILE_HOSTS),
                      lambda route: route.fulfill(status=200, content_type='image/png', body=EMPTY_PNG))
            page = ctx.new_page()

            def on_console(msg):
                if msg.type != 'error':
                    return
                url = (msg.location or {}).get('url', '')
                if msg.text.startswith('Failed to load resource') and not url.startswith(base):
                    return  # an outside service (CDN, weather) not reachable: not a bug in the app
                problems.append(f'console error: {msg.text[:300]}' + (f' ({url.replace(base, "")}:{msg.location.get("lineNumber")})' if url else ''))
            page.on('console', on_console)
            page.on('pageerror', lambda err: problems.append(f'uncaught exception: {str(err)[:300]}'))
            page.on('response', lambda r: r.url.startswith(base) and r.status >= 400 and
                    problems.append(f'HTTP {r.status} for {r.url.replace(base, "")}'))

            page.goto(base, wait_until='networkidle', timeout=60000)
            page.wait_for_function('typeof map !== "undefined" && typeof ACTS !== "undefined"', timeout=15000)

            def tab(a):
                page.click(f'#tabs .tab[data-a="{a}"]')
                page.wait_for_load_state('networkidle')
                if 'on' not in (page.get_attribute(f'#tabs .tab[data-a="{a}"]', 'class') or ''):
                    raise AssertionError('tab did not become active')
            for a in ('break', 'supply', 'route', 'sleep'):
                step(f'tab {a}', lambda a=a: tab(a))

            def window(button, win, close):
                page.click(button)
                page.wait_for_selector(win, state='visible', timeout=5000)
                page.click(close)
                page.wait_for_selector(win, state='hidden', timeout=5000)
            step('Settings', lambda: window('#btn-settings', '#settings', '#settings-close'))
            step('My spots', lambda: window('#btn-spots', '#spots-win', '#spots-x'))

            def spot_card():
                page.evaluate(f'map.setView([{START[0]}, {START[1]}], 13, {{animate: false}})')
                page.wait_for_load_state('networkidle')
                box = page.locator('#map').bounding_box()
                for dx, dy in ((0, 0), (-120, 80), (-200, -100)):   # try another place if a marker is in the way
                    page.mouse.click(box['x'] + box['width'] / 2 + dx, box['y'] + box['height'] / 2 + dy)
                    try:
                        page.wait_for_selector('#spot-card', state='visible', timeout=3000)
                        break
                    except PwError:
                        continue
                else:
                    raise AssertionError('no spot card opened after clicking the map')
                page.wait_for_function("(document.querySelector('#sc-top .big') || {}).textContent?.includes('%')",
                                       timeout=20000)
            step('spot card', spot_card)

            def pure():
                got = page.evaluate('exprs => exprs.map(e => { try { return [true, eval(e)]; } catch (x) { return [false, String(x)]; } })',
                                    [e for e, _ in PURE])
                for (expr, want), (ok, val) in zip(PURE, got):
                    if not ok:
                        problems.append(f'{expr} threw {val}')
                    elif val != want:
                        problems.append(f'{expr} = {val!r}, expected {want!r}')
            step('pure functions', pure)

            page.wait_for_timeout(500)   # let late errors arrive
            browser.close()
    finally:
        server.shutdown()

    if problems:
        print(f'{len(problems)} problem(s):')
        for p in problems[:30]:
            print(' -', p)
        sys.exit(1)
    print('OK')


if __name__ == '__main__':
    main()
