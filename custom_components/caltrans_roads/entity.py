"""Shared entity code: areas (a route or "near home"), the base entity and dynamic adding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Location, distance_km
from .const import ATTRIBUTION, DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import CaltransFeedCoordinator


@dataclass(frozen=True, slots=True)
class Area:
    """What one device covers: one route, or everything within a radius of home."""

    key: str
    name: str
    district: int
    route: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float | None = None

    def matches(self, location: Location, *others: Location) -> bool:
        """Does a record at this location belong to the area."""
        if self.route is not None:
            return location.route == self.route
        return any(self._near(loc) for loc in (location, *others))

    def _near(self, location: Location) -> bool:
        if location.latitude is None or location.longitude is None or self.latitude is None:
            return False
        return distance_km(self.latitude, self.longitude, location.latitude, location.longitude) <= (
            self.radius_km or 0
        )


def location_attributes(location: Location) -> dict[str, Any]:
    """Common attributes describing where something is."""
    attrs: dict[str, Any] = {"location": location.name}
    if location.direction:
        attrs["direction"] = location.direction
    if location.elevation_ft is not None:
        attrs["elevation_ft"] = location.elevation_ft
    if location.latitude is not None:
        attrs["latitude"] = location.latitude
        attrs["longitude"] = location.longitude
    return attrs


def short_name(location: Location) -> str:
    """Location name without a leading "SR-18 : " route prefix."""
    name = location.name
    if location.route and name.startswith(location.route):
        rest = name[len(location.route) :].lstrip(" :-")
        if rest:
            return rest
    return name


class CaltransEntity(CoordinatorEntity["CaltransFeedCoordinator"]):
    """Base for all entities: one device per area."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: CaltransFeedCoordinator, area: Area, key: str) -> None:
        super().__init__(coordinator)
        assert coordinator.config_entry is not None  # noqa: S101
        entry_id = coordinator.config_entry.entry_id
        self.area = area
        self._attr_unique_id = f"{entry_id}_{area.key}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{area.key}")},
            name=area.name,
            manufacturer="Caltrans public data (unofficial)",
            model=f"District {area.district} road conditions",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://quickmap.dot.ca.gov/",
        )

    @property
    def available(self) -> bool:
        """Keep the last good value through short outages."""
        return self.coordinator.is_fresh()

    def matching(self) -> list[Any]:
        """Records of this feed that belong to the area."""
        out = []
        for record in self.coordinator.data or []:
            end = getattr(record, "end_location", None)
            others = (end,) if end is not None else ()
            if self.area.matches(record.location, *others):
                out.append(record)
        return out


def add_per_record_entities(
    entry: ConfigEntry,
    coordinator: CaltransFeedCoordinator,
    areas: list[Area],
    async_add_entities: AddConfigEntryEntitiesCallback,
    build: Callable[[CaltransFeedCoordinator, Area, Any], Iterable[Entity]],
) -> None:
    """
    Add entities for each record (station, sign, camera) in an area, now and as new ones appear.

    Records are only known once the feed has loaded, which may be after setup if Caltrans
    was down when Home Assistant started.
    """
    known: set[tuple[str, str]] = set()

    @callback
    def _add_new() -> None:
        new: list[Entity] = []
        for record in coordinator.data or []:
            for area in areas:
                key = (area.key, record.id)
                if key in known or not area.matches(record.location):
                    continue
                known.add(key)
                new.extend(build(coordinator, area, record))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


def record_by_id(coordinator: CaltransFeedCoordinator, record_id: str) -> Any | None:
    """Find a record in the latest data."""
    for record in coordinator.data or []:
        if record.id == record_id:
            return record
    return None
