"""Службы Home Assistant для профилей настроек Kotel."""

from __future__ import annotations

import logging

import voluptuous as vol  # pyright: ignore[reportMissingImports]
from homeassistant.core import (  # pyright: ignore[reportMissingImports]
    HomeAssistant,
    ServiceCall,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError  # pyright: ignore[reportMissingImports]
from homeassistant.helpers import (  # pyright: ignore[reportMissingImports]
    config_validation as cv,
    device_registry as dr,
)

from .const import DOMAIN
from .profiles import ALL_SCOPES, DEFAULT_SCOPES, KotelProfileManager

_LOGGER = logging.getLogger(__name__)

# Поля цели (target) приходят вместе с данными службы, поэтому лишние ключи разрешены.
_BASE = {vol.Optional("device_id"): vol.All(cv.ensure_list, [cv.string])}


def _schema(fields: dict) -> vol.Schema:
    return vol.Schema({**_BASE, **fields}, extra=vol.ALLOW_EXTRA)


_SCOPES = vol.All(cv.ensure_list, [vol.In(ALL_SCOPES)])

SAVE_SCHEMA = _schema({
    vol.Required("name"): cv.string,
    vol.Optional("scopes", default=list(DEFAULT_SCOPES)): _SCOPES,
    vol.Optional("note", default=""): cv.string,
    vol.Optional("overwrite", default=False): cv.boolean,
})
APPLY_SCHEMA = _schema({
    vol.Required("name"): cv.string,
    vol.Optional("scopes"): _SCOPES,
    vol.Optional("dry_run", default=False): cv.boolean,
    vol.Optional("force", default=False): cv.boolean,
    vol.Optional("verify", default=True): cv.boolean,
})
UNDO_SCHEMA = _schema({
    vol.Optional("force", default=False): cv.boolean,
    vol.Optional("verify", default=True): cv.boolean,
})
DELETE_SCHEMA = _schema({vol.Required("name"): cv.string})
RENAME_SCHEMA = _schema({
    vol.Required("name"): cv.string,
    vol.Required("new_name"): cv.string,
})
LIST_SCHEMA = _schema({})
EXPORT_SCHEMA = _schema({
    vol.Optional("profiles"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("to_www", default=False): cv.boolean,
})
IMPORT_SCHEMA = _schema({
    vol.Required("file"): cv.string,
    vol.Optional("profiles"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("on_conflict", default="rename"): vol.In(["overwrite", "skip", "rename"]),
})


def _manager(hass: HomeAssistant) -> KotelProfileManager:
    return hass.data[DOMAIN]["_profiles"]


def resolve_device_ids(hass: HomeAssistant, manager: KotelProfileManager,
                       call: ServiceCall) -> list[str]:
    """Определить контроллеры по target; без target — единственный контроллер."""
    controllers = list(manager.controllers())
    requested = call.data.get("device_id")
    if not requested:
        return controllers
    registry = dr.async_get(hass)
    result: list[str] = []
    for device_id in requested:
        device = registry.async_get(device_id)
        found = None
        if device is not None:
            for domain, identifier in device.identifiers:
                if domain == DOMAIN and identifier in controllers:
                    found = identifier
        if found is None:
            raise ServiceValidationError(
                f"Устройство {device_id} не является контроллером Kotel")
        if found not in result:
            result.append(found)
    return result


def _single_device(hass: HomeAssistant, manager: KotelProfileManager,
                   call: ServiceCall) -> str:
    ids = resolve_device_ids(hass, manager, call)
    if not ids:
        raise ServiceValidationError("Контроллеры Kotel не найдены")
    if len(ids) > 1:
        raise ServiceValidationError(
            "Укажите контроллер (device_id): найдено несколько")
    return ids[0]


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Зарегистрировать службы (повторный вызов безопасен)."""
    if hass.services.has_service(DOMAIN, "save_profile"):
        return

    async def save_profile(call: ServiceCall):
        manager = _manager(hass)
        dev_id = _single_device(hass, manager, call)
        return await manager.async_save_profile(
            dev_id, call.data["name"], call.data["scopes"],
            call.data["note"], call.data["overwrite"])

    async def apply_profile(call: ServiceCall):
        manager = _manager(hass)
        dev_id = _single_device(hass, manager, call)
        return await manager.async_apply(
            dev_id, call.data["name"], call.data.get("scopes"),
            dry_run=call.data["dry_run"], force=call.data["force"],
            verify=call.data["verify"])

    async def undo_apply(call: ServiceCall):
        manager = _manager(hass)
        dev_id = _single_device(hass, manager, call)
        return await manager.async_undo(
            dev_id, force=call.data["force"], verify=call.data["verify"])

    async def delete_profile(call: ServiceCall):
        manager = _manager(hass)
        await manager.async_delete_profile(
            _single_device(hass, manager, call), call.data["name"])

    async def rename_profile(call: ServiceCall):
        manager = _manager(hass)
        await manager.async_rename_profile(
            _single_device(hass, manager, call), call.data["name"], call.data["new_name"])

    async def list_profiles(call: ServiceCall):
        manager = _manager(hass)
        devices = {}
        for dev_id in resolve_device_ids(hass, manager, call):
            profiles = []
            for name in manager.profile_names(dev_id):
                profile = manager.get_profile(dev_id, name)
                profiles.append({
                    "name": name,
                    "scopes": profile["scopes"],
                    "values": sum(len(v) for v in profile["values"].values()),
                    "note": profile.get("note", ""),
                    "firmware": profile.get("firmware"),
                    "updated": profile.get("updated"),
                })
            devices[dev_id] = {
                "name": manager.device_name(dev_id),
                "selected": manager.selected(dev_id),
                "last_applied": manager.last_applied(dev_id),
                "matching": manager.matching_profile(dev_id),
                "has_undo": manager.has_undo(dev_id),
                "profiles": profiles,
            }
        return {"devices": devices}

    async def export_profiles(call: ServiceCall):
        manager = _manager(hass)
        names = call.data.get("profiles")
        if call.data.get("device_id") or names:
            keys = []
            for dev_id in resolve_device_ids(hass, manager, call):
                for name in (names or manager.profile_names(dev_id)):
                    keys.append(f"{dev_id}|{name}")
        else:
            keys = None                      # все профили всех контроллеров
        return await manager.async_export(keys, to_www=call.data["to_www"])

    async def import_profiles(call: ServiceCall):
        manager = _manager(hass)
        dev_id = _single_device(hass, manager, call)
        return await manager.async_import(
            call.data["file"], names=call.data.get("profiles"),
            dev_id=dev_id, on_conflict=call.data["on_conflict"])

    register = hass.services.async_register
    register(DOMAIN, "save_profile", save_profile, schema=SAVE_SCHEMA,
             supports_response=SupportsResponse.OPTIONAL)
    register(DOMAIN, "apply_profile", apply_profile, schema=APPLY_SCHEMA,
             supports_response=SupportsResponse.OPTIONAL)
    register(DOMAIN, "undo_apply", undo_apply, schema=UNDO_SCHEMA,
             supports_response=SupportsResponse.OPTIONAL)
    register(DOMAIN, "delete_profile", delete_profile, schema=DELETE_SCHEMA)
    register(DOMAIN, "rename_profile", rename_profile, schema=RENAME_SCHEMA)
    register(DOMAIN, "list_profiles", list_profiles, schema=LIST_SCHEMA,
             supports_response=SupportsResponse.ONLY)
    register(DOMAIN, "export_profiles", export_profiles, schema=EXPORT_SCHEMA,
             supports_response=SupportsResponse.OPTIONAL)
    register(DOMAIN, "import_profiles", import_profiles, schema=IMPORT_SCHEMA,
             supports_response=SupportsResponse.OPTIONAL)


SERVICE_NAMES = (
    "save_profile", "apply_profile", "undo_apply", "delete_profile",
    "rename_profile", "list_profiles", "export_profiles", "import_profiles",
)


@callback
def async_unregister_services(hass: HomeAssistant) -> None:
    for name in SERVICE_NAMES:
        hass.services.async_remove(DOMAIN, name)
