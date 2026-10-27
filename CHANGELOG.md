# Changelog

## 0.1.0 (unreleased)

First release.

### New

- Set up from the UI: pick a Caltrans district, then either the routes to follow (listed from the live feeds) or
  "everything near home" within a distance you choose. Change routes, distance and data types later under Configure.
- **Chain controls:** per route, the worst requirement in effect (none, trucks only, R-1, R-2, R-3, traffic held or
  escorted, closed) with each segment's status, description and time of change, plus a "chains required" on/off sensor that stays on when part of the route is closed or held.
- **Road weather stations:** pavement temperature, pavement condition (dry, wet, ice warning, snow warning and so on),
  air temperature, visibility, wind speed and gust for stations on your routes. Stations that stopped reporting show
  as unavailable instead of an old reading.
- **Message signs:** how many signs on a route show a message, with the text. Per-sign sensors are available but off
  by default.
- **Lane closures:** the number of closures in effect on a route, with details for up to 20.
- **Traffic cameras** (off by default): still images from Caltrans cameras on your routes, fetched at most once a
  minute.
- Survives Caltrans outages: values are kept through short outages and go unavailable only when they are too old;
  a repair notice appears if a feed has not loaded for six hours. A chain control or sign file that Caltrans keeps
  serving as an old copy (more than two hours old) counts as an outage, so stale chain controls never look current. Handles districts that do not publish every feed,
  and the malformed and non-UTF-8 files some districts publish.
- Diagnostics download showing each feed's state (your home location is not included).
