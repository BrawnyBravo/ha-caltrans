"""Config and options flows."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.caltrans_roads.const import DOMAIN, FEEDS

from .conftest import fixture_bytes, make_entry, mock_feeds, setup_entry


async def _start(hass: HomeAssistant, district: str = "3", mode: str = "routes"):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"district": district, "mode": mode})


def _options(schema, key: str) -> list:
    for marker, selector in schema.schema.items():
        if marker == key:
            return [o["value"] if isinstance(o, dict) else o for o in selector.config["options"]]
    raise KeyError(key)


async def test_routes_flow(hass: HomeAssistant, d3_feeds: AiohttpClientMocker):
    result = await _start(hass)
    assert result["step_id"] == "routes"
    # Routes from the chain control, sign and weather fixtures, interstates first.
    assert _options(result["data_schema"], "routes") == ["I-80", "US-50", "SR-89"]
    # District 3 has every feed.
    assert _options(result["data_schema"], "data_types") == list(FEEDS)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"routes": [], "data_types": ["cc"]})
    assert result["errors"] == {"routes": "no_routes"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"routes": ["I-80"], "data_types": []})
    assert result["errors"] == {"data_types": "no_data_types"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"routes": ["I-80", "US-50"], "data_types": ["cc", "rwis"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Caltrans District 3"
    assert result["data"] == {"district": 3, "mode": "routes"}
    assert result["options"] == {"routes": ["I-80", "US-50"], "data_types": ["cc", "rwis"]}
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.i_80_chains_required").state == "on"


async def test_district_without_some_feeds_offers_only_what_exists(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
):
    mock_feeds(aioclient_mock, 4, {"cms": fixture_bytes("d04_cms.json")})
    result = await _start(hass, "4")
    assert _options(result["data_schema"], "data_types") == ["cms", "lcs", "cctv"]
    assert _options(result["data_schema"], "routes") == ["US-101"]


async def test_cannot_connect(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    mock_feeds(aioclient_mock, 3, dict.fromkeys(FEEDS, 500))
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_route_list_survives_one_bad_feed(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    mock_feeds(aioclient_mock, 3, {"cc": b"garbage", "cms": 500, "rwis": fixture_bytes("d03_rwis.json")})
    result = await _start(hass)
    assert result["step_id"] == "routes"
    assert _options(result["data_schema"], "routes") == ["I-80", "US-50"]


async def test_near_flow(hass: HomeAssistant):
    result = await _start(hass, "9", "near")
    assert result["step_id"] == "near"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"radius": 30, "data_types": []})
    assert result["errors"] == {"data_types": "no_data_types"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"radius": 30, "data_types": ["cc"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Caltrans District 9 near home"
    assert result["data"] == {"district": 9, "mode": "near"}
    assert result["options"] == {"radius": 30, "data_types": ["cc"]}


async def test_already_configured(hass: HomeAssistant):
    make_entry().add_to_hass(hass)
    result = await _start(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_routes(hass: HomeAssistant, d3_feeds: AiohttpClientMocker):
    entry = await setup_entry(hass, make_entry())
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    assert "SR-89" in _options(result["data_schema"], "routes")

    result = await hass.config_entries.options.async_configure(result["flow_id"], {"routes": [], "data_types": ["cc"]})
    assert result["errors"] == {"routes": "no_routes"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"routes": ["I-80"], "data_types": []}
    )
    assert result["errors"] == {"data_types": "no_data_types"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"routes": ["SR-89"], "data_types": ["cc", "cctv"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {"routes": ["SR-89"], "data_types": ["cc", "cctv"]}
    assert hass.states.get("sensor.sr_89_chain_control") is not None


async def test_options_when_caltrans_is_down_keeps_current_routes(hass: HomeAssistant, d3_feeds: AiohttpClientMocker):
    entry = await setup_entry(hass, make_entry(routes=["I-80", "SR-999"]))
    d3_feeds.clear_requests()
    mock_feeds(d3_feeds, 3, dict.fromkeys(FEEDS, 500))
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert _options(result["data_schema"], "routes") == ["I-80", "SR-999"]


async def test_options_near(hass: HomeAssistant, d3_feeds: AiohttpClientMocker):
    entry = await setup_entry(hass, make_entry(mode="near", radius=10))
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"radius": 50, "data_types": ["cc"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {"radius": 50, "data_types": ["cc"]}
