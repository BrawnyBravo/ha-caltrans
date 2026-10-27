"""
Shared fixtures.

The JSON files in tests/fixtures are trimmed copies of the live Caltrans feeds fetched on
26 Oct 2026 (about 11 PM California time; 27 Oct in UTC). Edits, because October has no chain controls:

* d03_cc.json: I-80 segments set to R-2 and R-1 (and one out-of-service R-3), US-50 set to
  RC (closed) and R-1 MODIFIED.
* d03_rwis.json: Donner Summit pavement set to snow warning at -2.1 C with a working sensor
  (live it reported error 4, cut cable); Floriston's record
  dated a day earlier so it counts as stale.
* d03_cctv.json: the third I-80 camera marked out of service.

d07_cc.json (longitudes in the status field), d10_rwis_broken.json (missing "},{" between
two table entries) and d11_cc_cp1252.json (Windows-1252 bytes) reproduce real defects.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.caltrans_roads.api import feed_url
from custom_components.caltrans_roads.const import (
    CONF_DISTRICT,
    CONF_FEEDS,
    CONF_MODE,
    CONF_RADIUS,
    CONF_ROUTES,
    DEFAULT_FEEDS,
    DOMAIN,
    FEEDS,
    MODE_NEAR,
    MODE_ROUTES,
)

pytest_plugins = "pytest_homeassistant_custom_component"

FIXTURES = Path(__file__).parent / "fixtures"

# Just after the fixtures were recorded: 06:05 UTC, 11:05 PM in California.
FROZEN_TIME = "2026-10-27T06:05:00+00:00"

# A public landmark: the published position of Caltrans' Donner Summit weather station on I-80.
HOME = (39.33906, -120.34782)


def fixture_bytes(name: str) -> bytes:
    """Raw bytes of a fixture file."""
    return (FIXTURES / name).read_bytes()


def restamped(name: str) -> bytes:
    """A fixture with every record stamped as written now, as a live file would be."""
    payload = json.loads(fixture_bytes(name))
    now = dt_util.utcnow()
    local = dt_util.as_local(now).astimezone(dt_util.get_time_zone("America/Los_Angeles"))
    for item in payload["data"]:
        for record in item.values():
            stamp = record.get("recordTimestamp")
            if stamp is not None:
                stamp["recordDate"] = local.strftime("%Y-%m-%d")
                stamp["recordTime"] = local.strftime("%H:%M:%S")
                if "recordEpoch" in stamp:
                    stamp["recordEpoch"] = str(int(now.timestamp()))
    return json.dumps(payload).encode()


def mock_feeds(aioclient_mock: AiohttpClientMocker, district: int, responses: dict[str, Any]) -> None:
    """Register responses: bytes for content, an int for an HTTP error, or an exception."""
    for feed, response in responses.items():
        url = feed_url(district, feed)
        if isinstance(response, bytes):
            aioclient_mock.get(url, content=response)
        elif isinstance(response, int):
            aioclient_mock.get(url, status=response)
        else:
            aioclient_mock.get(url, exc=response)


def d3_responses() -> dict[str, Any]:
    """All district 3 feeds from fixtures."""
    return {feed: fixture_bytes(f"d03_{feed}.json") for feed in FEEDS}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations."""
    return


@pytest.fixture(autouse=True)
def frozen(freezer):
    """Run every test at the moment the fixtures were recorded."""
    freezer.move_to(FROZEN_TIME)
    return freezer


@pytest.fixture
def d3_feeds(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """District 3 feeds answering with fixtures."""
    mock_feeds(aioclient_mock, 3, d3_responses())
    return aioclient_mock


def make_entry(
    *,
    district: int = 3,
    mode: str = MODE_ROUTES,
    routes: list[str] | None = None,
    feeds: list[str] | None = None,
    radius: float = 20,
) -> MockConfigEntry:
    """A config entry like the flow creates."""
    options: dict[str, Any] = {CONF_FEEDS: list(DEFAULT_FEEDS if feeds is None else feeds)}
    if mode == MODE_NEAR:
        options[CONF_RADIUS] = radius
    else:
        options[CONF_ROUTES] = ["I-80", "US-50"] if routes is None else routes
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"Caltrans District {district}",
        unique_id=f"{district}_{mode}",
        data={CONF_DISTRICT: district, CONF_MODE: mode},
        options=options,
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> MockConfigEntry:
    """Add and set up an entry."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
