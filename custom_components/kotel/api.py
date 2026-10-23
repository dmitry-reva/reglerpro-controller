"""Клиент API для Kotel (на основе функций из functions.js)."""

from __future__ import annotations

import asyncio
import logging

import aiohttp  # pyright: ignore[reportMissingImports]

from .const import (
    ACTION_CLIMATE_GET_SENSORS,
    ACTION_CLIMATE_MODE,
    ACTION_CLIMATE_ON_OFF,
    ACTION_CLIMATE_RANGE,
    ACTION_CLIMATE_SELECT_SENSOR,
    API_PATH,
)

_LOGGER = logging.getLogger(__name__)

_REDACT_KEYS = {"login", "password"}


def _redact(value):
    """Скрыть учётные данные, если сервер вернул их в ответе."""
    if isinstance(value, dict):
        return {
            key: ("***" if key in _REDACT_KEYS else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class KotelApiError(Exception):
    """Ошибка API Kotel."""


class KotelApiClient:
    """Асинхронный клиент для общения с сервером taptop.php."""

    def __init__(
        self,
        base_url: str,
        login: str,
        password: str,
        home_id: str = "",
        session: aiohttp.ClientSession | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._login = login
        self._password = password
        self._home_id = home_id
        self._session = session
        self._owns_session = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def _post(self, payload: dict) -> dict | list:
        """Отправка POST-запроса к taptop.php (аналог post_action из functions.js)."""
        url = f"{self._base_url}{API_PATH}"
        session = await self._get_session()
        action = payload.get("action")
        _LOGGER.debug("POST %s | action=%s", url, action)
        try:
            async with session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    raise KotelApiError(f"HTTP {resp.status}")
                # Сервер облака может отдавать JSON с другим Content-Type.
                data = await resp.json(content_type=None)
                _LOGGER.debug("Response action=%s: %s", action, _redact(data))
                return data
        except KotelApiError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
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
        """Получить детальные данные датчика (action=get_big_dt)."""
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

    async def send_command(
        self, action: str, device_id: str, range_value: int | float = 0
    ) -> dict:
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

    # ------------------------------------------------------------------
    # Климат-контроль (формат запросов как в climate.js / post_action)
    # ------------------------------------------------------------------
    async def _climate_post(
        self,
        action: str,
        device_id: str,
        value: str | int | float = "",
        range_value: int | float = 0,
    ) -> dict | list:
        """Запрос климат-контроля.

        В отличие от send_command, значение передаётся в поле `climat_war1`,
        а числовое значение слайдера — в `range` (как в post_action из JS).
        """
        payload = {
            "climat_war1": value,
            "action": action,
            "range": range_value,
            "id_name": device_id,
            "login": self._login,
            "password": self._password,
        }
        if self._home_id:
            payload["userId"] = self._home_id
        return await self._post(payload)

    async def climate_on_off(self, device_id: str) -> None:
        """Переключить климат-контроль (ВНИМАНИЕ: это переключатель, а не set)."""
        await self._climate_post(ACTION_CLIMATE_ON_OFF, device_id)

    async def set_climate_mode(self, device_id: str, mode: int) -> None:
        """Выбрать режим: 0 — постоянная t°, 3 — 7/7, 4 — 5/2, 5 — 4/1/2."""
        await self._climate_post(ACTION_CLIMATE_MODE, device_id, int(mode))

    async def set_climate_value(
        self, device_id: str, key: str, value: int | float
    ) -> None:
        """Записать значение: key = "temp_dt" или ключ расписания ("m3_P1_temp")."""
        await self._climate_post(ACTION_CLIMATE_RANGE, device_id, key, value)

    async def select_climate_sensor(self, device_id: str, sensor_id: str) -> None:
        """Выбрать датчик температуры помещения для климат-контроля."""
        await self._climate_post(ACTION_CLIMATE_SELECT_SENSOR, device_id, sensor_id)

    async def get_climate_sensors(self, device_id: str) -> list[dict]:
        """Список датчиков для выбора: [{"id_name": ..., "name": ...}, ...]."""
        data = await self._climate_post(ACTION_CLIMATE_GET_SENSORS, device_id)
        sensors = data.get("sensors") if isinstance(data, dict) else None
        if not isinstance(sensors, list):
            return []
        return [s for s in sensors if isinstance(s, dict) and s.get("id_name")]

    async def close(self) -> None:
        """Закрыть сессию, если клиент создал её сам."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()
