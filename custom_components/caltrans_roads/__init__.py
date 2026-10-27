"""Caltrans Road Conditions: chain controls, road weather, signs, closures and cameras."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.core import CoreState
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import (
    BACKGROUND_FEEDS,
    CONF_DISTRICT,
    CONF_FEEDS,
    CONF_MODE,
    CONF_RADIUS,
    CONF_ROUTES,
    DEFAULT_FEEDS,
    DEFAULT_RADIUS_KM,
    DISTRICT_FEEDS,
    DOMAIN,
    FEED_CAMERAS,
    FEED_CHAIN_CONTROL,
    FEED_CLOSURES,
    FEED_SIGNS,
    FEED_WEATHER,
    FEEDS,
    MODE_NEAR,
    NEAR_HOME_KEY,
    REQUEST_TIMEOUTS,
    STARTUP_REQUEST_TIMEOUT,
)
from .coordinator import CaltransFeedCoordinator
from .entity import Area

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS = [Platform.BINARY_SENSOR, Platform.CAMERA, Platform.SENSOR]

# Start of the per-entity part of each unique id, by feed.
ENTITY_KEYS = {
    FEED_CHAIN_CONTROL: ("chain_control", "chains_required"),
    FEED_CLOSURES: ("lane_closures",),
    FEED_SIGNS: ("message_signs", "sign_"),
    FEED_WEATHER: ("rwis_",),
    FEED_CAMERAS: ("camera_",),
}


@dataclass
class CaltransData:
    """What one config entry holds while loaded."""

    district: int
    areas: list[Area]
    coordinators: dict[str, CaltransFeedCoordinator]


type CaltransConfigEntry = ConfigEntry[CaltransData]


def enabled_feeds(entry: ConfigEntry) -> list[str]:
    """Feeds turned on for the entry that the district actually publishes."""
    district = entry.data[CONF_DISTRICT]
    wanted = entry.options.get(CONF_FEEDS, DEFAULT_FEEDS)
    return [feed for feed in FEEDS if feed in wanted and district in DISTRICT_FEEDS[feed]]


def build_areas(hass: HomeAssistant, entry: ConfigEntry) -> list[Area]:
    """One area per followed route, or one around home."""
    district = entry.data[CONF_DISTRICT]
    if entry.data.get(CONF_MODE) == MODE_NEAR:
        return [
            Area(
                key=NEAR_HOME_KEY,
                name=f"Roads near home (District {district})",
                district=district,
                latitude=hass.config.latitude,
                longitude=hass.config.longitude,
                radius_km=float(entry.options.get(CONF_RADIUS, DEFAULT_RADIUS_KM)),
            )
        ]
    return [
        Area(key=route.lower().replace("-", "_"), name=route, district=district, route=route)
        for route in entry.options.get(CONF_ROUTES, [])
    ]


async def async_setup_entry(hass: HomeAssistant, entry: CaltransConfigEntry) -> bool:
    """Set up from a config entry."""
    district = entry.data[CONF_DISTRICT]
    coordinators = {feed: CaltransFeedCoordinator(hass, entry, district, feed) for feed in enabled_feeds(entry)}
    to_fetch = [c for c in coordinators.values() if not c.seed_from_cache()]
    # Wait for the small files only; lane closures (up to 15 MB) and cameras load in the
    # background unless nothing else is turned on.
    small = [c for c in to_fetch if c.feed not in BACKGROUND_FEEDS]
    has_small = any(c.feed not in BACKGROUND_FEEDS for c in coordinators.values())
    waited = small if has_small else to_fetch
    if hass.state is not CoreState.running:
        for c in waited:
            c.request_timeout = min(STARTUP_REQUEST_TIMEOUT, REQUEST_TIMEOUTS[c.feed])
    try:
        await asyncio.gather(*(c.async_refresh() for c in waited))
    finally:
        for c in waited:
            c.request_timeout = REQUEST_TIMEOUTS[c.feed]
    checked = (
        [c for c in coordinators.values() if c.feed not in BACKGROUND_FEEDS]
        if has_small
        else list(coordinators.values())
    )
    if checked and all(c.data is None for c in checked):
        errors = "; ".join(c.last_error or "unknown error" for c in checked)
        msg = f"No Caltrans feed for district {district} could be loaded: {errors}"
        raise ConfigEntryNotReady(msg)
    background = [c for c in to_fetch if c not in waited]

    areas = build_areas(hass, entry)
    entry.runtime_data = CaltransData(district=district, areas=areas, coordinators=coordinators)
    _remove_unused(hass, entry, areas, list(coordinators))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    for coordinator in background:
        entry.async_create_background_task(
            hass, coordinator.async_refresh(), f"{DOMAIN} first {coordinator.feed} refresh"
        )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


def _remove_unused(hass: HomeAssistant, entry: ConfigEntry, areas: list[Area], feeds: list[str]) -> None:
    """Drop devices of routes no longer followed and entities of data types turned off."""
    wanted = {f"{entry.entry_id}_{area.key}" for area in areas}
    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if not any(domain == DOMAIN and ident in wanted for domain, ident in device.identifiers):
            # A device belongs to one config entry, so drop it outright (detaching the entry
            # with remove_config_entry_id is deprecated and stops working in 2027.8).
            device_registry.async_remove_device(device.id)

    off = tuple(prefix for feed in FEEDS if feed not in feeds for prefix in ENTITY_KEYS[feed])
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        for area in areas:
            prefix = f"{entry.entry_id}_{area.key}_"
            if off and entity.unique_id.startswith(prefix) and entity.unique_id[len(prefix) :].startswith(off):
                entity_registry.async_remove(entity.entity_id)
                break


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: CaltransConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting a device whose route is no longer followed."""
    wanted = {f"{entry.entry_id}_{area.key}" for area in entry.runtime_data.areas}
    return not any(domain == DOMAIN and ident in wanted for domain, ident in device.identifiers)


async def _async_options_updated(hass: HomeAssistant, entry: CaltransConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CaltransConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        for coordinator in entry.runtime_data.coordinators.values():
            ir.async_delete_issue(hass, DOMAIN, coordinator.issue_id)
    return unloaded
