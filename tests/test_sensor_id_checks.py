"""Тесты находок 46 и 47: проверка идентификатора датчика.

46 — ответ add_device принимается как идентификатор, только если это цифры.
47 — датчик из профиля (в том числе импортированного из другого дома) не
отправляется на сервер, пока не найден среди датчиков этого дома.
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

if getattr(ha_stubs, "STUBS_VERSION", 0) < 4:
    raise RuntimeError(
        "Устаревший tests/ha_stubs.py (нужна версия заглушек 4 или новее): "
        "скопируйте ha_stubs.py из последнего релиза рядом с тестами.")

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "kotel"
PKG = "kotel_under_test"
if PKG not in sys.modules:
    ha_stubs.load_integration(COMPONENT, PKG)

api_mod = importlib.import_module(f"{PKG}.api")
const = importlib.import_module(f"{PKG}.const")
profiles_mod = importlib.import_module(f"{PKG}.profiles")
config_flow_mod = importlib.import_module(f"{PKG}.config_flow")

KotelApiError = api_mod.KotelApiError
KotelAuthError = api_mod.KotelAuthError
KotelResponseError = api_mod.KotelResponseError

OWN = "3631427318216"          # датчик, выбранный сейчас
SECOND = "3631427318217"       # другой датчик этого дома (есть в get_all_dev)
ONLY_IN_LIST = "3631427318218"  # датчик этого дома, известный только из get_climat_sensors
FOREIGN = "9999999999999"      # датчик чужого дома


class DeviceIdTests(unittest.TestCase):
    def test_valid_and_invalid(self):
        valid = api_mod.is_valid_device_id
        for good in ("3631427318216", "12345678", "1" * 20):
            self.assertTrue(valid(good), good)
        for bad in ("", "error", "0", "1234567", "1" * 21, "3631427318216 ", "36314 27318216",
                    "login_pass_in_db_ok", "../../x", "36314273182a6", None, 3631427318216, True):
            self.assertFalse(valid(bad), repr(bad))


def make_client():
    return api_mod.KotelApiClient("https://app.reglerpro.ru", "user", "pass", "H1", session=object())


class AddSensorResponseTests(unittest.IsolatedAsyncioTestCase):
    async def add(self, answer):
        client = make_client()

        async def fake_post(payload, **kwargs):
            return answer

        client._post = fake_post
        return await client.add_temperature_sensor()

    async def test_id_as_string_or_number(self):
        self.assertEqual(await self.add("3631427318216"), "3631427318216")
        self.assertEqual(await self.add(3631427318216), "3631427318216")
        self.assertEqual(await self.add(" 3631427318216 \n"), "3631427318216")

    async def test_anything_else_is_a_response_error(self):
        for answer in ("error", "0", "", "Wrong home", 0, 12345, True, None, 3.14,
                       {"error": "x"}, ["3631427318216"], "36314273182 16"):
            with self.assertRaises(KotelResponseError, msg=repr(answer)) as ctx:
                await self.add(answer)
            self.assertIn("идентификатор датчика", str(ctx.exception))
            self.assertTrue(ctx.exception.snippet)

    async def test_error_text_is_not_shown_as_an_id(self):
        with self.assertRaises(KotelResponseError) as ctx:
            await self.add("Нет прав на добавление")
        self.assertIn("Нет прав", ctx.exception.snippet)       # виден как ответ сервера, не как ID

    async def test_home_id_is_still_required(self):
        client = api_mod.KotelApiClient("https://app.reglerpro.ru", "u", "p", "", session=object())
        with self.assertRaises(KotelApiError):
            await client.add_temperature_sensor()


class FakeAddApi:
    def __init__(self, result):
        self.result = result

    async def add_temperature_sensor(self, home_id):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class AddSensorFlowTests(unittest.IsolatedAsyncioTestCase):
    def make_flow(self, result):
        flow = config_flow_mod.KotelOptionsFlow()
        flow.config_entry = SimpleNamespace(entry_id="E1", data={"home_id": "H1"}, options={})
        flow.hass = SimpleNamespace(data={const.DOMAIN: {"E1": {"api": FakeAddApi(result)}}})
        flow.async_show_form = lambda **kw: {"type": "form", **kw}
        flow.async_create_entry = lambda **kw: {"type": "create", **kw}
        flow.async_show_menu = lambda **kw: {"type": "menu", **kw}
        return flow

    async def test_bad_response_shows_error_not_an_id(self):
        flow = self.make_flow(KotelResponseError("Некорректный ответ add_device: str: 'error'", "str: 'error'"))
        result = await flow.async_step_add_temperature_sensor({"confirm": True})
        self.assertEqual(result["step_id"], "add_temperature_sensor")
        self.assertEqual(result["errors"], {"base": "add_sensor_failed"})
        self.assertIn("Некорректный ответ add_device", result["description_placeholders"]["error"])

    async def test_valid_id_goes_to_result_step(self):
        flow = self.make_flow("3631427318216")
        result = await flow.async_step_add_temperature_sensor({"confirm": True})
        self.assertEqual(result["step_id"], "add_temperature_sensor_result")


class FakeSensorApi:
    def __init__(self, listed=None, list_error=None):
        self.listed, self.list_error = listed or [], list_error
        self.selected, self.values, self.modes, self.list_calls = [], [], [], 0

    async def get_climate_sensors(self, dev_id):
        self.list_calls += 1
        if self.list_error:
            raise self.list_error
        return [{"id_name": sensor_id, "name": "d"} for sensor_id in self.listed]

    async def select_climate_sensor(self, dev_id, sensor_id):
        self.selected.append(sensor_id)

    async def set_climate_value(self, dev_id, key, value):
        self.values.append((key, value))

    async def set_climate_mode(self, dev_id, mode):
        self.modes.append(mode)


class SensorProfileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._delay = profiles_mod.WRITE_DELAY
        profiles_mod.WRITE_DELAY = 0

    def tearDown(self):
        profiles_mod.WRITE_DELAY = self._delay

    def make(self, api=None, dt_devices=(OWN, SECOND), current=OWN):
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
        data = {"c1": {"type": "contr", "name": "Котёл", "mqtt_online": "1", "detail_ok": True,
                       "params": {}, "climate": {"sensor_id": current, "enabled": True, "mode": 0,
                                                 "target_const": 23, "target_temp": 23}}}
        for sensor_id in dt_devices:
            data[sensor_id] = {"type": "dt", "name": f"Датчик {sensor_id[-1]}"}
        coord = SimpleNamespace(data=data, last_update_success=True)

        async def refresh():
            return None

        coord.async_refresh = refresh
        api = api or FakeSensorApi()
        manager.register_entry("e1", coord, api)
        return manager, api

    async def apply(self, manager, sensor_id, *, dry_run=False, extra=None, scopes=("climate_sensor",)):
        values = {"climate_sensor": {"tstatDtId": sensor_id}, **(extra or {})}
        return await manager._apply_values(
            "c1", values, list(scopes), label="тест", kind="climate",
            dry_run=dry_run, force=False, verify=False, backup=False)

    async def test_foreign_sensor_is_not_sent(self):
        manager, api = self.make()
        report = await self.apply(manager, FOREIGN)
        self.assertEqual(api.selected, [])
        self.assertEqual(report["applied"], 0)
        self.assertFalse(report["ok"])
        self.assertEqual(report["failed"], [{"scope": "climate_sensor", "key": "tstatDtId",
                                              "error": profiles_mod.SENSOR_NOT_FOUND}])

    async def test_other_sensor_of_this_home_is_applied(self):
        manager, api = self.make()
        report = await self.apply(manager, SECOND)
        self.assertEqual(api.selected, [SECOND])
        self.assertTrue(report["ok"])
        self.assertEqual(report["applied"], 1)

    async def test_sensor_known_only_from_server_list_is_applied(self):
        manager, api = self.make(FakeSensorApi(listed=[ONLY_IN_LIST]))
        report = await self.apply(manager, ONLY_IN_LIST)
        self.assertEqual(api.selected, [ONLY_IN_LIST])
        self.assertTrue(report["ok"])

    async def test_server_list_failure_falls_back_to_device_list(self):
        api = FakeSensorApi(list_error=KotelApiError("HTTP 500"))
        manager, _ = self.make(api)
        self.assertTrue((await self.apply(manager, SECOND))["ok"])
        self.assertFalse((await self.apply(manager, FOREIGN))["ok"])
        self.assertEqual(api.selected, [SECOND])

    async def test_unchanged_sensor_needs_no_check(self):
        manager, api = self.make()
        report = await self.apply(manager, OWN)
        self.assertEqual((report["unchanged"], report["applied"], report["ok"]), (1, 0, True))
        self.assertEqual((api.selected, api.list_calls), ([], 0))

    async def test_no_known_sensors_at_all_means_refusal(self):
        manager, api = self.make(dt_devices=(), current="")
        report = await self.apply(manager, SECOND)
        self.assertEqual(api.selected, [])
        self.assertFalse(report["ok"])

    async def test_dry_run_reports_skipped_without_failure(self):
        manager, api = self.make()
        report = await self.apply(manager, FOREIGN, dry_run=True)
        self.assertEqual(api.selected, [])
        self.assertEqual(report["failed"], [])
        self.assertTrue(report["ok"])
        self.assertEqual(report["skipped"][-1]["reason"], profiles_mod.SENSOR_NOT_FOUND)
        self.assertEqual(report["changes"], [])

    async def test_other_values_are_still_applied(self):
        manager, api = self.make()
        report = await self.apply(
            manager, FOREIGN, scopes=("climate_sensor", "climate"),
            extra={"climate": {"tstatTemp": 22}})
        self.assertEqual(api.selected, [])
        self.assertEqual(api.values, [("temp_dt", 22)])
        self.assertEqual(report["applied"], 1)
        self.assertFalse(report["ok"])                      # профиль применён не полностью

    async def test_badly_formed_id_is_skipped_by_validation(self):
        manager, api = self.make()
        report = await self.apply(manager, "../../x")
        self.assertEqual(api.selected, [])
        self.assertEqual(report["skipped"][0]["reason"], "недопустимый идентификатор датчика")


class ProfileValidationTests(unittest.TestCase):
    def test_validate_value(self):
        validate = profiles_mod.validate_value
        self.assertIsNone(validate("climate_sensor", "tstatDtId", OWN))
        for bad in ("", "x", "error", 3631427318216, None):
            self.assertIsNotNone(validate("climate_sensor", "tstatDtId", bad), repr(bad))
        self.assertIsNotNone(validate("climate_sensor", "other", OWN))

    def test_clean_values_keeps_good_ids_untouched(self):
        cleaned = profiles_mod.clean_values({"climate_sensor": {"tstatDtId": OWN}})
        self.assertEqual(cleaned["climate_sensor"]["tstatDtId"], OWN)

    def test_profile_with_only_a_malformed_id_is_rejected(self):
        for bad in ("../../x", "login_pass_in_db_ok - NO", "x" * 100, ""):
            with self.assertRaises(ValueError, msg=bad):
                profiles_mod.clean_values({"climate_sensor": {"tstatDtId": bad}})

    def test_malformed_id_is_dropped_next_to_valid_values(self):
        cleaned = profiles_mod.clean_values({
            "climate_sensor": {"tstatDtId": "../../x"},
            "climate": {"tstatTemp": 22},
        })
        self.assertNotIn("tstatDtId", cleaned.get("climate_sensor", {}))
        self.assertEqual(cleaned["climate"]["tstatTemp"], 22)


if __name__ == "__main__":
    unittest.main()
