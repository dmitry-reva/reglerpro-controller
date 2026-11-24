"""Тесты находки 55: климатические значения профиля не записываются при выключенном климате.

В приложении ReglerPro при выключенном климате доступна только кнопка «Включить»
(climate.js: page_climat_after_action), режим, температура, расписание и датчик скрыты.
"""

from __future__ import annotations

import importlib
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ha_stubs  # noqa: E402

if getattr(ha_stubs, "STUBS_VERSION", 0) < 5:
    raise RuntimeError(
        "Устаревший tests/ha_stubs.py (нужна версия заглушек 5 или новее): "
        "скопируйте ha_stubs.py из последнего релиза рядом с тестами.")

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "kotel"
PKG = "kotel_under_test"
if PKG not in sys.modules:
    ha_stubs.load_integration(COMPONENT, PKG)

profiles_mod = importlib.import_module(f"{PKG}.profiles")
button_mod = importlib.import_module(f"{PKG}.button")

OWN = "3631427318216"
SECOND = "3631427318217"


class FakeApi:
    def __init__(self):
        self.calls = []
        self.list_calls = 0

    async def send_command(self, action, dev_id, value=0):
        self.calls.append(("send_command", action, value))
        return {}

    async def set_climate_mode(self, dev_id, mode):
        self.calls.append(("mode", mode))

    async def set_climate_value(self, dev_id, key, value):
        self.calls.append(("value", key, value))

    async def select_climate_sensor(self, dev_id, sensor_id):
        self.calls.append(("sensor", sensor_id))

    async def get_climate_sensors(self, dev_id):
        self.list_calls += 1
        return []


class ClimateGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._delay = profiles_mod.WRITE_DELAY
        profiles_mod.WRITE_DELAY = 0

    def tearDown(self):
        profiles_mod.WRITE_DELAY = self._delay

    def make(self, enabled=True, detail_ok=True):
        dt_util = sys.modules["homeassistant.util.dt"]
        dt_util.utcnow = lambda: datetime.now(timezone.utc)

        class Store:
            def __init__(self, *a, **k):
                pass

            async def async_load(self):
                return None

            async def async_save(self, data):
                pass

        profiles_mod.Store = Store
        hass = SimpleNamespace(bus=SimpleNamespace(async_fire=lambda *a, **k: None))
        manager = profiles_mod.KotelProfileManager(hass)
        data = {
            "c1": {"type": "contr", "name": "К", "online": True, "mqtt_online": "1",
                   "detail_ok": detail_ok, "params": {"temp_w_ust": 60, "temp_w_min": 40},
                   "climate": {"enabled": enabled, "mode": 0, "target_const": 23, "target_temp": 23,
                               "sensor_id": OWN, "schedule": {"m3_P1_temp": 20}}},
            OWN: {"type": "dt", "name": "А"}, SECOND: {"type": "dt", "name": "Б"},
        }
        coord = SimpleNamespace(data=data, last_update_success=True)

        async def refresh():
            return None

        coord.async_refresh = refresh
        api = FakeApi()
        manager.register_entry("e1", coord, api)
        return manager, api

    VALUES = {
        "boiler": {"temp_w_ust": 55},
        "climate": {"tstat_mode": 3, "tstatTemp": 22},
        "schedule": {"m3_P1_temp": 23},
        "climate_sensor": {"tstatDtId": SECOND},
    }
    SCOPES = ["boiler", "climate", "schedule", "climate_sensor"]

    async def apply(self, manager, *, dry_run=False, force=False, values=None, scopes=None):
        return await manager._apply_values(
            "c1", values or self.VALUES, scopes or self.SCOPES, label="тест", kind="climate",
            dry_run=dry_run, force=force, verify=False, backup=False)

    async def test_climate_off_blocks_every_climate_value_but_not_boiler(self):
        manager, api = self.make(enabled=False)
        report = await self.apply(manager)
        self.assertEqual(api.calls, [("send_command", "temp_w_ust", 55)])      # только котёл
        self.assertEqual(report["applied"], 1)
        self.assertFalse(report["ok"])
        refused = {(p["scope"], p["key"]): p["error"] for p in report["failed"]}
        self.assertEqual(set(refused), {("climate", "tstat_mode"), ("climate", "tstatTemp"),
                                        ("schedule", "m3_P1_temp"), ("climate_sensor", "tstatDtId")})
        self.assertEqual(set(refused.values()), {profiles_mod.CLIMATE_OFF})

    async def test_mode_is_not_switched_halfway(self):
        manager, api = self.make(enabled=False)
        await self.apply(manager, values={"climate": {"tstat_mode": 3}}, scopes=["climate"])
        self.assertEqual(api.calls, [])

    async def test_no_network_lookup_for_a_refused_sensor(self):
        manager, api = self.make(enabled=False)
        await self.apply(manager, values={"climate_sensor": {"tstatDtId": SECOND}},
                         scopes=["climate_sensor"])
        self.assertEqual((api.calls, api.list_calls), ([], 0))

    async def test_dry_run_reports_skipped_not_failed(self):
        manager, api = self.make(enabled=False)
        report = await self.apply(manager, dry_run=True)
        self.assertEqual(api.calls, [])
        self.assertEqual(report["failed"], [])
        self.assertTrue(report["ok"])
        self.assertEqual({p["reason"] for p in report["skipped"]}, {profiles_mod.CLIMATE_OFF})
        self.assertEqual([c["scope"] for c in report["changes"]], ["boiler"])

    async def test_force_bypasses_the_check(self):
        manager, api = self.make(enabled=False)
        report = await self.apply(manager, force=True)
        self.assertTrue(report["ok"], report)
        self.assertIn(("mode", 3), api.calls)
        self.assertIn(("value", "temp_dt", 22), api.calls)
        self.assertIn(("value", "m3_P1_temp", 23), api.calls)
        self.assertIn(("sensor", SECOND), api.calls)

    async def test_missing_detail_data_is_not_reported_as_switched_off(self):
        manager, api = self.make(enabled=False, detail_ok=False)     # из списка климат выглядит «выключенным»
        report = await self.apply(manager, values={"climate": {"tstat_mode": 3}}, scopes=["climate"])
        self.assertEqual(api.calls, [])
        self.assertEqual({p["error"] for p in report["failed"]}, {profiles_mod.CLIMATE_NO_DATA})

    async def test_climate_on_works_as_before(self):
        manager, api = self.make(enabled=True)
        report = await self.apply(manager)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["applied"], 5)
        self.assertEqual(report["failed"], [])
        self.assertIn(("mode", 3), api.calls)

    async def test_boiler_only_profile_ignores_the_climate_state(self):
        manager, api = self.make(enabled=False)
        report = await manager._apply_values(
            "c1", {"boiler": {"temp_w_ust": 55}}, ["boiler"], label="т", kind="boiler",
            dry_run=False, force=False, verify=False, backup=False)
        self.assertTrue(report["ok"])
        self.assertEqual(api.calls, [("send_command", "temp_w_ust", 55)])

    async def test_button_message_names_the_reason(self):
        manager, _ = self.make(enabled=False)
        report = await self.apply(manager, values={"climate": {"tstat_mode": 3}}, scopes=["climate"])
        text = button_mod._summary(report)
        self.assertIn("climate.tstat_mode", text)
        self.assertIn("климат-контроль выключен", text)

    def test_block_reason_helper(self):
        reason = profiles_mod._climate_block_reason
        self.assertIsNone(reason({"climate": {"enabled": True}}))
        self.assertEqual(reason({"climate": {"enabled": False}}), profiles_mod.CLIMATE_OFF)
        self.assertEqual(reason({"climate": {}}), profiles_mod.CLIMATE_OFF)
        self.assertEqual(reason({"detail_ok": False, "climate": {"enabled": True}}),
                         profiles_mod.CLIMATE_NO_DATA)
        self.assertIsNone(reason({"climate": {"enabled": True}}))       # detail_ok по умолчанию True

    def test_climate_scopes(self):
        self.assertEqual(set(profiles_mod.CLIMATE_SCOPES), {"climate", "schedule", "climate_sensor"})


if __name__ == "__main__":
    unittest.main()
