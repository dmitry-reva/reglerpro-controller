"""Координатор данных для интеграции Kotel."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from datetime import timedelta

from homeassistant.helpers.update_coordinator import (  # type: ignore[import-not-found]
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import KotelApiClient, KotelApiError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .params_parser import get_params

_LOGGER = logging.getLogger(__name__)

# Ключи расписания в tstatArr: m3_P1_time, m4_P2_d5_temp, m5_P4_d1_time ...
_SCHEDULE_KEY_RE = re.compile(r"^m[345]_P[1-4]_(d\d_)?(time|temp)$")


def _as_dict(value) -> dict:
    """tstatArr / tempUstNowTstate могут прийти строкой JSON или словарём."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _truthy(value) -> bool:
    """Флаг из API: 1/0, true/false и строки "1"/"0" (строка "0" — это False)."""
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "off", "none", "null")
    return bool(value)


def to_number(value):
    """Привести значение к int/float, иначе вернуть None ("24", "23,5", 24.0)."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        number = value
    elif isinstance(value, str):
        text = value.strip().replace(",", ".")
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    if isinstance(number, float):
        if not math.isfinite(number):
            return None
        if number.is_integer():
            return int(number)
    return number


def _parse_climate(pick) -> dict:
    """Собрать данные климат-контроля из ответа контроллера.

    pick(key) возвращает поле из детального ответа, а если его там нет — из
    элемента списка get_all_dev.
    """
    tstat = _as_dict(pick("tstatArr"))
    now_ust = _as_dict(pick("tempUstNowTstate"))

    enabled = _truthy(tstat.get("tstatOn"))
    sensor_id = tstat.get("tstatDtId") or None

    target_temp = to_number(now_ust.get("temp"))
    if target_temp is None:
        target_temp = to_number(pick("temp_dt_ust"))

    # textPeriodOn в реальных ответах лежит на верхнем уровне,
    # в JS (список устройств) — внутри tempUstNowTstate. Поддерживаем оба места.
    period_text = now_ust.get("textPeriodOn") or pick("textPeriodOn") or None

    return {
        "enabled": enabled,
        "sensor_selected": bool(sensor_id),
        # как в JS: климат работает, только если включён И выбран датчик
        "active": enabled and bool(sensor_id),
        "mode": to_number(tstat.get("tstat_mode")),     # 0 / 3 / 4 / 5
        "target_const": to_number(tstat.get("tstatTemp")),
        "target_temp": target_temp,                     # актуальная цель (с учётом периода)
        "period": to_number(now_ust.get("period")),
        "period_text": period_text,
        "sensor_id": sensor_id,
        "sensor_name": pick("sensor_name"),
        "sensor_temp": to_number(pick("sensor_temp")),
        # все значения расписания, которые вернул сервер (ключи m3_*, m4_*, m5_*)
        "schedule": {
            key: value for key, value in tstat.items() if _SCHEDULE_KEY_RE.match(key)
        },
    }


class KotelCoordinator(DataUpdateCoordinator):
    """Координатор, опрашивающий сервер taptop.php по расписанию."""

    def __init__(self, hass, api: KotelApiClient,
                 scan_interval: int = DEFAULT_SCAN_INTERVAL):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self._api = api

    async def _async_update_data(self) -> dict:
        """Получить свежие данные всех устройств.

        Шаг 1: get_all_dev — список всех устройств дома.
        Шаг 2: для всех контроллеров — get_big_contr параллельно.
        Шаг 3: для датчиков используются данные из списка.
        """
        try:
            devices = await self._api.get_all_devices()
        except KotelApiError as err:
            raise UpdateFailed(f"Не удалось получить список устройств: {err}") from err

        devices = [dev for dev in devices if isinstance(dev, dict)]
        controllers = [
            dev for dev in devices
            if dev.get("type") == "contr" and dev.get("id_name")
        ]
        if controllers:
            results = await asyncio.gather(
                *(self._api.get_controller(dev["id_name"]) for dev in controllers),
                return_exceptions=True,
            )
        else:
            results = []

        details_by_id: dict[str, dict] = {}
        for dev, result in zip(controllers, results):
            dev_id = dev["id_name"]
            if isinstance(result, Exception):
                if isinstance(result, KotelApiError):
                    _LOGGER.warning(
                        "Не удалось получить данные контроллера %s: %s", dev_id, result)
                else:
                    _LOGGER.error(
                        "Неожиданная ошибка при получении контроллера %s: %r",
                        dev_id, result,
                        exc_info=(type(result), result, result.__traceback__),
                    )
                details_by_id[dev_id] = {}
            else:
                details_by_id[dev_id] = result or {}

        result_data = {}

        for dev in devices:
            dev_type = dev.get("type")
            dev_id = dev.get("id_name", "")
            dev_name = dev.get("name", dev_id)
            if not dev_id:
                continue

            if dev_type == "contr":
                details = details_by_id.get(dev_id) or dev

                def pick(key, default=None, _details=details, _dev=dev):
                    # детальные данные приоритетнее, но если поля нет — берём из списка
                    value = _details.get(key)
                    return value if value is not None else _dev.get(key, default)

                params = get_params(pick("params", ""))
                result_data[dev_id] = {
                    "type": "contr",
                    "name": dev_name,
                    "id": dev_id,
                    "params": params,
                    # приводим к "1"/"0", как ожидают сенсоры и проверки профилей
                    "mqtt_online": "1" if _truthy(pick("mqtt_online", 0)) else "0",
                    "climate": _parse_climate(pick),
                    "raw": details,
                    "list_raw": dev,  # элемент из get_all_dev (для диагностики)
                }

            elif dev_type == "dt":
                result_data[dev_id] = {
                    "type": "dt",
                    "name": dev_name,
                    "id": dev_id,
                    "temp": dev.get("temp"),
                    "time": dev.get("time", 0),
                    "raw": dev,
                }

        # Температура датчика климат-контроля: контроллер отдаёт её целой
        # (sensor_temp = "24"), а у самого датчика из списка устройств есть
        # десятые. Если датчик найден, берём его значение.
        for dev_data in result_data.values():
            if dev_data["type"] != "contr":
                continue
            climate = dev_data["climate"]
            sensor = result_data.get(climate.get("sensor_id"))
            precise = to_number(sensor.get("temp")) if sensor else None
            if precise is not None:
                climate["sensor_temp"] = precise

        return result_data
