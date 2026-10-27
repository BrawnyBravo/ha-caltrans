"""One update coordinator per Caltrans feed."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import PARSERS, CaltransError, feed_url, fetch_feed
from .const import (
    DOMAIN,
    FEED_LABELS,
    FEED_WEATHER,
    FILE_STALE_AFTER,
    REPAIR_AFTER,
    REQUEST_TIMEOUTS,
    STALE_AFTER,
    UPDATE_INTERVALS,
)

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class CaltransFeedCoordinator(DataUpdateCoordinator[list[Any]]):
    """Fetches one district's file for one feed and keeps the last good copy."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, district: int, feed: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} district {district} {feed}",
            update_interval=UPDATE_INTERVALS[feed],
        )
        self.district = district
        self.feed = feed
        self.url = feed_url(district, feed)
        self.stale_after = STALE_AFTER[feed]
        self.last_success: datetime | None = None
        self.last_error: str | None = None
        self.failing_since: datetime | None = None
        self.raw_bytes: int | None = None
        self.request_timeout: float = REQUEST_TIMEOUTS[feed]

    @property
    def issue_id(self) -> str:
        """Repair issue id for this feed."""
        assert self.config_entry is not None  # noqa: S101
        return f"feed_unavailable_{self.config_entry.entry_id}_{self.feed}"

    def is_fresh(self) -> bool:
        """True while the last good data is recent enough to show."""
        if self.data is None or self.last_success is None:
            return False
        return dt_util.utcnow() - self.last_success <= self.stale_after

    async def _async_update_data(self) -> list[Any]:
        session = async_get_clientsession(self.hass)
        try:
            raw = await fetch_feed(session, self.district, self.feed, self.request_timeout)
            # Lane closure files reach 15 MB; parse off the event loop.
            records = await self.hass.async_add_executor_job(PARSERS[self.feed], raw)
        except CaltransError as err:
            self._failed(str(err))
            raise UpdateFailed(str(err)) from err
        except Exception as err:  # a parser bug must still count as a failed update
            error = f"Could not read {self.url}: {type(err).__name__}: {err}"
            self._failed(error)
            raise UpdateFailed(error) from err
        if (error := self._old_copy_error(records)) is not None:
            self._failed(error)
            raise UpdateFailed(error)
        self._succeeded(len(raw))
        self._cache()[(self.district, self.feed)] = (records, self.last_success, len(raw))
        return records

    def _old_copy_error(self, records: list[Any]) -> str | None:
        """An error message if the file is an old copy, judged by its newest record."""
        limit = FILE_STALE_AFTER.get(self.feed)
        attr = "updated" if self.feed == FEED_WEATHER else "record_time"
        times = [t for r in records if (t := getattr(r, attr, None)) is not None]
        if limit is None or not times:
            return None
        newest = max(times)
        if dt_util.utcnow() - newest <= limit:
            return None
        return f"Caltrans is serving an old copy of {self.url} (newest record {newest.isoformat()})"

    def _cache(self) -> dict[tuple[int, str], tuple[list[Any], Any, int | None]]:
        return self.hass.data.setdefault(DOMAIN, {}).setdefault("cache", {})

    def seed_from_cache(self) -> bool:
        """
        Reuse data another coordinator for the same file fetched recently.

        An options change reloads the entry; without this every reload would download
        every file again, lane closures included.
        """
        cached = self._cache().get((self.district, self.feed))
        if cached is None:
            return False
        records, fetched, size = cached
        assert self.update_interval is not None  # noqa: S101
        if dt_util.utcnow() - fetched >= self.update_interval:
            return False
        self.data, self.last_success, self.raw_bytes = records, fetched, size
        self.last_update_success = True
        return True

    def _succeeded(self, size: int) -> None:
        self.last_success = dt_util.utcnow()
        self.last_error = None
        self.failing_since = None
        self.raw_bytes = size
        self._down_since_store().pop(self.issue_id, None)
        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)

    def _down_since_store(self) -> dict[str, Any]:
        """When each feed started failing, kept outside the coordinator so setup retries and reloads keep it."""
        return self.hass.data.setdefault(DOMAIN, {}).setdefault("down_since", {})

    def _failed(self, error: str) -> None:
        now = dt_util.utcnow()
        self.last_error = error
        if self.failing_since is None:
            self.failing_since = now
        first_failure = self._down_since_store().setdefault(self.issue_id, now)
        down_since = self.last_success or first_failure
        if now - down_since >= REPAIR_AFTER:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self.issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="feed_unavailable",
                translation_placeholders={
                    "feed": FEED_LABELS[self.feed],
                    "district": str(self.district),
                    "url": self.url,
                    "error": error,
                    "docs_url": "https://cwwp2.dot.ca.gov/documentation/",
                },
            )
        # The base class only notifies entities on the first failure in a row; tell them
        # every time so they go unavailable once the data passes its stale limit.
        self.async_update_listeners()
