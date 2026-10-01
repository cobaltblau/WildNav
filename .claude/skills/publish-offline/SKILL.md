---
name: publish-offline
description: "WildNav offline and installable app, and publishing background. sw.js service worker caching (wn-lib, wn-data, wn-map, VERSION bump), Save this area, manifest.webmanifest, icons/, iOS home-screen quirks (status bar style), CORS for CDN and map tiles, and the gh-pages / main split with old data in the history (publish_site.py). Use before changing sw.js, caching, the manifest or icons, or touching git history."
---

# WildNav offline / installable app and publishing background

## Offline / installable app
- `sw.js` (service worker, registered from index.html): app files network-first, CDN files
  cache-first (`wn-lib`), `data/12/*` cache-first (`wn-data`, URLs carry `?v=<build>`; the app
  posts the current build and older tiles are deleted), map images network-first with offline
  fallback (`wn-map`, capped at 3000; no bulk downloads — OSM tile policy).
  **Bump `VERSION` in sw.js when its caching rules change.**
- CDN tags and base-map tiles use CORS (`crossorigin`) so responses can be cached;
  opaque responses are never cached (they inflate the storage quota).
- Settings → "Offline use": "Save this area" stores the data files of the visible tiles.
- `manifest.webmanifest` + `icons/` (PNG, made from the logo shapes with Pillow; iPhone needs
  `apple-touch-icon.png`). iOS keeps storage for home-screen apps; plain Safari may clear it.
- Status bar style must stay `black`, not `black-translucent`: iOS 26 has a bug that shifts the
  whole page up and leaves a gap at the bottom in home-screen apps drawn under the status bar.

## Publishing: old data in the history
(How to publish: CLAUDE.md, "Publishing".)
- The old data versions are still in `main`'s history from before the switch (~660 MB); removing them
  would mean rewriting history (only with the owner's explicit OK).
