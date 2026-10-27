"""Diagnostics: settings and the state of each feed (no home location is included)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .const import CONF_RADIUS

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import CaltransConfigEntry


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: CaltransConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = entry.runtime_data
    return {
        "entry": {"data": dict(entry.data), "options": dict(entry.options)},
        "areas": [
            {"key": area.key, "name": area.name, "route": area.route, CONF_RADIUS: area.radius_km}
            for area in data.areas
        ],
        "feeds": {
            feed: {
                "url": c.url,
                "update_interval_s": c.update_interval.total_seconds() if c.update_interval else None,
                "stale_after_s": c.stale_after.total_seconds(),
                "last_update_success": c.last_update_success,
                "last_success": c.last_success.isoformat() if c.last_success else None,
                "failing_since": c.failing_since.isoformat() if c.failing_since else None,
                "last_error": c.last_error,
                "fresh": c.is_fresh(),
                "bytes": c.raw_bytes,
                "records": len(c.data) if c.data is not None else None,
            }
            for feed, c in data.coordinators.items()
        },
    }
