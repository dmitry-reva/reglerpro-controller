"""Диагностика интеграции ReglerPro Controller.

Файл `diagnostics.py` автоматически подхватывается Home Assistant:
«Настройки → Устройства и службы → ReglerPro Controller → Скачать диагностику».
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_REDACT_KEYS = {"password", "login"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("***" if key in _REDACT_KEYS else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Вернуть диагностику записи без учётных данных."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = runtime.get("coordinator")

    devices: dict[str, Any] = {}
    if coordinator is not None and coordinator.data:
        for dev_id, data in coordinator.data.items():
            devices[dev_id] = {
                "name": data.get("name"),
                "type": data.get("type"),
                "online": data.get("mqtt_online"),
                "params_count": len(data.get("params") or {}),
                "climate": data.get("climate"),
                "raw": _redact(data.get("raw")),
                "list_raw": _redact(data.get("list_raw")),
            }

    return {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "data": _redact(dict(entry.data)),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": (
                coordinator.last_update_success if coordinator is not None else None
            ),
            "last_exception": (
                repr(coordinator.last_exception)
                if coordinator is not None and coordinator.last_exception
                else None
            ),
            "update_interval": (
                str(coordinator.update_interval)
                if coordinator is not None and coordinator.update_interval
                else None
            ),
            "devices": devices,
        },
    }
