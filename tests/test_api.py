"""Parsing of the Caltrans feeds, including the defects seen in the live files."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.caltrans_roads import api
from custom_components.caltrans_roads.api import (
    CHAIN_CLOSED,
    CHAIN_NONE,
    CHAIN_R1,
    CHAIN_R2,
    CHAIN_R3,
    CHAIN_TRAFFIC,
    CHAIN_TRUCKS,
    FeedDataError,
    FeedUnavailableError,
    chain_level,
    decode_payload,
    distance_km,
    feed_url,
    fetch_feed,
    natural_route_key,
    parse_cameras,
    parse_chain_control,
    parse_closures,
    parse_signs,
    parse_weather,
)

from .conftest import fixture_bytes


def test_feed_url_pattern():
    assert feed_url(3, "cc") == "https://cwwp2.dot.ca.gov/data/d3/cc/ccStatusD03.json"
    assert feed_url(10, "lcs") == "https://cwwp2.dot.ca.gov/data/d10/lcs/lcsStatusD10.json"


@pytest.mark.parametrize(
    ("status", "level"),
    [
        ("R-0", CHAIN_NONE),
        ("W", CHAIN_NONE),
        ("R-1", CHAIN_R1),
        ("r-1  modified", CHAIN_TRUCKS),
        ("R-2", CHAIN_R2),
        ("R-3", CHAIN_R3),
        ("ESC", CHAIN_TRAFFIC),
        ("HT", CHAIN_TRAFFIC),
        ("TTSD", CHAIN_TRUCKS),
        ("RC", CHAIN_CLOSED),
        ("Road Closed", CHAIN_CLOSED),
        ("-118.1307759", None),
        (None, None),
    ],
)
def test_chain_levels(status, level):
    assert chain_level(status) == level


def test_chain_control_fixture():
    points = {p.id: p for p in parse_chain_control(fixture_bytes("d03_cc.json"))}
    assert len(points) == 10
    p = points["3-NEV-80-13.5-E-192"]
    assert p.level == CHAIN_R2
    assert p.location.route == "I-80"
    assert p.location.elevation_ft is not None
    # 22:10 California daylight time is 05:10 UTC the next day.
    assert p.since == datetime(2026, 10, 27, 5, 10, tzinfo=UTC)
    assert points["3-NEV-80-13.5-W-200"].in_service is False


def test_garbage_status_is_kept_but_not_recognised():
    points = parse_chain_control(fixture_bytes("d07_cc.json"))
    bad = [p for p in points if p.status and p.status.startswith("-")]
    assert len(bad) == 2
    assert all(p.level is None for p in bad)
    # "Not Reported" coordinates become None.
    assert all(p.location.latitude is None for p in bad)


def test_windows_1252_file():
    points = parse_chain_control(fixture_bytes("d11_cc_cp1252.json"))
    assert any(p.location.nearby_place and "Hindú" in p.location.nearby_place for p in points)


def test_weather_units():
    stations = {s.id: s for s in parse_weather(fixture_bytes("d03_rwis.json"))}
    donner = stations["3"]
    assert donner.air_temperature == 1.8  # raw "18": tenths of a degree C
    assert donner.surface_temperature == -2.1
    assert donner.surface_status == "snow_warning"
    assert donner.wind_speed == 4.0  # raw "40": tenths of m/s
    assert donner.wind_gust == 6.7
    assert donner.visibility_km == 6.17  # raw "61678": tenths of a metre
    assert donner.updated == datetime(2026, 10, 27, 6, 0, 2, tzinfo=UTC)  # from recordEpoch
    # Bryte Bend pavement sensor reports the error value 1001.
    assert stations["11"].surface_temperature is None


def test_broken_json_is_repaired():
    raw = fixture_bytes("d10_rwis_broken.json")
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    stations = parse_weather(raw)
    assert len(stations) == 1
    assert stations[0].air_temperature == 14.5


@pytest.mark.parametrize(
    "raw",
    [b"<html>Internal Server Error</html>", b'{"data": "nope"}', b"[]", b'{"data": [1, 2', b"{} {}"],
)
def test_unusable_files(raw):
    with pytest.raises(FeedDataError):
        decode_payload(raw)


def test_bad_records_are_skipped():
    raw = json.dumps(
        {
            "data": [
                "not a dict",
                {"cc": {"location": {"route": "I-80"}}},
                {"cc": "not a dict"},
                {"cc": {"index": "x", "location": "oops"}},
                {"rwis": {"index": "y", "rwisData": "oops"}},
                {"cms": {"index": "z", "message": "oops"}},
                {"lcs": {"index": "w", "closure": "oops"}},
                {"cctv": {"index": "v", "imageData": "oops"}},
            ]
        }
    ).encode()
    assert parse_chain_control(raw) == []
    assert parse_weather(raw) == []
    assert parse_signs(raw) == []
    assert parse_closures(raw) == []
    assert parse_cameras(raw) == []


def test_weather_error_values():
    record = {
        "rwis": {
            "index": "9",
            "inService": "true",
            "recordTimestamp": {"recordDate": "2026-10-26", "recordTime": "23:00:00"},
            "location": {"route": "I-80", "locationName": "Test", "latitude": "99", "longitude": "1"},
            "rwisData": {
                "windData": {"essAvgWindSpeed": "65535", "essMaxWindGustSpeed": "1210", "essAvgWindDirection": "361"},
                "temperatureData": {
                    "essTemperatureSensorTable": [
                        {"essTemperatureSensorEntry": {"essAirTemperature": "1001"}},
                        {"essTemperatureSensorEntry": {"essAirTemperature": "-55"}},
                    ],
                    "essDewpointTemp": "Not Reported",
                },
                "humidityPrecipData": {"essRelativeHumidity": "101", "essPrecipSituation": "8"},
                "visibilityData": {"essVisibility": "1000001"},
                "pavementSensorData": {
                    "essPavementSensorTable": [
                        {"essPavementSensorEntry": {"essSurfaceStatus": "2", "essSurfaceTemperature": "nan"}}
                    ]
                },
            },
        }
    }
    (station,) = parse_weather(json.dumps({"data": [record]}).encode())
    assert station.wind_speed is None
    assert station.wind_gust is None  # 121 m/s is a sensor fault
    assert station.wind_direction is None
    assert station.air_temperature == -5.5  # first sensor errored, second used
    assert station.dewpoint is None
    assert station.humidity is None
    assert station.visibility_km is None
    assert station.surface_status is None  # "error" is not a road condition
    assert station.surface_temperature is None
    assert station.precipitation == "snow moderate"
    assert station.location.latitude is None  # 99 is not a latitude
    # No epoch: California local time.
    assert station.updated == datetime(2026, 10, 27, 6, 0, tzinfo=UTC)


def test_signs():
    signs = {s.id: s for s in parse_signs(fixture_bytes("d03_cms.json"))}
    assert signs["1240"].message == "BE / WORK ZONE / ALERT"
    assert signs["1272"].message == "N-5 FWY TO CLOSE / 2 MILES NORTH OF / SAC AIRPORT | NIGHTLY / NOV 1 - NOV 6"
    assert signs["1238"].message == ""
    assert signs["1246"].in_service is False
    assert signs["1240"].record_time is not None


def test_closures_keep_only_those_in_effect():
    closures = parse_closures(fixture_bytes("d03_lcs.json"))
    assert len(closures) == 5  # cancelled, finished and not-yet-started ones dropped
    full = [c for c in closures if c.closure_type == "Full"]
    assert len(full) == 1
    assert full[0].location.direction == "East"  # travel flow direction
    assert full[0].start == datetime(2026, 10, 27, 4, 1, tzinfo=UTC)


def test_closure_without_epochs_uses_local_time():
    record = {
        "lcs": {
            "index": "a",
            "location": {"begin": {"beginRoute": "I-80", "beginLocationName": "Here"}, "end": {}},
            "closure": {
                "closureTimestamp": {
                    "closureStartDate": "2026-10-26",
                    "closureStartTime": "21:00:00",
                    "closureEndDate": "bad",
                    "closureEndTime": "bad",
                    "closureStartEpoch": "",
                    "closureEndEpoch": "99999999999999999999",
                    "isClosureEndIndefinite": "true",
                },
                "code1097": {"isCode1097": "true"},
            },
        }
    }
    (closure,) = parse_closures(json.dumps({"data": [record]}).encode())
    assert closure.start == datetime(2026, 10, 27, 4, 0, tzinfo=UTC)
    assert closure.end is None
    assert closure.indefinite is True
    assert closure.end_location.name == "Unnamed location"


def test_cameras():
    cameras = {c.id: c for c in parse_cameras(fixture_bytes("d03_cctv.json"))}
    assert len(cameras) == 4
    cam = cameras["51"]
    assert cam.image_url.startswith("https://cwwp2.dot.ca.gov/data/d3/cctv/image/")
    assert cam.stream_url is None or cam.stream_url.startswith("https://")
    assert cameras["53"].in_service is False


def test_camera_without_https_image_is_skipped():
    raw = json.dumps(
        {"data": [{"cctv": {"index": "1", "imageData": {"static": {"currentImageURL": "http://x/y.jpg"}}}}]}
    ).encode()
    assert parse_cameras(raw) == []


def test_route_sort_and_distance():
    routes = ["SR-89", "US-50", "I-80", "SR-4", "I-5", "Weird"]
    assert sorted(routes, key=natural_route_key) == ["I-5", "I-80", "US-50", "SR-4", "SR-89", "Weird"]
    assert distance_km(39.0, -120.0, 39.0, -120.0) == 0
    assert 110 < distance_km(39.0, -120.0, 40.0, -120.0) < 112


async def test_fetch_feed(aioclient_mock: AiohttpClientMocker, hass):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    aioclient_mock.get(feed_url(3, "cc"), content=b"{}")
    aioclient_mock.get(feed_url(4, "cc"), status=500)
    aioclient_mock.get(feed_url(5, "cc"), exc=TimeoutError())
    aioclient_mock.get(feed_url(6, "cc"), exc=aiohttp.ClientError("boom"))
    assert await fetch_feed(session, 3, "cc", 5) == b"{}"
    with pytest.raises(FeedUnavailableError, match="HTTP 500"):
        await fetch_feed(session, 4, "cc", 5)
    with pytest.raises(FeedUnavailableError, match="Timed out"):
        await fetch_feed(session, 5, "cc", 5)
    with pytest.raises(FeedUnavailableError, match="boom"):
        await fetch_feed(session, 6, "cc", 5)


def test_helpers_edge_cases():
    assert api._epoch("-5") is None
    assert api._local_time("2026-10-26", None) is None
    assert api._number("inf") is None


def _station(air: str, humidity: str, pavement: list[dict]) -> bytes:
    record = {
        "rwis": {
            "index": "1",
            "inService": "true",
            "location": {"route": "SR-99", "locationName": "Test"},
            "rwisData": {
                "temperatureData": {
                    "essTemperatureSensorTable": [{"essTemperatureSensorEntry": {"essAirTemperature": air}}]
                },
                "humidityPrecipData": {"essRelativeHumidity": humidity},
                "pavementSensorData": {"essPavementSensorTable": [{"essPavementSensorEntry": p} for p in pavement]},
            },
        }
    }
    return json.dumps({"data": [record]}).encode()


def test_faulted_station_air_temperature_dropped():
    # Live district 10 "SR-99 : Westside", 26 Oct 2026: -45.5 C, 0% humidity, dewpoint -97.1 C.
    (station,) = parse_weather(_station("-455", "0", []))
    assert station.air_temperature is None
    assert station.humidity is None
    # 0% humidity alone marks the probe as failed even with a believable temperature.
    (station,) = parse_weather(_station("123", "0", []))
    assert station.air_temperature is None
    # Below California's plausible range even with a working humidity probe.
    (station,) = parse_weather(_station("-455", "40", []))
    assert station.air_temperature is None
    (station,) = parse_weather(_station("123", "45", []))
    assert station.air_temperature == 12.3
    assert station.humidity == 45


def test_pavement_sensor_error_skips_that_sensor():
    faulty = {"essPavementSensorError": "4", "essSurfaceTemperature": "0", "essSurfaceStatus": "3"}
    good = {"essPavementSensorError": "2", "essSurfaceTemperature": "-15", "essSurfaceStatus": "7"}
    unknown = {"essPavementSensorError": "Not Reported", "essSurfaceTemperature": "20", "essSurfaceStatus": "5"}
    (station,) = parse_weather(_station("50", "60", [faulty, good]))
    assert station.surface_temperature == -1.5
    assert station.surface_status == "ice_warning"
    (station,) = parse_weather(_station("50", "60", [faulty]))
    assert station.surface_temperature is None
    assert station.surface_status is None
    # No error field reported: the reading is used.
    (station,) = parse_weather(_station("50", "60", [unknown]))
    assert station.surface_temperature == 2.0


def test_duplicate_indexes_get_distinct_stable_ids():
    # Live district 3 signs, 26 Oct 2026: index "1234" on two different signs.
    def sign(index, name):
        return {
            "cms": {
                "index": index,
                "inService": "true",
                "location": {"route": "US-50", "locationName": name},
                "message": {},
            }
        }

    raw = json.dumps(
        {"data": [sign("1234", "044 - Midway Rd"), sign("1234", "150 - Howe_Ave_JEO_SAC50_EB"), sign("9", "x")]}
    ).encode()
    ids = [s.id for s in parse_signs(raw)]
    assert ids == ["1234_044_midway_rd", "1234_150_howe_ave_jeo_sac50_eb", "9"]
    assert [s.id for s in parse_signs(raw)] == ids  # same every time
