"""Diagnostics."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.caltrans_roads.diagnostics import async_get_config_entry_diagnostics

from .conftest import HOME, make_entry, setup_entry


async def test_diagnostics(hass: HomeAssistant, d3_feeds: AiohttpClientMocker):
    hass.config.latitude, hass.config.longitude = HOME
    entry = await setup_entry(hass, make_entry(mode="near", radius=20))
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"] == {"district": 3, "mode": "near"}
    assert diag["areas"][0]["radius"] == 20
    cc = diag["feeds"]["cc"]
    assert cc["url"] == "https://cwwp2.dot.ca.gov/data/d3/cc/ccStatusD03.json"
    assert cc["fresh"] is True
    assert cc["records"] == 10
    assert cc["update_interval_s"] == 180
    # The home location is not in diagnostics.
    text = str(diag)
    assert str(HOME[0]) not in text
    assert str(HOME[1]) not in text
