"""Координатор данных для интеграции Kotel."""
from email.policy import default
import json

def _as_dict(value) -> dict:
    """tstatArr / tempUstNowTstate могут прийти строкой JSON или словарём."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}

import logging
from datetime import timedelta

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed  # type: ignore[import-not-found]

from .api import KotelApiClient, KotelApiError
from .const import DOMAIN, DEFAULT_SCAN_INTERVAL
from .params_parser import get_params

_LOGGER = logging.getLogger(__name__)


class KotelCoordinator(DataUpdateCoordinator):
    """Координатор, опрашивающий сервер taptop.php по расписанию."""

    def __init__(self, hass, api: KotelApiClient):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self._api = api

    async def _async_update_data(self) -> dict:
        """Получить свежие данные всех устройств.

        Шаг 1: get_all_dev — список всех устройств дома.
        Шаг 2: для каждого контроллера — get_big_contr (детальные данные).
        Шаг 3: для каждого датчика — get_big_dt.
        """
        try:
            devices = await self._api.get_all_devices()
        except KotelApiError as err:
            raise UpdateFailed(f"Не удалось получить список устройств: {err}") from err

        result = {}

        for dev in devices:
            dev_type = dev.get("type")
            dev_id = dev.get("id_name", "")
            dev_name = dev.get("name", dev_id)

            if dev_type == "contr":
                # Контроллер: получаем детальные данные
                try:
                    details = await self._api.get_controller(dev_id)
                except KotelApiError as err:
                    _LOGGER.warning("Не удалось получить данные контроллера %s: %s", dev_id, err)
                    details = dev  # используем данные из списка

                params = get_params(details.get("params", ""))
                
                def pick(key, default=None):
                # детальные данные приоритетнее, но если поля нет — берём из списка
                    return details.get(key, dev.get(key, default))
                tstat = _as_dict(pick("tstatArr"))
                tstat_now_ust = _as_dict(pick("tempUstNowTstate"))

                
                result[dev_id] = {
                    "type": "contr",
                    "name": dev_name,
                    "id": dev_id,
                    "params": params,
                    "mqtt_online": details.get("mqtt_online", "0"),
                    "climate": {
                        "enabled": bool(tstat.get("tstatOn")),
                        "sensor_selected": bool(tstat.get("tstatDtId")),
                        # как в JS: климат работает, только если включён И выбран датчик
                        "active": bool(tstat.get("tstatOn") and tstat.get("tstatDtId")),
                        "sensor_name": pick("sensor_name"),
                        "sensor_temp": pick("sensor_temp"),
                        "target_temp": tstat_now_ust.get("temp"),
                        "period_text": tstat_now_ust.get("textPeriodOn"),
                    },
                    "raw": details,
                    "list_raw": dev,  # элемент из get_all_dev (для диагностики)
                }
                
            elif dev_type == "dt":
                # Датчик температуры: данные уже есть в списке
                result[dev_id] = {
                    "type": "dt",
                    "name": dev_name,
                    "id": dev_id,
                    "temp": dev.get("temp"),
                    "time": dev.get("time", 0),
                    "raw": dev,
                }

        return result
