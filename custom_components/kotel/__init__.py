"""Интеграция Kotel для Home Assistant."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .api import KotelApiClient
from .coordinator import KotelCoordinator
from .profiles import KotelProfileManager
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "number", "switch", "climate", "select", "text", "button"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Настройка интеграции при добавлении через UI."""
    api = KotelApiClient(
        base_url=entry.data["url"],
        login=entry.data["login"],
        password=entry.data["password"],
        home_id=entry.data.get("home_id", ""),
    )

    coordinator = KotelCoordinator(hass, api)
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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Выгрузка интеграции."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["api"].close()
        manager = hass.data[DOMAIN].get("_profiles")
        if manager is not None:
            manager.unregister_entry(entry.entry_id)
            if not manager.has_entries:
                async_unregister_services(hass)
    return unload_ok
