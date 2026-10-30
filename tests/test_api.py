"""Unit-тесты API без внешних зависимостей и запущенного Home Assistant."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_API_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "kotel"
    / "api.py"
)
_AIOHTTP_STUB = types.ModuleType("aiohttp")
_AIOHTTP_STUB.ClientSession = object
_PACKAGE_NAME = "kotel_api_test"
_PACKAGE = types.ModuleType(_PACKAGE_NAME)
_PACKAGE.__path__ = [str(_API_PATH.parent)]
sys.modules[_PACKAGE_NAME] = _PACKAGE
with patch.dict(sys.modules, {"aiohttp": _AIOHTTP_STUB}):
    api = importlib.import_module(f"{_PACKAGE_NAME}.api")


class AddTemperatureSensorTest(unittest.IsolatedAsyncioTestCase):
    async def test_requests_and_returns_sensor_id(self):
        client = api.KotelApiClient(
            base_url="https://example.test",
            login="test-login",
            password="test-password",
            home_id="home-id-456",
        )
        client._post = AsyncMock(return_value=" sensor-id-123 ")

        sensor_id = await client.add_temperature_sensor()

        self.assertEqual(sensor_id, "sensor-id-123")
        client._post.assert_awaited_once_with({
            "climat_war1": "",
            "action": "add_device",
            "range": 0,
            "userId": "",
            "id_name": "",
            "login": "test-login",
            "password": "test-password",
            "action_value1": "home-id-456",
            "action_value2": "",
        })

    async def test_normalizes_numeric_sensor_id(self):
        client = api.KotelApiClient(
            base_url="https://example.test",
            login="test-login",
            password="test-password",
            home_id="home-id-456",
        )
        client._post = AsyncMock(return_value=123456)

        self.assertEqual(await client.add_temperature_sensor(), "123456")

    async def test_rejects_missing_home_id_without_request(self):
        client = api.KotelApiClient(
            base_url="https://example.test",
            login="test-login",
            password="test-password",
        )
        client._post = AsyncMock()

        with self.assertRaises(api.KotelApiError):
            await client.add_temperature_sensor()

        client._post.assert_not_awaited()

    async def test_rejects_unexpected_or_empty_response(self):
        for response in ({}, [], "  ", True):
            with self.subTest(response=response):
                client = api.KotelApiClient(
                    base_url="https://example.test",
                    login="test-login",
                    password="test-password",
                )
                client._post = AsyncMock(return_value=response)

                with self.assertRaises(api.KotelApiError):
                    await client.add_temperature_sensor("home-id-456")


if __name__ == "__main__":
    unittest.main()
