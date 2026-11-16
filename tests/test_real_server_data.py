"""Тесты на реальных данных сервера и диагностика без идентификаторов.

Данные — настоящий ответ get_all_dev (идентификаторы заменены, графики убраны).
"""

from __future__ import annotations

import importlib
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ha_stubs  # noqa: E402

if getattr(ha_stubs, "STUBS_VERSION", 0) < 4:
    raise RuntimeError(
        "Устаревший tests/ha_stubs.py (нужна версия заглушек 4 или новее): "
        "скопируйте ha_stubs.py из последнего релиза рядом с тестами.")

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "kotel"
PKG = "kotel_under_test"
if PKG not in sys.modules:
    ha_stubs.load_integration(COMPONENT, PKG)

coordinator_mod = importlib.import_module(f"{PKG}.coordinator")
diagnostics_mod = importlib.import_module(f"{PKG}.diagnostics")
params_parser = importlib.import_module(f"{PKG}.params_parser")
const = importlib.import_module(f"{PKG}.const")

# Настоящий ответ get_all_dev (идентификаторы заменены, графики убраны). Данные
# встроены в файл, чтобы тест не зависел от других файлов.
SAMPLE = json.loads(r'''
[
 {
  "type": "contr",
  "id_name": "1000000000001",
  "name": "Дом",
  "params": "[47,0,0,45,1,291023,0,0,0,0,20,20,0,45,0,35,0,24,60,600,20,60,1,250,0,1,6,1,0,0]",
  "mqtt_online": "1",
  "mqtt_offline_time": 15637,
  "vers": "423",
  "tstatArr": "{\"tstatDtId\":\"2000000000002\",\"tstatOn\":1,\"tstatTemp\":23.5,\"m3_P1_time\":0,\"m3_P2_time\":5,\"m3_P3_time\":9,\"m3_P4_time\":18,\"m3_P1_temp\":23,\"m3_P2_temp\":24.5,\"m3_P3_temp\":23.5,\"m3_P4_temp\":24,\"tstat_mode\":4,\"m5_P1_d4_time\":10,\"m4_P1_d5_time\":0,\"m4_P1_d5_temp\":23,\"m4_P2_d5_time\":5,\"m4_P2_d5_temp\":24.5,\"m4_P3_d5_time\":9,\"m4_P3_d5_temp\":23.5,\"m4_P4_d5_time\":18,\"m4_P4_d5_temp\":24,\"m4_P1_d2_time\":0,\"m4_P1_d2_temp\":23,\"m4_P2_d2_time\":5,\"m4_P2_d2_temp\":25,\"m4_P3_d2_time\":8,\"m4_P3_d2_temp\":24.5,\"m4_P4_d2_time\":21,\"m4_P4_d2_temp\":24}",
  "tempUstNowTstate": {
   "period": 1,
   "textPeriodOn": "📅 - По расписанию 5/2\n",
   "temp": 24.5
  },
  "temp_dt_ust": 24.5,
  "textPeriodOn": "📅 - По расписанию 5/2\n",
  "sensor_name": "Холл",
  "sensor_temp": "24.5"
 },
 {
  "type": "dt",
  "id_name": "2000000000002",
  "name": "Холл",
  "temp": "24.5",
  "vers": "332",
  "time": 172,
  "korect_dt": "0"
 },
 {
  "time_for_graf": "[\"12:40\",\"13:00\"]"
 }
]
''')
CONTR_ID, DT_ID = "1000000000001", "2000000000002"


class FakeApi:
    def __init__(self, devices):
        self.devices = devices
        self.servers = ("https://app.reglerpro.ru", "https://web.reglerpro.ru")
        self.active_url = self.servers[0]

    async def get_all_devices(self):
        return self.devices

    async def get_controller(self, dev_id):
        return next(dev for dev in self.devices if dev.get("id_name") == dev_id)


async def load(devices=None):
    coord = coordinator_mod.KotelCoordinator(None, FakeApi(devices or SAMPLE))
    return coord, await coord._async_update_data()


class RealResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_devices_and_ignored_item(self):
        _, data = await load()
        self.assertEqual(set(data), {CONTR_ID, DT_ID})        # элемент без id пропущен
        self.assertEqual((data[CONTR_ID]["type"], data[DT_ID]["type"]), ("contr", "dt"))
        self.assertTrue(data[CONTR_ID]["detail_ok"])
        self.assertEqual(data[CONTR_ID]["mqtt_online"], "1")

    async def test_params_from_json_array(self):
        _, data = await load()
        params = data[CONTR_ID]["params"]
        expected = {"temp_w_ust": 47, "temp_w": 45, "sost_rab": 1, "vers": 291023,
                    "produv_time": 20, "produv_interval": 20, "vent_max": 45,
                    "temp_w_min": 35, "korect_": 0, "tstatNow": 1,
                    "shn_roz_vkl": 6, "shn_uga_vkl": 1, "time_ugas": 60}
        for key, value in expected.items():
            self.assertEqual(params.get(key), value, key)
        self.assertTrue(params_parser.is_kotel_auto(params["vers"]))

    async def test_climate(self):
        _, data = await load()
        climate = data[CONTR_ID]["climate"]
        self.assertTrue(climate["enabled"])
        self.assertTrue(climate["active"])
        self.assertEqual(climate["mode"], const.TSTAT_MODE_5_2)
        self.assertEqual(climate["sensor_id"], DT_ID)
        self.assertEqual(climate["sensor_name"], "Холл")
        self.assertEqual(climate["target_const"], 23.5)
        self.assertEqual(climate["target_temp"], 24.5)
        self.assertEqual(climate["period"], 1)
        self.assertIn("5/2", climate["period_text"])
        self.assertEqual(climate["sensor_temp"], 24.5)

    async def test_schedule_keys(self):
        _, data = await load()
        schedule = data[CONTR_ID]["climate"]["schedule"]
        self.assertEqual(len(schedule), 25)
        self.assertEqual(schedule["m4_P2_d2_temp"], 25)
        self.assertEqual(schedule["m5_P1_d4_time"], 10)
        self.assertNotIn("tstatOn", schedule)

    async def test_sensor(self):
        _, data = await load()
        self.assertEqual(data[DT_ID]["temp"], "24.5")
        self.assertEqual(data[DT_ID]["time"], 172)
        self.assertEqual(data[DT_ID]["name"], "Холл")


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    SECRETS = ("s3cret!", "dmrlogin", "H123456", CONTR_ID, DT_ID)

    async def result(self, with_runtime=True):
        coord, data = await load()
        coord.data = data
        coord.last_update_success = True
        coord.last_exception = Exception(f"bad login dmrlogin, device {CONTR_ID}")
        coord.update_interval = timedelta(seconds=30)
        entry = SimpleNamespace(
            entry_id="E1", title="ReglerPro (H123456)", version=1,
            data={"url": "https://app.reglerpro.ru", "login": "dmrlogin",
                  "password": "s3cret!", "home_id": "H123456"},
            options={"scan_interval": 30})
        runtime = {"coordinator": coord, "api": coord._api} if with_runtime else {}
        hass = SimpleNamespace(data={const.DOMAIN: {"E1": runtime}})
        return await diagnostics_mod.async_get_config_entry_diagnostics(hass, entry)

    async def test_no_secret_survives_anywhere(self):
        text = json.dumps(await self.result(), ensure_ascii=False)
        for secret in self.SECRETS:
            self.assertNotIn(secret, text, secret)

    async def test_labels_and_useful_content_remain(self):
        result = await self.result()
        text = json.dumps(result, ensure_ascii=False)
        for label in ("<home_id>", "<device-1>", "<device-2>", "<login>"):
            self.assertIn(label, text)
        self.assertEqual(result["entry"]["title"], "ReglerPro (<home_id>)")
        self.assertEqual(result["entry"]["data"]["url"], "https://app.reglerpro.ru")
        self.assertEqual(result["entry"]["data"]["password"], "***")
        self.assertEqual(result["entry"]["data"]["home_id"], "***")
        devices = result["coordinator"]["devices"]
        self.assertEqual(set(devices), {"<device-1>", "<device-2>"})
        controller = devices["<device-1>"]
        self.assertTrue(controller["detail_ok"])
        self.assertGreater(controller["params_count"], 10)
        self.assertEqual(controller["climate"]["sensor_id"], "<device-2>")   # связь сохранена
        self.assertEqual(controller["climate"]["mode"], 4)
        self.assertEqual(result["api"]["active_server"], "https://app.reglerpro.ru")
        self.assertEqual(len(result["api"]["servers"]), 2)

    async def test_works_without_runtime(self):
        result = await self.result(with_runtime=False)
        self.assertIsNone(result["api"]["servers"])
        self.assertEqual(result["coordinator"]["devices"], {})
        self.assertNotIn("s3cret!", json.dumps(result, ensure_ascii=False))

    def test_scrub_is_recursive_and_keeps_types(self):
        scrub = diagnostics_mod._scrub
        out = scrub({"a": [1, 2.5, None, True, "x ABCDEF y"], "ABCDEF": {"home_id": "z"}},
                    {"ABCDEF": "<id>"})
        self.assertEqual(out, {"a": [1, 2.5, None, True, "x <id> y"], "<id>": {"home_id": "***"}})

    def test_longer_secret_wins(self):
        coord = SimpleNamespace(data={"12345": {}, "1234567": {}})
        entry = SimpleNamespace(data={"login": "abc", "password": "", "home_id": ""})
        repl = diagnostics_mod._replacements(entry, coord)
        self.assertEqual(list(repl)[:2], ["1234567", "12345"])
        self.assertEqual(diagnostics_mod._scrub("1234567", repl), "<device-2>")


if __name__ == "__main__":
    unittest.main()
