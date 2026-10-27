"""Config and options flow: pick a district, then routes (or "near home"), then data types."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import PARSERS, CaltransError, fetch_feed, natural_route_key
from .const import (
    CONF_DISTRICT,
    CONF_FEEDS,
    CONF_MODE,
    CONF_RADIUS,
    CONF_ROUTES,
    DEFAULT_FEEDS,
    DEFAULT_RADIUS_KM,
    DISTRICT_FEEDS,
    DISTRICTS,
    DOMAIN,
    FEED_CHAIN_CONTROL,
    FEED_SIGNS,
    FEED_WEATHER,
    FEEDS,
    MAX_RADIUS_KM,
    MODE_NEAR,
    MODE_ROUTES,
    REQUEST_TIMEOUTS,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# Small feeds that list the routes worth following. Lane closures are left out (megabytes).
ROUTE_SOURCES = (FEED_CHAIN_CONTROL, FEED_SIGNS, FEED_WEATHER)


class NoRoutesError(Exception):
    """None of the route-listing feeds could be loaded."""


async def async_fetch_routes(hass: HomeAssistant, district: int) -> list[str]:
    """Routes that appear in a district's chain control, sign and weather feeds."""
    session = async_get_clientsession(hass)
    sources = [feed for feed in ROUTE_SOURCES if district in DISTRICT_FEEDS[feed]]

    async def one(feed: str) -> list[Any] | None:
        try:
            raw = await fetch_feed(session, district, feed, REQUEST_TIMEOUTS[feed])
            return await hass.async_add_executor_job(PARSERS[feed], raw)
        except CaltransError:
            return None

    results = await asyncio.gather(*(one(feed) for feed in sources))
    if all(result is None for result in results):
        raise NoRoutesError
    routes = {record.location.route for result in results if result for record in result if record.location.route}
    return sorted(routes, key=natural_route_key)


def _feed_selector(district: int) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[feed for feed in FEEDS if district in DISTRICT_FEEDS[feed]],
            multiple=True,
            mode=SelectSelectorMode.LIST,
            translation_key="data_type",
        )
    )


def _default_feeds(district: int, current: list[str] | None = None) -> list[str]:
    wanted = current if current is not None else DEFAULT_FEEDS
    return [feed for feed in wanted if district in DISTRICT_FEEDS[feed]]


def _routes_schema(district: int, routes: list[str], selected: list[str], feeds: list[str]) -> vol.Schema:
    options = sorted(set(routes) | set(selected), key=natural_route_key)
    return vol.Schema(
        {
            vol.Required(CONF_ROUTES, default=selected): SelectSelector(
                SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(CONF_FEEDS, default=feeds): _feed_selector(district),
        }
    )


def _near_schema(district: int, radius: float, feeds: list[str]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_RADIUS, default=radius): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=MAX_RADIUS_KM, step=1, unit_of_measurement="km", mode=NumberSelectorMode.BOX
                )
            ),
            vol.Required(CONF_FEEDS, default=feeds): _feed_selector(district),
        }
    )


class CaltransConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a Caltrans district."""

    VERSION = 1

    def __init__(self) -> None:
        self._district = 0
        self._mode = MODE_ROUTES
        self._routes: list[str] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:  # noqa: ARG004
        """Options flow."""
        return CaltransOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose district and mode."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._district = int(user_input[CONF_DISTRICT])
            self._mode = user_input[CONF_MODE]
            await self.async_set_unique_id(f"{self._district}_{self._mode}")
            self._abort_if_unique_id_configured()
            if self._mode == MODE_NEAR:
                return await self.async_step_near()
            try:
                self._routes = await async_fetch_routes(self.hass, self._district)
            except NoRoutesError:
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_routes()

        schema = vol.Schema(
            {
                vol.Required(CONF_DISTRICT): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=str(number), label=f"District {number}: {area}")
                            for number, area in DISTRICTS.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_MODE, default=MODE_ROUTES): SelectSelector(
                    SelectSelectorConfig(
                        options=[MODE_ROUTES, MODE_NEAR], mode=SelectSelectorMode.LIST, translation_key="mode"
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_routes(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose routes and data types."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_ROUTES]:
                errors[CONF_ROUTES] = "no_routes"
            elif not user_input[CONF_FEEDS]:
                errors[CONF_FEEDS] = "no_data_types"
            else:
                return self.async_create_entry(
                    title=f"Caltrans District {self._district}",
                    data={CONF_DISTRICT: self._district, CONF_MODE: MODE_ROUTES},
                    options={CONF_ROUTES: user_input[CONF_ROUTES], CONF_FEEDS: user_input[CONF_FEEDS]},
                )
        return self.async_show_form(
            step_id="routes",
            data_schema=_routes_schema(self._district, self._routes, [], _default_feeds(self._district)),
            errors=errors,
            description_placeholders={"district": str(self._district), "count": str(len(self._routes))},
        )

    async def async_step_near(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose a radius around the home location and data types."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_FEEDS]:
                errors[CONF_FEEDS] = "no_data_types"
            else:
                return self.async_create_entry(
                    title=f"Caltrans District {self._district} near home",
                    data={CONF_DISTRICT: self._district, CONF_MODE: MODE_NEAR},
                    options={CONF_RADIUS: user_input[CONF_RADIUS], CONF_FEEDS: user_input[CONF_FEEDS]},
                )
        return self.async_show_form(
            step_id="near",
            data_schema=_near_schema(self._district, DEFAULT_RADIUS_KM, _default_feeds(self._district)),
            errors=errors,
            description_placeholders={"district": str(self._district)},
        )


class CaltransOptionsFlow(OptionsFlow):
    """Change routes (or radius) and data types."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """One form with the current choices."""
        entry = self.config_entry
        district = entry.data[CONF_DISTRICT]
        near = entry.data.get(CONF_MODE) == MODE_NEAR
        errors: dict[str, str] = {}
        if user_input is not None:
            if not near and not user_input[CONF_ROUTES]:
                errors[CONF_ROUTES] = "no_routes"
            elif not user_input[CONF_FEEDS]:
                errors[CONF_FEEDS] = "no_data_types"
            else:
                return self.async_create_entry(data=user_input)

        feeds = _default_feeds(district, entry.options.get(CONF_FEEDS))
        if near:
            schema = _near_schema(district, entry.options.get(CONF_RADIUS, DEFAULT_RADIUS_KM), feeds)
        else:
            try:
                routes = await async_fetch_routes(self.hass, district)
            except NoRoutesError:
                routes = []  # Caltrans unreachable: still allow editing the routes already chosen
            schema = _routes_schema(district, routes, entry.options.get(CONF_ROUTES, []), feeds)
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
