"""Диагностика интеграции ReglerPro Controller.

Файл `diagnostics.py` автоматически подхватывается Home Assistant:
«Настройки → Устройства и службы → ReglerPro Controller → Скачать диагностику».

Сервер отдаёт данные дома по логину, паролю и номеру дома и не проверяет, что
пользователь состоит в этом доме. Поэтому номер дома и идентификаторы устройств
нужно считать секретными так же, как пароль: в диагностике они заменяются
обезличенными метками (`<home_id>`, `<device-1>` …) везде, в том числе внутри
строк и ключей словарей.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


# Значения этих ключей скрываются всегда
_REDACT_KEYS = {"password", "login", "home_id", "userId"}
_MIN_SECRET_LENGTH = 3


def _replacements(entry: ConfigEntry, coordinator) -> dict[str, str]:
    """Секретные значения -> метки; длинные первыми, чтобы не резать идентификаторы."""
    found: dict[str, str] = {}

    def add(value: Any, label: str) -> None:
        text = str(value or "").strip()
        if len(text) >= _MIN_SECRET_LENGTH and text not in found:
            found[text] = label

    add(entry.data.get("password"), "***")
    add(entry.data.get("login"), "<login>")
    add(entry.data.get("home_id"), "<home_id>")
    for number, dev_id in enumerate(sorted((coordinator.data or {}) if coordinator else {}), 1):
        add(dev_id, f"<device-{number}>")
    return dict(sorted(found.items(), key=lambda item: -len(item[0])))


def _scrub(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            _scrub(key, replacements): (
                "***" if key in _REDACT_KEYS else _scrub(item, replacements)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(item, replacements) for item in value]
    if isinstance(value, str):
        for secret, label in replacements.items():
            value = value.replace(secret, label)
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Вернуть диагностику записи без учётных данных, номера дома и идентификаторов."""
    runtime = getattr(entry, "runtime_data", None)      # нет, если запись не загружена
    coordinator = getattr(runtime, "coordinator", None)
    api = getattr(runtime, "api", None)

    devices: dict[str, Any] = {}
    if coordinator is not None and coordinator.data:
        for dev_id, data in coordinator.data.items():
            devices[dev_id] = {
                "name": data.get("name"),
                "type": data.get("type"),
                "online": data.get("online"),
                "mqtt_online": data.get("mqtt_online"),
                "detail_ok": data.get("detail_ok"),
                "params_count": len(data.get("params") or {}),
                "climate": data.get("climate"),
                "raw": data.get("raw"),
                "list_raw": data.get("list_raw"),
            }

    result = {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "api": {
            # на каком из серверов клиент работает сейчас
            "servers": list(getattr(api, "servers", ())) if api is not None else None,
            "active_server": getattr(api, "active_url", None) if api is not None else None,
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
    return _scrub(result, _replacements(entry, coordinator))
