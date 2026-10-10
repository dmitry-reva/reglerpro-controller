"""Клиент API для Kotel (на основе функций из functions.js)."""

import logging
import aiohttp  # pyright: ignore[reportMissingImports]
import asyncio
from .const import API_PATH

_LOGGER = logging.getLogger(__name__)


class KotelApiError(Exception):
    """Ошибка API Kotel."""


class KotelApiClient:
    # Асинхронный клиент для общения с сервером taptop.php
    def __init__(self, base_url: str, login: str, password: str, home_id: str = ""):
        self._base_url = base_url.rstrip("/")
        self._login = login
        self._password = password
        self._home_id = home_id
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _post(self, payload: dict) -> dict | list:
        """Отправка POST-запроса к taptop.php (аналог post_action из functions.js)."""
        url = f"{self._base_url}{API_PATH}"
        session = await self._get_session()
        _LOGGER.debug("POST %s | action=%s", url, payload.get("action"))
        try:
            async with session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    raise KotelApiError(f"HTTP {resp.status}")
                data = await resp.json(content_type=None)
                _LOGGER.debug("Response: %s", data)
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise KotelApiError(f"Ошибка соединения: {err}") from err

    async def get_all_devices(self) -> list:
        """Получить список всех устройств (action=get_all_dev).

        Аналог post_action("", "get_all_dev", "", "", login, pass, "", home_id).
        Возвращает массив устройств type "contr" или "dt".
        """
        payload = {
            "action": "get_all_dev",
            "login": self._login,
            "password": self._password,
            "action_value1": self._home_id,
            "action_value2": "",
        }
        data = await self._post(payload)
        if not isinstance(data, list):
            return []
        return data

    async def get_controller(self, device_id: str) -> dict:
        """Получить детальные данные контроллера (action=get_big_contr).

        Аналог post_action(home_id, "get_big_contr", device_id, "", login, pass).
        """
        payload = {
            "action": "get_big_contr",
            "id_name": device_id,
            "login": self._login,
            "password": self._password,
            "action_value1": "",
            "action_value2": "",
        }
        data = await self._post(payload)
        return data if isinstance(data, dict) else {}

    async def get_sensor(self, device_id: str) -> dict:
#        """Получить детальные данные датчика (action=get_big_dt).
#        Аналог post_action(home_id, "get_big_dt", device_id, "", login, pass).
#        """
        payload = {
            "action": "get_big_dt",
            "id_name": device_id,
            "login": self._login,
            "password": self._password,
            "action_value1": "",
            "action_value2": "",
        }
        data = await self._post(payload)
        return data if isinstance(data, dict) else {}

    async def send_command(self, action: str, device_id: str, range_value: int | float = 0) -> dict:
        """Отправить команду управления (аналог post_action с range).

        Примеры action: "temp_w_ust", "temp_w_min", "nasos_t", "korect_",
        "produv_time", "produv_interval", "vent_min", "vent_max",
        "time_ugas", "start__", "stop___", "korect_dt".
        """
        payload = {
            "action": action,
            "id_name": device_id,
            "range": range_value,
            "login": self._login,
            "password": self._password,
            "action_value1": "",
            "action_value2": "",
        }
        # Для korect_ сервер ожидает value + 20 (как в functions.js)
        if action == "korect_":
            payload["range"] = range_value + 20
        data = await self._post(payload)
        return data if isinstance(data, dict) else {}

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
