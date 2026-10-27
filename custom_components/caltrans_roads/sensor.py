"""Sensors: chain control, lane closures, message signs and road weather stations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import DEGREE, UnitOfLength, UnitOfSpeed, UnitOfTemperature
from homeassistant.util import dt as dt_util

from .api import (
    CHAIN_LEVELS,
    CHAIN_NONE,
    SURFACE_STATUS,
    ChainControlPoint,
    LaneClosure,
    MessageSign,
    WeatherStation,
    chain_rank,
)
from .const import (
    CLOSURE_GRACE,
    FEED_CHAIN_CONTROL,
    FEED_CLOSURES,
    FEED_SIGNS,
    FEED_WEATHER,
    MAX_ATTR_ITEMS,
    STATION_STALE_AFTER,
)
from .entity import CaltransEntity, add_per_record_entities, location_attributes, record_by_id, short_name

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import CaltransConfigEntry
    from .coordinator import CaltransFeedCoordinator
    from .entity import Area

PARALLEL_UPDATES = 0

MAX_STATE_LENGTH = 255


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def worst_chain_control(points: list[ChainControlPoint]) -> tuple[str, ChainControlPoint | None]:
    """Worst recognised level among in-service points, and the point that has it."""
    worst, worst_point = CHAIN_NONE, None
    for point in points:
        if not point.in_service or point.level is None:
            continue
        if worst_point is None or chain_rank(point.level) > chain_rank(worst):
            worst, worst_point = point.level, point
    return worst, worst_point


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CaltransConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    data = entry.runtime_data
    coordinators = data.coordinators
    entities: list[SensorEntity] = []
    for area in data.areas:
        if FEED_CHAIN_CONTROL in coordinators:
            entities.append(ChainControlSensor(coordinators[FEED_CHAIN_CONTROL], area, "chain_control"))
        if FEED_CLOSURES in coordinators:
            entities.append(LaneClosureSensor(coordinators[FEED_CLOSURES], area, "lane_closures"))
        if FEED_SIGNS in coordinators:
            entities.append(SignSummarySensor(coordinators[FEED_SIGNS], area, "message_signs"))
    async_add_entities(entities)

    if FEED_WEATHER in coordinators:
        add_per_record_entities(
            entry,
            coordinators[FEED_WEATHER],
            data.areas,
            async_add_entities,
            lambda coordinator, area, station: [
                WeatherStationSensor(coordinator, area, station, description) for description in WEATHER_SENSORS
            ],
        )
    if FEED_SIGNS in coordinators:
        add_per_record_entities(
            entry,
            coordinators[FEED_SIGNS],
            data.areas,
            async_add_entities,
            lambda coordinator, area, sign: [MessageSignSensor(coordinator, area, sign)],
        )


# ---------------------------------------------------------------------------
# Chain control


class ChainControlSensor(CaltransEntity, SensorEntity):
    """Worst chain control in effect in the area."""

    _attr_translation_key = "chain_control"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = CHAIN_LEVELS

    @property
    def native_value(self) -> str:
        level, _ = worst_chain_control(self.matching())
        return level

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        points: list[ChainControlPoint] = self.matching()
        _level, worst = worst_chain_control(points)
        in_service = [p for p in points if p.in_service]
        active = sorted(
            (p for p in in_service if p.level not in (None, CHAIN_NONE)),
            key=lambda p: (-chain_rank(p.level or CHAIN_NONE), p.location.name),
        )
        changes = [p.since for p in in_service if p.since is not None]
        return {
            "status": worst.status if worst else None,
            "description": worst.description if worst else None,
            "segment_count": len(in_service),
            "segments_with_controls": len(active),
            "segments": [
                {
                    "location": p.location.name,
                    "direction": p.location.direction,
                    "elevation_ft": p.location.elevation_ft,
                    "status": p.status,
                    "level": p.level,
                    "description": p.description,
                    "since": _iso(p.since),
                }
                for p in active[:MAX_ATTR_ITEMS]
            ],
            "unrecognized_statuses": sorted({p.status for p in in_service if p.level is None and p.status}),
            "last_change": _iso(max(changes)) if changes else None,
        }


# ---------------------------------------------------------------------------
# Lane closures


class LaneClosureSensor(CaltransEntity, SensorEntity):
    """Number of lane closures in effect in the area."""

    _attr_translation_key = "lane_closures"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _current(self) -> list[LaneClosure]:
        now = dt_util.utcnow()
        return [
            c
            for c in self.matching()
            if c.indefinite or c.end is None or now - c.end <= CLOSURE_GRACE  # never closed out by the crew
        ]

    @property
    def native_value(self) -> int:
        return len(self._current())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        closures = sorted(self._current(), key=lambda c: (c.start is None, c.start or dt_util.utcnow(), c.id))
        return {
            "full_closures": sum(1 for c in closures if (c.closure_type or "").lower() == "full"),
            "shown": min(len(closures), MAX_ATTR_ITEMS),
            "closures": [
                {
                    "from": c.location.name,
                    "to": c.end_location.name,
                    "direction": c.location.direction,
                    "type": c.closure_type,
                    "work": c.work_type,
                    "lanes_closed": c.lanes_closed,
                    "total_lanes": c.total_lanes,
                    "facility": c.facility,
                    "started": _iso(c.start),
                    "planned_end": None if c.indefinite else _iso(c.end),
                }
                for c in closures[:MAX_ATTR_ITEMS]
            ],
        }


# ---------------------------------------------------------------------------
# Message signs


class SignSummarySensor(CaltransEntity, SensorEntity):
    """How many signs in the area show a message, with the messages as attributes."""

    _attr_translation_key = "message_signs"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _showing(self) -> list[MessageSign]:
        return sorted(
            (s for s in self.matching() if s.in_service and s.message),
            key=lambda s: (s.location.name, s.id),
        )

    @property
    def native_value(self) -> int:
        return len(self._showing())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "messages": [
                {
                    "location": s.location.name,
                    "direction": s.location.direction,
                    "message": s.message,
                    "updated": _iso(s.updated),
                }
                for s in self._showing()[:MAX_ATTR_ITEMS]
            ]
        }


class MessageSignSensor(CaltransEntity, SensorEntity):
    """Text on one sign (empty when blank). Off by default: busy routes have dozens of signs."""

    _attr_translation_key = "message_sign"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: CaltransFeedCoordinator, area: Area, sign: MessageSign) -> None:
        super().__init__(coordinator, area, f"sign_{sign.id}")
        self._sign_id = sign.id
        self._attr_translation_placeholders = {"sign": short_name(sign.location)}

    def _sign(self) -> MessageSign | None:
        return record_by_id(self.coordinator, self._sign_id)

    @property
    def available(self) -> bool:
        sign = self._sign()
        return super().available and sign is not None and sign.in_service

    @property
    def native_value(self) -> str | None:
        sign = self._sign()
        if sign is None:
            return None
        return sign.message[:MAX_STATE_LENGTH]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        sign = self._sign()
        if sign is None:
            return {}
        return {**location_attributes(sign.location), "updated": _iso(sign.updated)}


# ---------------------------------------------------------------------------
# Road weather stations


@dataclass(frozen=True, kw_only=True)
class WeatherSensorDescription(SensorEntityDescription):
    """How to read one value from a station."""

    value_fn: Callable[[WeatherStation], Any]


WEATHER_SENSORS: tuple[WeatherSensorDescription, ...] = (
    WeatherSensorDescription(
        key="surface_temperature",
        translation_key="surface_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda s: s.surface_temperature,
    ),
    WeatherSensorDescription(
        key="surface_status",
        translation_key="surface_status",
        device_class=SensorDeviceClass.ENUM,
        options=[status for status in SURFACE_STATUS.values() if status != "error"],
        value_fn=lambda s: s.surface_status,
    ),
    WeatherSensorDescription(
        key="air_temperature",
        translation_key="air_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda s: s.air_temperature,
    ),
    WeatherSensorDescription(
        key="visibility",
        translation_key="visibility",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=1,
        value_fn=lambda s: s.visibility_km,
    ),
    WeatherSensorDescription(
        key="wind_speed",
        translation_key="wind_speed",
        device_class=SensorDeviceClass.WIND_SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        suggested_display_precision=1,
        value_fn=lambda s: s.wind_speed,
    ),
    WeatherSensorDescription(
        key="wind_gust",
        translation_key="wind_gust",
        device_class=SensorDeviceClass.WIND_SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        suggested_display_precision=1,
        value_fn=lambda s: s.wind_gust,
    ),
    WeatherSensorDescription(
        key="wind_direction",
        translation_key="wind_direction",
        state_class=SensorStateClass.MEASUREMENT_ANGLE,
        device_class=SensorDeviceClass.WIND_DIRECTION,
        native_unit_of_measurement=DEGREE,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.wind_direction,
    ),
)


class WeatherStationSensor(CaltransEntity, SensorEntity):
    """One value from one road weather station."""

    entity_description: WeatherSensorDescription

    def __init__(
        self,
        coordinator: CaltransFeedCoordinator,
        area: Area,
        station: WeatherStation,
        description: WeatherSensorDescription,
    ) -> None:
        super().__init__(coordinator, area, f"rwis_{station.id}_{description.key}")
        self.entity_description = description
        self._station_id = station.id
        self._attr_translation_placeholders = {"station": short_name(station.location)}

    def _station(self) -> WeatherStation | None:
        return record_by_id(self.coordinator, self._station_id)

    @property
    def available(self) -> bool:
        station = self._station()
        if not super().available or station is None or not station.in_service:
            return False
        # Many stations stop reporting for days; their last reading is not today's weather.
        return station.updated is not None and dt_util.utcnow() - station.updated <= STATION_STALE_AFTER

    @property
    def native_value(self) -> Any:
        station = self._station()
        return None if station is None else self.entity_description.value_fn(station)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        station = self._station()
        if station is None:
            return {}
        attrs = {**location_attributes(station.location), "updated": _iso(station.updated)}
        if self.entity_description.key == "surface_status":
            attrs["freeze_point_c"] = station.freeze_point
            attrs["precipitation"] = station.precipitation
        elif self.entity_description.key == "air_temperature":
            attrs["humidity"] = station.humidity
            attrs["dewpoint_c"] = station.dewpoint
        return attrs
