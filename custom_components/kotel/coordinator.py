"""Координатор данных для интеграции Kotel."""

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
                result[dev_id] = {
                    "type": "contr",
                    "name": dev_name,
                    "id": dev_id,
                    "params": params,
                    "mqtt_online": details.get("mqtt_online", "0"),
                    "raw": details,
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
