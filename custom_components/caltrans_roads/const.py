"""Constants for the Caltrans Road Conditions integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "caltrans_roads"
NAME: Final = "Caltrans Road Conditions"
VERSION: Final = "0.1.0"

ATTRIBUTION: Final = "Data from Caltrans public feeds. Unofficial; not affiliated with or endorsed by Caltrans."

# One file per district and feed, refreshed by Caltrans once a minute.
# Documented at https://cwwp2.dot.ca.gov/documentation/
FEED_URL: Final = "https://cwwp2.dot.ca.gov/data/d{district}/{feed}/{feed}StatusD{district:02d}.json"

FEED_CHAIN_CONTROL: Final = "cc"
FEED_WEATHER: Final = "rwis"
FEED_SIGNS: Final = "cms"
FEED_CLOSURES: Final = "lcs"
FEED_CAMERAS: Final = "cctv"

FEEDS: Final = (FEED_CHAIN_CONTROL, FEED_WEATHER, FEED_SIGNS, FEED_CLOSURES, FEED_CAMERAS)

FEED_LABELS: Final = {
    FEED_CHAIN_CONTROL: "chain control",
    FEED_WEATHER: "road weather",
    FEED_SIGNS: "message sign",
    FEED_CLOSURES: "lane closure",
    FEED_CAMERAS: "camera",
}

# Cameras are many and off by default.
DEFAULT_FEEDS: Final = [FEED_CHAIN_CONTROL, FEED_WEATHER, FEED_SIGNS, FEED_CLOSURES]

# Caltrans districts and the area each covers.
DISTRICTS: Final = {
    1: "Eureka (North Coast)",
    2: "Redding (Northeast)",
    3: "Marysville / Sacramento / Lake Tahoe",
    4: "Oakland (Bay Area)",
    5: "San Luis Obispo (Central Coast)",
    6: "Fresno (Central Valley)",
    7: "Los Angeles / Ventura",
    8: "San Bernardino / Riverside",
    9: "Bishop (Eastern Sierra)",
    10: "Stockton",
    11: "San Diego / Imperial",
    12: "Orange County",
}

# Which district publishes which feed. Taken from the Caltrans documentation pages
# (cc.htm and rwis.htm list districts) and confirmed against the live files on
# 26 Oct 2026 (California time): districts 4, 5 and 12 return HTTP 500 for chain control and road
# weather, districts 7 and 11 for road weather. Signs, closures and cameras exist
# for every district.
_ALL: Final = frozenset(range(1, 13))
DISTRICT_FEEDS: Final = {
    FEED_CHAIN_CONTROL: frozenset({1, 2, 3, 6, 7, 8, 9, 10, 11}),
    FEED_WEATHER: frozenset({1, 2, 3, 6, 8, 9, 10}),
    FEED_SIGNS: _ALL,
    FEED_CLOSURES: _ALL,
    FEED_CAMERAS: _ALL,
}

# How often each feed is fetched. The files change once a minute at most; chain
# control and signs matter most and are small. Lane closures are large (2-15 MB
# per district) and planned in advance, so they are fetched slowly. Camera
# metadata barely changes; the images themselves are fetched on demand.
UPDATE_INTERVALS: Final = {
    FEED_CHAIN_CONTROL: timedelta(minutes=3),
    FEED_SIGNS: timedelta(minutes=3),
    FEED_WEATHER: timedelta(minutes=5),
    FEED_CLOSURES: timedelta(minutes=30),
    FEED_CAMERAS: timedelta(hours=6),
}

# Entities keep their last good value through short outages and go unavailable
# only once the data is older than this.
STALE_AFTER: Final = {
    FEED_CHAIN_CONTROL: timedelta(minutes=30),
    FEED_SIGNS: timedelta(minutes=30),
    FEED_WEATHER: timedelta(hours=1),
    FEED_CLOSURES: timedelta(hours=3),
    FEED_CAMERAS: timedelta(hours=24),
}

# Chain control and sign files stamp every record with the time the file was written.
# A file whose newest record is older than this is an old copy (seen live: on 26 Oct 2026
# the district 3 road weather file was serving readings from 19 May) and counts as a failure.
FILE_STALE_AFTER: Final = {
    FEED_CHAIN_CONTROL: timedelta(hours=2),
    FEED_SIGNS: timedelta(hours=2),
    # Weather records carry each station's own report time; if even the newest station is
    # this old, the whole file is an old copy (district 3 on 26 Oct 2026 was serving May).
    FEED_WEATHER: timedelta(hours=6),
}

# Large files load in the background so Home Assistant's startup does not wait for them.
BACKGROUND_FEEDS: Final = frozenset({"lcs", "cctv"})

REQUEST_TIMEOUTS: Final = {
    FEED_CHAIN_CONTROL: 30,
    FEED_SIGNS: 30,
    FEED_WEATHER: 30,
    FEED_CLOSURES: 120,
    FEED_CAMERAS: 60,
}

# While Home Assistant is starting, the files setup waits for get this long at most, so a
# Caltrans server that accepts the connection and then hangs cannot hold up startup for the
# full timeout above (seen in a live test: 30 s). A slow file just loads on the next retry.
STARTUP_REQUEST_TIMEOUT: Final = 10

# A feed that has not loaded for this long raises a repair issue.
REPAIR_AFTER: Final = timedelta(hours=6)

# A weather station whose own record is older than this is shown as unavailable.
STATION_STALE_AFTER: Final = timedelta(hours=6)

# A closure the crew never marked as finished stops counting this long after its planned end.
CLOSURE_GRACE: Final = timedelta(hours=2)

# Cap on list attributes so the state machine and recorder stay small.
MAX_ATTR_ITEMS: Final = 20

# Camera images are cached this long; Caltrans updates them every few minutes.
CAMERA_CACHE_SECONDS: Final = 60

CONF_DISTRICT: Final = "district"
CONF_MODE: Final = "mode"
CONF_ROUTES: Final = "routes"
CONF_RADIUS: Final = "radius"
CONF_FEEDS: Final = "data_types"

MODE_ROUTES: Final = "routes"
MODE_NEAR: Final = "near"

DEFAULT_RADIUS_KM: Final = 25
MAX_RADIUS_KM: Final = 200

NEAR_HOME_KEY: Final = "near_home"
