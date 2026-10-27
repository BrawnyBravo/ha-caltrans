"""Binary sensor: chains required on a route."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorEntity

from .api import CHAIN_CLOSED, CHAIN_NONE, CHAIN_TRAFFIC, CHAINS_REQUIRED_LEVELS, chain_rank
from .const import FEED_CHAIN_CONTROL
from .entity import CaltransEntity
from .sensor import worst_chain_control

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import CaltransConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CaltransConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    data = entry.runtime_data
    coordinator = data.coordinators.get(FEED_CHAIN_CONTROL)
    if coordinator is None:
        return
    async_add_entities(ChainsRequiredBinarySensor(coordinator, area, "chains_required") for area in data.areas)


# On for chain controls and for anything worse on a chain control point: a closed or held
# segment next to R-2 segments must not switch the alert off.
ON_LEVELS = CHAINS_REQUIRED_LEVELS | {CHAIN_TRAFFIC, CHAIN_CLOSED}


class ChainsRequiredBinarySensor(CaltransEntity, BinarySensorEntity):
    """On while any segment in the area is at R-1, R-2, R-3, held/escorted or closed."""

    _attr_translation_key = "chains_required"

    def _active(self) -> list[Any]:
        return [p for p in self.matching() if p.in_service and p.level in ON_LEVELS]

    @property
    def is_on(self) -> bool:
        return bool(self._active())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        level, point = worst_chain_control(self.matching())
        active = self._active()
        chains = [p for p in active if p.level in CHAINS_REQUIRED_LEVELS]
        chain_point = max(chains, key=lambda p: chain_rank(p.level), default=None)
        attrs: dict[str, Any] = {
            "level": level,
            "chains_level": chain_point.level if chain_point else CHAIN_NONE,
            "closed": any(p.level == CHAIN_CLOSED for p in active),
            "traffic_held_or_escorted": any(p.level == CHAIN_TRAFFIC for p in active),
        }
        if point is not None:
            attrs["status"] = point.status
            attrs["where"] = point.location.name
        return attrs
