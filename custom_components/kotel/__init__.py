"""Интеграция Kotel для Home Assistant."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import KotelApiClient
from .const import (
    CONF_FALLBACK_URLS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import KotelCoordinator
from .profiles import KotelProfileManager
from .runtime import KotelRuntimeData
from .services import async_register_services

if TYPE_CHECKING:  # pragma: no cover - только для типов
    from .runtime import KotelConfigEntry

_LOGGER = logging.getLogger(__name__)

# Настройка только через интерфейс (config flow), без YAML
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.CLIMATE,
    Platform.SELECT,
    Platform.TEXT,
    Platform.BUTTON,
]


def _reload_key(entry: ConfigEntry) -> str:
    """Параметры, от которых зависит работа записи, без пароля.

    Пароль не входит: после повторной авторизации запись перезагружает сам поток
    (слушателя может не быть, если первый опрос закончился отказом), иначе
    перезагрузка выполнялась бы дважды.
    """
    data = {key: value for key, value in entry.data.items() if key != "password"}
    return json.dumps({"data": data, "options": dict(entry.options)},
                      sort_keys=True, default=str)


def _make_update_listener(entry: ConfigEntry):
    """Слушатель: перезагрузить запись при изменении параметров (интервал, серверы)."""
    initial = _reload_key(entry)

    async def _async_update_listener(hass: HomeAssistant, updated: ConfigEntry) -> None:
        if _reload_key(updated) != initial:
            await hass.config_entries.async_reload(updated.entry_id)

    return _async_update_listener


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Службы и менеджер профилей общие для всех записей: создаются один раз.

    Службы регистрируются здесь, а не при настройке записи: они должны быть
    доступны, пока интеграция загружена, и сообщать понятную ошибку, если ни
    одна запись ещё не работает.
    """
    manager = KotelProfileManager(hass)
    await manager.async_load()
    hass.data.setdefault(DOMAIN, {})["_profiles"] = manager
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: KotelConfigEntry) -> bool:
    """Настройка интеграции при добавлении через UI."""
    api = KotelApiClient(
        base_url=entry.data["url"],
        login=entry.data["login"],
        password=entry.data["password"],
        home_id=entry.data.get("home_id", ""),
        session=async_get_clientsession(hass),
        # None — автоматически (серверы из KNOWN_SERVERS), список — выбор из «Серверы»
        fallback_urls=entry.options.get(CONF_FALLBACK_URLS),
    )

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = KotelCoordinator(hass, api, scan_interval, config_entry=entry)
    await coordinator.async_config_entry_first_refresh()

    # Менеджер профилей один на всю интеграцию (профили хранятся по id контроллера)
    hass.data[DOMAIN]["_profiles"].register_entry(entry.entry_id, coordinator, api)

    entry.runtime_data = KotelRuntimeData(api=api, coordinator=coordinator)

    entry.async_on_unload(entry.add_update_listener(_make_update_listener(entry)))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: KotelConfigEntry) -> bool:
    """Выгрузка интеграции."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        manager = hass.data.get(DOMAIN, {}).get("_profiles")
        if manager is not None:
            manager.unregister_entry(entry.entry_id)
        await entry.runtime_data.api.close()
    return unload_ok
