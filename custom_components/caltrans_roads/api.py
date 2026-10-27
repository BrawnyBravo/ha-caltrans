"""
Fetch and parse the Caltrans CWWP2 JSON feeds.

Field meanings come from the Caltrans field descriptions and code charts at
https://cwwp2.dot.ca.gov/documentation/ (rwis-field-description.htm, rwis-chart.htm,
cc-chart.htm, lcs-field-description.htm). The road weather values follow NTCIP 1204,
which the field description cites for each field:

* temperatures: tenths of a degree Celsius, -1000 to 1000, 1001 means error
* wind speed: tenths of a metre per second, 65535 means error
* visibility: tenths of a metre, 1000001 means error
* direction: degrees, 361 means error

The temperature scaling was also checked against nearby National Weather Service
stations on 26 Oct 2026, California time (Blue Canyon 4.5 C feed vs 6 C airport, Truckee 0.3 C vs 0 C).

Timestamps without an epoch value are California local time.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import aiohttp

from .const import (
    FEED_CAMERAS,
    FEED_CHAIN_CONTROL,
    FEED_CLOSURES,
    FEED_SIGNS,
    FEED_URL,
    FEED_WEATHER,
)

if TYPE_CHECKING:
    from collections.abc import Callable

PACIFIC = ZoneInfo("America/Los_Angeles")

_MISSING = {"", "not reported", "n/a", "null", "none"}


class CaltransError(Exception):
    """Base error for feed problems."""


class FeedUnavailableError(CaltransError):
    """The feed could not be fetched (network error, timeout or HTTP error)."""


class FeedDataError(CaltransError):
    """The feed was fetched but could not be understood."""


# ---------------------------------------------------------------------------
# Models


@dataclass(frozen=True, slots=True)
class Location:
    """Where a record is."""

    route: str | None
    name: str
    direction: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    elevation_ft: int | None = None
    county: str | None = None
    nearby_place: str | None = None


@dataclass(frozen=True, slots=True)
class ChainControlPoint:
    """One chain control segment."""

    id: str
    location: Location
    in_service: bool
    status: str | None
    level: str | None
    description: str | None
    since: datetime | None
    record_time: datetime | None = None


@dataclass(frozen=True, slots=True)
class WeatherStation:
    """One road weather station (RWIS)."""

    id: str
    location: Location
    in_service: bool
    updated: datetime | None
    air_temperature: float | None = None
    dewpoint: float | None = None
    humidity: int | None = None
    surface_temperature: float | None = None
    surface_status: str | None = None
    freeze_point: float | None = None
    visibility_km: float | None = None
    wind_speed: float | None = None
    wind_gust: float | None = None
    wind_direction: int | None = None
    precipitation: str | None = None


@dataclass(frozen=True, slots=True)
class MessageSign:
    """One changeable message sign."""

    id: str
    location: Location
    in_service: bool
    updated: datetime | None
    message: str
    record_time: datetime | None = None


@dataclass(frozen=True, slots=True)
class LaneClosure:
    """One lane closure that is in effect."""

    id: str
    location: Location
    end_location: Location
    closure_type: str | None
    work_type: str | None
    facility: str | None
    lanes_closed: str | None
    total_lanes: str | None
    start: datetime | None
    end: datetime | None
    indefinite: bool


@dataclass(frozen=True, slots=True)
class TrafficCamera:
    """One traffic camera."""

    id: str
    location: Location
    in_service: bool
    image_url: str
    stream_url: str | None
    update_minutes: int | None = None


# ---------------------------------------------------------------------------
# Chain control codes (cc-chart.htm)

CHAIN_NONE = "none"
CHAIN_TRUCKS = "trucks_only"
CHAIN_R1 = "r1"
CHAIN_R2 = "r2"
CHAIN_R3 = "r3"
CHAIN_TRAFFIC = "traffic_control"
CHAIN_CLOSED = "closed"

# Lowest to highest.
CHAIN_LEVELS = [CHAIN_NONE, CHAIN_TRUCKS, CHAIN_R1, CHAIN_R2, CHAIN_R3, CHAIN_TRAFFIC, CHAIN_CLOSED]
CHAINS_REQUIRED_LEVELS = frozenset({CHAIN_R1, CHAIN_R2, CHAIN_R3})

_CHAIN_CODES = {
    "R-0": CHAIN_NONE,
    "W": CHAIN_NONE,  # no restrictions, watch for snow on pavement
    "R-1": CHAIN_R1,
    "R-1 MODIFIED": CHAIN_TRUCKS,  # trucks and vehicles towing trailers only
    "R-2": CHAIN_R2,
    "R-3": CHAIN_R3,
    "TH": CHAIN_TRUCKS,
    "TS": CHAIN_TRUCKS,
    "TTA": CHAIN_TRUCKS,
    "TTS": CHAIN_TRUCKS,
    "TTSD": CHAIN_TRUCKS,
    "MIN": CHAIN_TRUCKS,
    "MAX": CHAIN_TRUCKS,
    "ESC": CHAIN_TRAFFIC,  # CHP escorting all traffic
    "VM": CHAIN_TRAFFIC,  # vehicles metered
    "HT": CHAIN_TRAFFIC,  # all traffic held
    "RC": CHAIN_CLOSED,
    "ROAD CLOSED": CHAIN_CLOSED,
}


def chain_level(status: str | None) -> str | None:
    """Map a Caltrans chain control code to a level, or None when not recognised."""
    if status is None:
        return None
    return _CHAIN_CODES.get(re.sub(r"\s+", " ", status.strip().upper()))


def chain_rank(level: str) -> int:
    """Severity rank of a level."""
    return CHAIN_LEVELS.index(level)


# ---------------------------------------------------------------------------
# RWIS code charts (rwis-chart.htm)

SURFACE_STATUS = {
    "1": "other",
    "2": "error",
    "3": "dry",
    "4": "trace_moisture",
    "5": "wet",
    "6": "chemically_wet",
    "7": "ice_warning",
    "8": "ice_watch",
    "9": "snow_warning",
    "10": "snow_watch",
    "11": "absorption",
    "12": "dew",
    "13": "frost",
    "14": "absorption_at_dewpoint",
}

PRECIPITATION_SITUATION = {
    "1": "other",
    "2": "unknown",
    "3": "no precipitation",
    "4": "unidentified slight",
    "5": "unidentified moderate",
    "6": "unidentified heavy",
    "7": "snow slight",
    "8": "snow moderate",
    "9": "snow heavy",
    "10": "rain slight",
    "11": "rain moderate",
    "12": "rain heavy",
    "13": "frozen precipitation slight",
    "14": "frozen precipitation moderate",
    "15": "frozen precipitation heavy",
}


# ---------------------------------------------------------------------------
# Small value helpers


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if text.lower() in _MISSING:
        return None
    return text


def _number(value: Any) -> float | None:
    text = _text(value)
    if text is None:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return number


def _scaled(value: Any, *, scale: float, error: float, low: float, high: float) -> float | None:
    """Scale a raw NTCIP integer, dropping the error value and anything physically implausible."""
    raw = _number(value)
    if raw is None or raw == error:
        return None
    scaled = raw / scale
    if not low <= scaled <= high:
        return None
    return round(scaled, 2)


def _temperature(value: Any) -> float | None:
    # Pavement and dewpoint: outside -60..70 C is a sensor fault.
    return _scaled(value, scale=10, error=1001, low=-60, high=70)


def _air_temperature(value: Any) -> float | None:
    # California's air has not been colder than -40 C or hotter than 57 C at a highway station;
    # live 26 Oct 2026, a faulted district 10 station reported -45.5 C with 0% humidity.
    return _scaled(value, scale=10, error=1001, low=-40, high=57)


def _wind(value: Any) -> float | None:
    # 65535 is the error value; anything above 90 m/s (200 mph) is a sensor fault.
    return _scaled(value, scale=10, error=65535, low=0, high=90)


def _bool(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def _local_time(date: Any, time: Any) -> datetime | None:
    """Combine a California local date and time into an aware UTC datetime."""
    date_text, time_text = _text(date), _text(time)
    if date_text is None or time_text is None:
        return None
    try:
        naive = datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007
    except ValueError:
        return None
    return naive.replace(tzinfo=PACIFIC).astimezone(UTC)


def _epoch(value: Any) -> datetime | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    try:
        return datetime.fromtimestamp(number, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _record_time(stamp: dict[str, Any] | None) -> datetime | None:
    stamp = stamp or {}
    return _epoch(stamp.get("recordEpoch")) or _local_time(stamp.get("recordDate"), stamp.get("recordTime"))


def _location(raw: dict[str, Any] | None, prefix: str = "") -> Location:
    raw = raw or {}

    def get(key: str) -> Any:
        if prefix:
            key = prefix + key[0].upper() + key[1:]
        return raw.get(key)

    lat, lon = _number(get("latitude")), _number(get("longitude"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        lat = lon = None
    elevation = _number(get("elevation"))
    return Location(
        route=_text(get("route")),
        name=_text(get("locationName")) or "Unnamed location",
        direction=_text(get("direction")),
        latitude=lat,
        longitude=lon,
        elevation_ft=round(elevation) if elevation is not None else None,
        county=_text(get("county")),
        nearby_place=_text(get("nearbyPlace")),
    )


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    r_lat1, r_lat2 = math.radians(lat1), math.radians(lat2)
    d_lat, d_lon = r_lat2 - r_lat1, math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(r_lat1) * math.cos(r_lat2) * math.sin(d_lon / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Decoding

# Seen live in district 10 road weather on 26 Oct 2026 (California time): two table entries written one
# after the other without the "},{" between them, which makes the whole file invalid JSON.
_MISSING_ENTRY_SEPARATOR = re.compile(r'\}(\s*)"(\w+Entry)"(\s*):')


def decode_payload(raw: bytes) -> list[dict[str, Any]]:
    """Decode a feed file into its list of records."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # District 11 chain control is written in Windows-1252 (Spanish place names).
        text = raw.decode("cp1252", errors="replace")
    text = text.lstrip("﻿")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as err:
        repaired, count = _MISSING_ENTRY_SEPARATOR.subn(r'}},\1{"\2"\3:', text)
        if not count:
            msg = f"Feed is not valid JSON: {err}"
            raise FeedDataError(msg) from err
        try:
            payload = json.loads(repaired)
        except json.JSONDecodeError as err2:
            msg = f"Feed is not valid JSON: {err2}"
            raise FeedDataError(msg) from err2
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        msg = "Feed has no data list"
        raise FeedDataError(msg)
    return [item for item in payload["data"] if isinstance(item, dict)]


def _records(raw: bytes, key: str) -> list[dict[str, Any]]:
    out = []
    for item in decode_payload(raw):
        record = item.get(key)
        # Entities are keyed by the record index; a record without one cannot be followed.
        if isinstance(record, dict) and str(record.get("index") or "").strip():
            out.append(record)
    # Indexes are meant to be unique but are not always (district 3 signs had "1234" twice on
    # 26 Oct 2026). Give duplicates an id that also names the place, so each keeps its own entity.
    counts = Counter(str(r["index"]).strip() for r in out)
    for record in out:
        index = str(record["index"]).strip()
        if counts[index] > 1:
            name = (
                (record.get("location") or {}).get("locationName") if isinstance(record.get("location"), dict) else None
            )
            slug = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")
            record["_id"] = f"{index}_{slug}" if slug else index
        else:
            record["_id"] = index
    return out


# ---------------------------------------------------------------------------
# Parsers. Each skips a record it cannot read rather than failing the whole feed.


def parse_chain_control(raw: bytes) -> list[ChainControlPoint]:
    """Parse a chain control (cc) file."""
    points = []
    for rec in _records(raw, "cc"):
        try:
            status_data = rec.get("statusData") or {}
            stamp = status_data.get("statusTimestamp") or {}
            status = _text(status_data.get("status"))
            points.append(
                ChainControlPoint(
                    id=rec["_id"],
                    location=_location(rec.get("location")),
                    in_service=_bool(rec.get("inService")),
                    status=status,
                    level=chain_level(status),
                    description=_text(status_data.get("statusDescription")),
                    since=_local_time(stamp.get("statusDate"), stamp.get("statusTime")),
                    record_time=_record_time(rec.get("recordTimestamp")),
                )
            )
        except (AttributeError, TypeError):
            continue
    return points


# essPavementSensorError (NTCIP 1204): 2 = no error; 3 no response, 4 cut cable, 5 short circuit, 1 other.
_PAVEMENT_OK = {"2", None}


def _first(table: list[Any] | None, entry_key: str, value_key: str, convert: Callable[[Any], Any]) -> Any:
    for item in table or []:
        if not isinstance(item, dict):
            continue
        entry = item.get(entry_key) or {}
        # A pavement sensor reporting a fault reads 0 C and "dry" (live district 3, 26 Oct 2026); skip it.
        if _text(entry.get("essPavementSensorError")) not in _PAVEMENT_OK:
            continue
        value = convert(entry.get(value_key))
        if value is not None:
            return value
    return None


def _surface_status(value: Any) -> str | None:
    status = SURFACE_STATUS.get(_text(value) or "")
    # "error" is a sensor fault, not a road condition.
    return None if status == "error" else status


def parse_weather(raw: bytes) -> list[WeatherStation]:
    """Parse a road weather (rwis) file."""
    stations = []
    for rec in _records(raw, "rwis"):
        try:
            data = rec.get("rwisData") or {}
            temps = data.get("temperatureData") or {}
            wind = data.get("windData") or {}
            humid = data.get("humidityPrecipData") or {}
            vis = data.get("visibilityData") or {}
            pavement = (data.get("pavementSensorData") or {}).get("essPavementSensorTable")
            entry = "essPavementSensorEntry"
            humidity = _number(humid.get("essRelativeHumidity"))
            humidity = humidity if humidity is not None and 0 <= humidity <= 100 else None
            air = _first(
                temps.get("essTemperatureSensorTable"),
                "essTemperatureSensorEntry",
                "essAirTemperature",
                _air_temperature,
            )
            # 0% relative humidity does not occur in open air; it means the humidity/temperature probe failed.
            probe_failed = humidity == 0
            direction = _number(wind.get("essAvgWindDirection"))
            visibility = _scaled(vis.get("essVisibility"), scale=10, error=1000001, low=0, high=100000)
            stations.append(
                WeatherStation(
                    id=rec["_id"],
                    location=_location(rec.get("location")),
                    in_service=_bool(rec.get("inService")),
                    updated=_record_time(rec.get("recordTimestamp")),
                    air_temperature=None if probe_failed else air,
                    dewpoint=None if probe_failed else _temperature(temps.get("essDewpointTemp")),
                    humidity=None if probe_failed or humidity is None else round(humidity),
                    surface_temperature=_first(pavement, entry, "essSurfaceTemperature", _temperature),
                    surface_status=_first(pavement, entry, "essSurfaceStatus", _surface_status),
                    freeze_point=_first(pavement, entry, "essSurfaceFreezePoint", _temperature),
                    visibility_km=round(visibility / 1000, 2) if visibility is not None else None,
                    wind_speed=_wind(wind.get("essAvgWindSpeed")),
                    wind_gust=_wind(wind.get("essMaxWindGustSpeed")),
                    wind_direction=round(direction) if direction is not None and 0 <= direction <= 360 else None,
                    precipitation=PRECIPITATION_SITUATION.get(_text(humid.get("essPrecipSituation")) or ""),
                )
            )
        except (AttributeError, TypeError):
            continue
    return stations


def _sign_message(message: dict[str, Any]) -> str:
    if (_text(message.get("display")) or "").lower() == "blank":
        return ""
    phases = []
    for phase in ("phase1", "phase2"):
        block = message.get(phase) or {}
        lines = [_text(block.get(f"{phase}Line{n}")) for n in (1, 2, 3)]
        text = " / ".join(line for line in lines if line)
        if text:
            phases.append(text)
    return " | ".join(phases)


def parse_signs(raw: bytes) -> list[MessageSign]:
    """Parse a changeable message sign (cms) file."""
    signs = []
    for rec in _records(raw, "cms"):
        try:
            message = rec.get("message") or {}
            stamp = message.get("messageTimestamp") or {}
            signs.append(
                MessageSign(
                    id=rec["_id"],
                    location=_location(rec.get("location")),
                    in_service=_bool(rec.get("inService")),
                    updated=_local_time(stamp.get("messageDate"), stamp.get("messageTime")),
                    message=_sign_message(message),
                    record_time=_record_time(rec.get("recordTimestamp")),
                )
            )
        except (AttributeError, TypeError):
            continue
    return signs


def _closure_in_effect(closure: dict[str, Any]) -> bool:
    """Started (code 1097), not ended (1098) and not cancelled (1022)."""
    return (
        _bool((closure.get("code1097") or {}).get("isCode1097"))
        and not _bool((closure.get("code1098") or {}).get("isCode1098"))
        and not _bool((closure.get("code1022") or {}).get("isCode1022"))
    )


def parse_closures(raw: bytes) -> list[LaneClosure]:
    """Parse a lane closure (lcs) file, keeping only closures in effect (the file is mostly plans)."""
    closures = []
    for rec in _records(raw, "lcs"):
        try:
            closure = rec.get("closure") or {}
            if not _closure_in_effect(closure):
                continue
            times = closure.get("closureTimestamp") or {}
            loc = rec.get("location") or {}
            begin = _location(loc.get("begin"), "begin")
            end = _location(loc.get("end"), "end")
            flow = _text(loc.get("travelFlowDirection"))
            if flow:
                begin = dataclasses.replace(begin, direction=flow)
            closures.append(
                LaneClosure(
                    id=rec["_id"],
                    location=begin,
                    end_location=end,
                    closure_type=_text(closure.get("typeOfClosure")),
                    work_type=_text(closure.get("typeOfWork")),
                    facility=_text(closure.get("facility")),
                    lanes_closed=_text(closure.get("lanesClosed")),
                    total_lanes=_text(closure.get("totalExistingLanes")),
                    start=_epoch(times.get("closureStartEpoch"))
                    or _local_time(times.get("closureStartDate"), times.get("closureStartTime")),
                    end=_epoch(times.get("closureEndEpoch"))
                    or _local_time(times.get("closureEndDate"), times.get("closureEndTime")),
                    indefinite=_bool(times.get("isClosureEndIndefinite")),
                )
            )
        except (AttributeError, TypeError):
            continue
    return closures


def parse_cameras(raw: bytes) -> list[TrafficCamera]:
    """Parse a camera (cctv) file."""
    cameras = []
    for rec in _records(raw, "cctv"):
        try:
            image = rec.get("imageData") or {}
            static = image.get("static") or {}
            url = _text(static.get("currentImageURL"))
            if not url or not url.startswith("https://"):
                continue
            stream = _text(image.get("streamingVideoURL"))
            minutes = _number(static.get("currentImageUpdateFrequency"))
            cameras.append(
                TrafficCamera(
                    id=rec["_id"],
                    location=_location(rec.get("location")),
                    in_service=_bool(rec.get("inService")),
                    image_url=url,
                    stream_url=stream if stream and stream.startswith("https://") else None,
                    update_minutes=round(minutes) if minutes else None,
                )
            )
        except (AttributeError, TypeError):
            continue
    return cameras


PARSERS: dict[str, Callable[[bytes], list[Any]]] = {
    FEED_CHAIN_CONTROL: parse_chain_control,
    FEED_WEATHER: parse_weather,
    FEED_SIGNS: parse_signs,
    FEED_CLOSURES: parse_closures,
    FEED_CAMERAS: parse_cameras,
}


# ---------------------------------------------------------------------------
# Fetching


def feed_url(district: int, feed: str) -> str:
    """URL of one district's feed file."""
    return FEED_URL.format(district=district, feed=feed)


async def fetch_feed(session: aiohttp.ClientSession, district: int, feed: str, seconds: float) -> bytes:
    """Download one feed file."""
    url = feed_url(district, feed)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=seconds)) as resp:
            if resp.status != 200:
                msg = f"HTTP {resp.status} from {url}"
                raise FeedUnavailableError(msg)
            return await resp.read()
    except TimeoutError as err:
        msg = f"Timed out after {seconds:g} s fetching {url}"
        raise FeedUnavailableError(msg) from err
    except aiohttp.ClientError as err:
        msg = f"Could not fetch {url}: {err}"
        raise FeedUnavailableError(msg) from err


def natural_route_key(route: str) -> tuple[int, int, str]:
    """Sort routes as I-5, I-80, US-50, SR-4, SR-89 (interstates, then US, then state routes)."""
    prefix, _, number = route.partition("-")
    order = {"I": 0, "US": 1, "SR": 2}.get(prefix.upper(), 3)
    digits = re.match(r"\d+", number)
    return (order, int(digits.group()) if digits else 9999, route)
