"""What each platform shows, from the recorded fixtures."""

from __future__ import annotations

import json

import pytest
from homeassistant.components.camera import async_get_image
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.caltrans_roads.const import DOMAIN, MODE_NEAR

from .conftest import HOME, fixture_bytes, make_entry, mock_feeds, restamped, settle, setup_entry

DONNER = "sensor.i_80_hwy_80_at_donner_summit"


async def test_chain_control(hass: HomeAssistant, d3_feeds: AiohttpClientMocker):
    await setup_entry(hass, make_entry())
    state = hass.states.get("sensor.i_80_chain_control")
    assert state.state == "r2"
    assert state.attributes["status"] == "R-2"
    assert state.attributes["segment_count"] == 4  # the out-of-service R-3 is left out
    assert state.attributes["segments_with_controls"] == 2
    assert [s["status"] for s in state.attributes["segments"]] == ["R-2", "R-1"]
    assert state.attributes["last_change"] == "2026-10-27T05:10:00+00:00"
    assert "unofficial" in state.attributes["attribution"].lower()
    assert hass.states.get("binary_sensor.i_80_chains_required").state == "on"
    assert hass.states.get("binary_sensor.i_80_chains_required").attributes["status"] == "R-2"

    us50 = hass.states.get("sensor.us_50_chain_control")
    assert us50.state == "closed"
    # A closed segment keeps the alert on, and says so.
    us50_alert = hass.states.get("binary_sensor.us_50_chains_required")
    assert us50_alert.state == "on"
    assert us50_alert.attributes["closed"] is True
    assert us50_alert.attributes["chains_level"] == "none"


async def test_unrecognised_chain_status(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    mock_feeds(aioclient_mock, 7, {"cc": fixture_bytes("d07_cc.json"), "cms": 500, "lcs": 500, "rwis": 500})
    await setup_entry(hass, make_entry(district=7, routes=["SR-2"], feeds=["cc"]))
    state = hass.states.get("sensor.sr_2_chain_control")
    assert state.state == "none"
    assert state.attributes["unrecognized_statuses"] == ["-117.9363729", "-118.1307759"]
    assert hass.states.get("binary_sensor.sr_2_chains_required").attributes == {
        "level": "none",
        "chains_level": "none",
        "closed": False,
        "traffic_held_or_escorted": False,
        "status": "R-0",
        "where": "La Canada (Gate)",
        "friendly_name": "SR-2 Chains required",
        "attribution": state.attributes["attribution"],
    }


async def test_weather_station_sensors(hass: HomeAssistant, d3_feeds: AiohttpClientMocker):
    await setup_entry(hass, make_entry())
    assert hass.states.get(f"{DONNER}_air_temperature").state == "1.8"
    assert hass.states.get(f"{DONNER}_air_temperature").attributes["unit_of_measurement"] == "°C"
    assert hass.states.get(f"{DONNER}_pavement_temperature").state == "-2.1"
    pavement = hass.states.get(f"{DONNER}_pavement")
    assert pavement.state == "snow_warning"
    assert pavement.attributes["elevation_ft"] == 7221
    # 4.0 and 6.7 m/s, shown in the metric test setup's km/h.
    assert float(hass.states.get(f"{DONNER}_wind_speed").state) == pytest.approx(14.4)
    assert float(hass.states.get(f"{DONNER}_wind_gust").state) == pytest.approx(24.12)
    assert float(hass.states.get(f"{DONNER}_visibility").state) == pytest.approx(6.17)
    # Off by default.
    assert hass.states.get(f"{DONNER}_wind_direction") is None
    # Station on US-50 lands on the US-50 device.
    assert hass.states.get("sensor.us_50_hwy_50_at_echo_summit_air_temperature") is not None
    # Error value from the pavement sensor: unknown, not a made-up number.
    assert hass.states.get("sensor.i_80_hwy_80_at_bryte_bend_pavement_temperature").state == "unknown"
    # Floriston last reported a day ago.
    assert hass.states.get("sensor.i_80_hwy_80_at_floriston_air_temperature").state == STATE_UNAVAILABLE


async def test_message_signs(hass: HomeAssistant, d3_feeds: AiohttpClientMocker, entity_registry: er.EntityRegistry):
    entry = await setup_entry(hass, make_entry())
    summary = hass.states.get("sensor.i_80_message_signs")
    assert summary.state == "2"  # blank and out-of-service signs not counted
    assert [m["message"] for m in summary.attributes["messages"]] == [
        "BE / WORK ZONE / ALERT",
        "N-5 FWY TO CLOSE / 2 MILES NORTH OF / SAC AIRPORT | NIGHTLY / NOV 1 - NOV 6",
    ]
    # Per-sign sensors exist but are off by default.
    sign = entity_registry.async_get("sensor.i_80_sign_003_gilardi_rd")
    assert sign is not None
    assert sign.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    for entity_id in (
        "sensor.i_80_sign_003_gilardi_rd",
        "sensor.i_80_sign_004_truckee",
        "sensor.i_80_sign_012_blue_canyon",
    ):
        entity_registry.async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    assert hass.states.get("sensor.i_80_sign_003_gilardi_rd").state == "BE / WORK ZONE / ALERT"
    assert hass.states.get("sensor.i_80_sign_003_gilardi_rd").attributes["direction"] == "East"
    assert hass.states.get("sensor.i_80_sign_004_truckee").state == ""
    assert hass.states.get("sensor.i_80_sign_012_blue_canyon").state == STATE_UNAVAILABLE


async def test_lane_closures(hass: HomeAssistant, d3_feeds: AiohttpClientMocker, frozen):
    await setup_entry(hass, make_entry())
    state = hass.states.get("sensor.i_80_lane_closures")
    assert state.state == "4"
    assert state.attributes["full_closures"] == 1
    assert state.attributes["shown"] == 4
    first = state.attributes["closures"][0]
    assert set(first) >= {"from", "to", "type", "work", "lanes_closed", "started", "planned_end"}
    assert hass.states.get("sensor.us_50_lane_closures").state == "1"


async def test_closure_never_closed_out_stops_counting(hass: HomeAssistant, d3_feeds: AiohttpClientMocker, frozen):
    """Planned end 04:59 PDT (11:59 UTC); still counted until two hours later."""
    entry = await setup_entry(hass, make_entry(feeds=["lcs"]))
    frozen.move_to("2026-10-27T13:58:00+00:00")
    coordinator = entry.runtime_data.coordinators["lcs"]
    coordinator.last_success = dt_util.utcnow()  # keep the data fresh for this check
    coordinator.async_update_listeners()
    assert hass.states.get("sensor.i_80_lane_closures").state == "4"
    frozen.move_to("2026-10-27T14:00:00+00:00")
    coordinator.last_success = dt_util.utcnow()
    coordinator.async_update_listeners()
    assert hass.states.get("sensor.i_80_lane_closures").state == "0"


async def test_devices_one_per_route(
    hass: HomeAssistant, d3_feeds: AiohttpClientMocker, device_registry: dr.DeviceRegistry
):
    entry = await setup_entry(hass, make_entry())
    devices = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    assert sorted(d.name for d in devices) == ["I-80", "US-50"]
    assert all(d.entry_type is dr.DeviceEntryType.SERVICE for d in devices)
    assert all((DOMAIN, f"{entry.entry_id}_i_80") in d.identifiers or d.name == "US-50" for d in devices)


async def test_near_home(hass: HomeAssistant, d3_feeds: AiohttpClientMocker):
    hass.config.latitude, hass.config.longitude = HOME
    await setup_entry(hass, make_entry(mode=MODE_NEAR, radius=20))
    prefix = "roads_near_home_district_3"
    assert hass.states.get(f"sensor.{prefix}_chain_control").state == "r2"
    assert hass.states.get(f"binary_sensor.{prefix}_chains_required").state == "on"
    assert hass.states.get(f"sensor.{prefix}_hwy_80_at_donner_summit_air_temperature").state == "1.8"
    # Floriston is about 25 km away, outside the 20 km radius; Sacramento is far away.
    assert hass.states.get(f"sensor.{prefix}_hwy_80_at_floriston_air_temperature") is None
    assert hass.states.get(f"sensor.{prefix}_hwy_80_at_bryte_bend_air_temperature") is None
    assert hass.states.get(f"sensor.{prefix}_lane_closures").state == "0"
    assert hass.states.get(f"sensor.{prefix}_message_signs").state == "0"


async def test_cameras(hass: HomeAssistant, d3_feeds: AiohttpClientMocker):
    image_url = "https://cwwp2.dot.ca.gov/data/d3/cctv/image/hwy80atwestcapitol/hwy80atwestcapitol.jpg"
    bad_url = "https://cwwp2.dot.ca.gov/data/d3/cctv/image/hwy80atbrytebend/hwy80atbrytebend.jpg"
    d3_feeds.get(image_url, content=b"\xff\xd8jpeg")
    d3_feeds.get(bad_url, status=404)
    await setup_entry(hass, make_entry(feeds=["cctv"]))

    camera = "camera.i_80_hwy_80_at_west_capitol"
    state = hass.states.get(camera)
    assert state.state == "idle"
    assert state.attributes["image_url"] == image_url
    assert hass.states.get("camera.i_80_hwy_80_at_truxel").state == STATE_UNAVAILABLE
    assert hass.states.get("camera.us_50_hwy_50_at_harbor_blvd_onr_yol50_wb_1") is not None

    image = await async_get_image(hass, camera)
    assert image.content == b"\xff\xd8jpeg"
    calls = d3_feeds.call_count
    image = await async_get_image(hass, camera)  # cached
    assert d3_feeds.call_count == calls

    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, "camera.i_80_hwy_80_at_bryte_bend")


async def test_camera_image_errors(hass: HomeAssistant, d3_feeds: AiohttpClientMocker, frozen):
    image_url = "https://cwwp2.dot.ca.gov/data/d3/cctv/image/hwy80atwestcapitol/hwy80atwestcapitol.jpg"
    d3_feeds.get(image_url, exc=TimeoutError())
    entry = await setup_entry(hass, make_entry(feeds=["cctv"]))
    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, "camera.i_80_hwy_80_at_west_capitol")

    # A camera that vanished from the feed returns no image.
    coordinator = entry.runtime_data.coordinators["cctv"]
    coordinator.async_set_updated_data([])
    await settle(hass)
    assert hass.states.get("camera.i_80_hwy_80_at_west_capitol").state == STATE_UNAVAILABLE


def _cc_with(statuses: dict[str, str]) -> bytes:
    """The district 3 chain control fixture with some segments' status replaced (index -> code)."""
    payload = json.loads(restamped("d03_cc.json"))
    for item in payload["data"]:
        record = item["cc"]
        if record["index"] in statuses:
            record["statusData"]["status"] = statuses[record["index"]]
    return json.dumps(payload).encode()


@pytest.mark.parametrize(
    ("statuses", "on", "chains_level", "closed"),
    [
        # Summit closed while the approach is at R-2: alert stays on and reports both.
        ({"3-NEV-80-13.5-E-192": "RC", "3-NEV-80-13.5-E-192a": "R-2"}, "on", "r2", True),
        # Traffic held at one point, R-1 at another.
        ({"3-NEV-80-13.5-E-192": "HT", "3-NEV-80-13.5-E-192a": "R-1"}, "on", "r1", False),
        # Whole followed part closed, no chain level: on, flagged closed.
        ({"3-NEV-80-13.5-E-192": "RC", "3-NEV-80-13.5-E-192a": "RC"}, "on", "none", True),
        # Trucks-only restrictions do not turn the alert on.
        ({"3-NEV-80-13.5-E-192": "R-1 MODIFIED", "3-NEV-80-13.5-E-192a": "R-0"}, "off", "none", False),
    ],
)
async def test_chains_required_with_mixed_segments(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, statuses, on, chains_level, closed
):
    mock_feeds(aioclient_mock, 3, {"cc": _cc_with(statuses)})
    await setup_entry(hass, make_entry(routes=["I-80"], feeds=["cc"]))
    state = hass.states.get("binary_sensor.i_80_chains_required")
    assert state.state == on
    assert state.attributes["chains_level"] == chains_level
    assert state.attributes["closed"] is closed


async def test_faulty_pavement_sensor_is_unknown(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    """Live district 3: pavement sensors with error 4 (cut cable) report 0 C and dry."""
    payload = json.loads(restamped("d03_rwis.json"))
    for item in payload["data"]:
        for entry in item["rwis"]["rwisData"]["pavementSensorData"]["essPavementSensorTable"]:
            entry["essPavementSensorEntry"]["essPavementSensorError"] = "4"
            entry["essPavementSensorEntry"]["essSurfaceTemperature"] = "0"
    mock_feeds(aioclient_mock, 3, {"rwis": json.dumps(payload).encode()})
    await setup_entry(hass, make_entry(routes=["I-80"], feeds=["rwis"]))
    assert hass.states.get(f"{DONNER}_pavement_temperature").state == "unknown"
    assert hass.states.get(f"{DONNER}_pavement").state == "unknown"
    assert hass.states.get(f"{DONNER}_air_temperature").state == "1.8"
