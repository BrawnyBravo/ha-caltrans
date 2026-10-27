"""Cameras: still images from Caltrans traffic cameras on followed routes (off by default)."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.components.camera import Camera
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CAMERA_CACHE_SECONDS, FEED_CAMERAS
from .entity import CaltransEntity, add_per_record_entities, location_attributes, record_by_id, short_name

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import CaltransConfigEntry
    from .api import TrafficCamera
    from .coordinator import CaltransFeedCoordinator
    from .entity import Area

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

IMAGE_TIMEOUT = 20


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CaltransConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up cameras when the camera data type is turned on."""
    data = entry.runtime_data
    coordinator = data.coordinators.get(FEED_CAMERAS)
    if coordinator is None:
        return
    add_per_record_entities(
        entry,
        coordinator,
        data.areas,
        async_add_entities,
        lambda coordinator, area, camera: [CaltransCamera(coordinator, area, camera)],
    )


class CaltransCamera(CaltransEntity, Camera):
    """A Caltrans traffic camera still image."""

    _attr_translation_key = "camera"

    def __init__(self, coordinator: CaltransFeedCoordinator, area: Area, camera: TrafficCamera) -> None:
        CaltransEntity.__init__(self, coordinator, area, f"camera_{camera.id}")
        Camera.__init__(self)
        self._camera_id = camera.id
        self._attr_translation_placeholders = {"camera": short_name(camera.location)}
        self._cached: bytes | None = None
        self._cached_url: str | None = None
        self._cached_at = 0.0

    def _camera(self) -> TrafficCamera | None:
        return record_by_id(self.coordinator, self._camera_id)

    @property
    def available(self) -> bool:
        camera = self._camera()
        return super().available and camera is not None and camera.in_service

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        camera = self._camera()
        if camera is None:
            return {}
        return {
            **location_attributes(camera.location),
            "image_url": camera.image_url,
            "stream_url": camera.stream_url,
            "image_update_minutes": camera.update_minutes,
        }

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        """Return the current still image, fetched at most once a minute."""
        camera = self._camera()
        if camera is None:
            return None
        now = time.monotonic()
        if (
            self._cached is not None
            and self._cached_url == camera.image_url
            and now - self._cached_at < CAMERA_CACHE_SECONDS
        ):
            return self._cached
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(camera.image_url, timeout=aiohttp.ClientTimeout(total=IMAGE_TIMEOUT)) as resp:
                if resp.status != 200:
                    _LOGGER.debug("Camera image %s returned HTTP %s", camera.image_url, resp.status)
                    return self._cached
                image = await resp.read()
        except (TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.debug("Camera image %s failed: %s", camera.image_url, err)
            return self._cached
        self._cached, self._cached_url, self._cached_at = image, camera.image_url, now
        return image
