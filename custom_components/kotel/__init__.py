"""Интеграция Kotel для Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import KotelApiClient
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import KotelCoordinator
from .profiles import KotelProfileManager
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ("sensor", "number", "switch", "climate", "select", "text", "button")


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Перезагрузить запись при изменении параметров (например, интервала)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Настройка интеграции при добавлении через UI."""
    api = KotelApiClient(
        base_url=entry.data["url"],
        login=entry.data["login"],
        password=entry.data["password"],
        home_id=entry.data.get("home_id", ""),
        session=async_get_clientsession(hass),
    )

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = KotelCoordinator(hass, api, scan_interval)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    # Менеджер профилей один на всю интеграцию (профили хранятся по id контроллера)
    manager = hass.data[DOMAIN].get("_profiles")
    if manager is None:
        manager = KotelProfileManager(hass)
        await manager.async_load()
        hass.data[DOMAIN]["_profiles"] = manager
    manager.register_entry(entry.entry_id, coordinator, api)

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
    }

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Выгрузка интеграции."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_data = hass.data.get(DOMAIN, {})
        data = domain_data.pop(entry.entry_id, None)
        if data is not None:
            await data["api"].close()
        manager = domain_data.get("_profiles")
        if manager is not None:
            manager.unregister_entry(entry.entry_id)
            if not manager.has_entries:
                async_unregister_services(hass)
    return unload_ok
