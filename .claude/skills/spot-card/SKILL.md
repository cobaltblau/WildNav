---
name: spot-card
description: "WildNav spot card and side panel in index.html. openSpot, the big score with its reason line, the six key-fact tiles (updateCardSummary), the card sections Score, Nearby, Night and Notes (cardSec, setSum, cardRow, updateCardScore, updateCardAround, radarSVG, analyseSpot), weather per night (Open-Meteo, loadWeather, weatherNights), cardWait, saved spots in localStorage wn:spots (GPX / JSON export, share link), the Here panel under the cursor (updateHover) and the phone bottom sheet. Use before changing the card, the panel or saved-spot storage."
---

# WildNav spot card and side panel (index.html)

"Compact on purpose" and "Nothing may jump when data arrives" are in CLAUDE.md ("Owner's design rules").

## Spot card (saved spots)
Clicking the map opens a spot card (`openSpot`). Compact on purpose (the owner asked for it): name,
big score + a one-line reason ("Held back most by …") + bar (`#sc-top`), a 2-column grid of six key-fact tiles
(`updateCardSummary`: terrain + protected area, hidden, nearest house, slope + cold hollow, noise, tonight's weather),
buttons, then collapsible `<details>` sections made with
`cardSec(key, icon, title, body)`, each with a summary line under the title (`setSum(key, html)`). No information twice:
"Score" (`updateCardScore`) = everything about the spot itself with its effect on the score (terrain, slope +
cold hollow, houses, hunting stands, hidden, quiet night, access, terrain within 100 m, protected area);
"Nearby" (`updateCardAround`) = compass map `radarSVG` from `analyseSpot` over the 3×3 tiles + the useful
places and their bonus; "Night" (key `wx`) = sunset, moon, next sunrise (SunCalc from cdnjs) then the weather per
night; "Notes" (rating + text). Section titles: one or two words. Rows are built with `cardRow(icon, title, value, second line)`.
Open sections are remembered in `localStorage['wn:cardOpen']`.
Weather: Open-Meteo (free, no key, CC BY 4.0, credited in the section), fetched only when a card
opens (`loadWeather`, cached 30 min per ~1 km, up to 3 tries before it shows "offline"), one row per night 20:00–08:00 (`weatherNights`). Saved spots live only in
`localStorage['wn:spots']`; export as GPX or JSON backup, import GPX/JSON, share via `#spot=lat,lon,name`.

## Side panel and "Here"
- Side panel (`#panel`, headings short: "Here", "Near you"): spot card (when open), "Here"
  (under the cursor, hidden on touch devices). "My spots" is a window opened from the top bar (`#spots-win`, see below). On desktop the panel floats over the map (the map is full
  width) and each section is its own rounded card, like the tool strip and legend (owner's wish).
  "Under the cursor" (`updateHover`) always shows the same rows ("—", "zoom in" or "…" when a value is missing),
  a reserved line under each place name, and one two-line hint at the bottom (also names a protected area),
  so the panel never changes height while the mouse moves.
  On phones (≤720 px) it is a bottom sheet: 150 px showing the card summary, tap/swipe the
  handle for 75 %.
