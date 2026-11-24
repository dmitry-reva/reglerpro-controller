"""Тесты находок 79 и 80: параметры, которых нет у контроллера, недоступны; servo_cooler и korect_dt.

Правила показа взяты из приложения ReglerPro (functions.js: page_device_settings,
page контроллера, params_for_range).
"""

from __future__ import annotations

import importlib
import json
import sys
import unittest
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


def mod(name: str):
    return importlib.import_module(f"{PKG}.{name}")


number_mod = mod("number")
coordinator_mod = mod("coordinator")
params_parser = mod("params_parser")
profiles_mod = mod("profiles")
api_mod = mod("api")
HomeAssistantError = sys.modules["homeassistant.exceptions"].HomeAssistantError

# настоящий ответ контроллера «авто» (vers 291023): servo_cooler = 0, korect_20 = 20
AUTO_ARRAY = "[47,0,0,45,1,291023,0,0,0,0,20,20,0,45,0,35,0,24,60,600,20,60,1,250,0,1,6,1,0,0]"
AUTO = params_parser.get_params(AUTO_ARRAY)
NON_AUTO = {**AUTO, "vers": 201742}

ALWAYS = {"temp_w_ust", "temp_w_min", "nasos_t", "produv_time", "produv_interval", "vent_max",
          "shn_rev_auto_i"}
AUGER = {"shn_roz_vkl", "shn_roz", "shn_uga_vkl", "shn_uga"}


class SupportRulesTests(unittest.TestCase):
    def supported(self, params):
        return {row[0] for row in number_mod.NUMBER_PARAMS
                if number_mod.param_supported(row[0], params)}

    def test_auto_boiler(self):
        self.assertEqual(self.supported(AUTO),
                         ALWAYS | AUGER | {"korect_", "time_ugas"})     # без vent_min и servo_cooler

    def test_non_auto_boiler(self):
        self.assertEqual(self.supported(NON_AUTO), ALWAYS | {"vent_min", "korect_", "time_ugas"})

    def test_servo_cooler_only_when_non_zero(self):
        self.assertFalse(number_mod.param_supported("servo_cooler", AUTO))              # 0
        self.assertFalse(number_mod.param_supported("servo_cooler", {**AUTO, "servo_cooler": None}))
        self.assertTrue(number_mod.param_supported("servo_cooler", {**AUTO, "servo_cooler": 55}))

    def test_korect_and_ugas_need_korect_20(self):
        without = {k: v for k, v in AUTO.items() if k != "korect_20"}
        for params in (without, {**AUTO, "korect_20": 0}, {**AUTO, "korect_20": None}):
            self.assertFalse(number_mod.param_supported("korect_", params), params.get("korect_20"))
            self.assertFalse(number_mod.param_supported("time_ugas", params))
        self.assertTrue(number_mod.param_supported("korect_", {**AUTO, "korect_20": 27}))

    def test_missing_value_is_unsupported(self):
        for key in ("temp_w_ust", "vent_max", "nasos_t"):
            self.assertFalse(number_mod.param_supported(key, {k: v for k, v in AUTO.items() if k != key}))
        self.assertFalse(number_mod.param_supported("temp_w_ust", {}))

    def test_unknown_version_is_not_auto(self):
        params = {k: v for k, v in AUTO.items() if k != "vers"}
        self.assertTrue(number_mod.param_supported("vent_min", params))
        self.assertFalse(number_mod.param_supported("shn_roz", params))

    def test_marker_format_without_korect_20(self):
        # строковый формат: korect_20 может отсутствовать (в приложении поля скрыты)
        params = {"vers": 291500, "temp_w_ust": 60, "time_ugas": 0, "korect_": None}
        self.assertFalse(number_mod.param_supported("time_ugas", params))


def make_coordinator(params, success=True):
    coord = SimpleNamespace(
        data={"c1": {"type": "contr", "name": "К", "params": params}},
        last_update_success=success, api=None, refreshes=0, hass=SimpleNamespace(data={}))

    async def refresh():
        coord.refreshes += 1

    coord.async_request_refresh = refresh
    return coord


def entity_for(coord, key):
    row = next(r for r in number_mod.NUMBER_PARAMS if r[0] == key)
    return number_mod.KotelNumberEntity(coord, "c1", *row)


class EntityAvailabilityTests(unittest.TestCase):
    def available(self, params, success=True):
        coord = make_coordinator(params, success)
        return {row[0] for row in number_mod.NUMBER_PARAMS if entity_for(coord, row[0]).available}

    def test_available_follows_the_app(self):
        self.assertEqual(self.available(AUTO), ALWAYS | AUGER | {"korect_", "time_ugas"})
        self.assertEqual(self.available(NON_AUTO), ALWAYS | {"vent_min", "korect_", "time_ugas"})

    def test_nothing_is_available_after_a_failed_update(self):
        self.assertEqual(self.available(AUTO, success=False), set())

    def test_hidden_parameter_keeps_its_value_for_automations(self):
        entity = entity_for(make_coordinator(AUTO), "vent_min")
        self.assertFalse(entity.available)
        self.assertEqual(entity.native_value, AUTO["vent_min"])

    def test_schedule_numbers_are_not_filtered_by_boiler_rules(self):
        coord = make_coordinator({})                 # параметров котла нет вообще
        coord.data["c1"]["climate"] = {"schedule": {"m3_P1_time": 0, "m3_P1_temp": 22}}
        for kind in ("time", "temp"):
            entity = number_mod.KotelScheduleNumber(coord, "c1", "m3_P{n}_{kind}", 1, kind)
            self.assertTrue(entity.available, kind)


class FakeApi:
    def __init__(self):
        self.calls = []
        self.error = None

    async def send_command(self, action, dev_id, value=0):
        if self.error:
            raise self.error
        self.calls.append((action, dev_id, value))
        return {}


class ServoCoolerTests(unittest.IsolatedAsyncioTestCase):
    def test_definition(self):
        row = next(r for r in number_mod.NUMBER_PARAMS if r[0] == "servo_cooler")
        self.assertEqual((row[1], row[2], row[3], row[4]), ("fan_damper", 1, 99, 1))   # 1 % … 99 %

    async def test_write_goes_through_coordinator_api(self):
        api = FakeApi()
        coord = make_coordinator({**AUTO, "servo_cooler": 55})
        coord.api = api
        entity = entity_for(coord, "servo_cooler")
        self.assertTrue(entity.available)
        self.assertEqual(entity.native_value, 55)
        await entity.async_set_native_value(60)
        self.assertEqual(api.calls, [("servo_cooler", "c1", 60)])
        self.assertEqual(coord.refreshes, 1)

    def test_profiles_accept_the_damper_with_the_app_limits(self):
        validate = profiles_mod.validate_value
        self.assertIsNone(validate("boiler", "servo_cooler", 1))
        self.assertIsNone(validate("boiler", "servo_cooler", 99))
        self.assertIsNotNone(validate("boiler", "servo_cooler", 0))
        self.assertIsNotNone(validate("boiler", "servo_cooler", 100))


class SensorCorrectionTests(unittest.IsolatedAsyncioTestCase):
    def make(self, korect_dt, success=True):
        coord = SimpleNamespace(
            data={"d1": {"type": "dt", "name": "Холл", "temp": 24.5, "time": 10, "korect_dt": korect_dt}},
            last_update_success=success, api=FakeApi(), refreshes=0, hass=SimpleNamespace(data={}))

        async def refresh():
            coord.refreshes += 1

        coord.async_request_refresh = refresh
        return coord, number_mod.KotelSensorCorrectionNumber(coord, "d1")

    def test_definition_matches_the_app(self):
        cls = number_mod.KotelSensorCorrectionNumber
        self.assertEqual((cls._attr_native_min_value, cls._attr_native_max_value, cls._attr_native_step),
                         (-15, 15, 0.5))

    def test_value_and_availability(self):
        _, entity = self.make(1.5)
        self.assertEqual((entity.native_value, entity.available), (1.5, True))
        self.assertEqual(self.make(0)[1].native_value, 0)
        self.assertTrue(self.make(0)[1].available)
        for missing in (None,):
            self.assertFalse(self.make(missing)[1].available)
        self.assertFalse(self.make(1.5, success=False)[1].available)

    async def test_write_sends_korect_dt_to_the_sensor(self):
        coord, entity = self.make(0)
        await entity.async_set_native_value(1.5)
        await entity.async_set_native_value(-2.0)
        self.assertEqual(coord.api.calls, [("korect_dt", "d1", 1.5), ("korect_dt", "d1", -2)])
        self.assertIsInstance(coord.api.calls[1][2], int)          # 2.0 -> 2, как у остальных чисел
        self.assertEqual(coord.refreshes, 2)

    async def test_api_error_becomes_a_home_assistant_error(self):
        coord, entity = self.make(0)
        coord.api.error = api_mod.KotelApiError("HTTP 500")
        with self.assertRaises(HomeAssistantError):
            await entity.async_set_native_value(1)
        self.assertEqual(coord.refreshes, 0)

    def test_unique_id_is_per_sensor(self):
        _, entity = self.make(0)
        self.assertEqual(entity._attr_unique_id, "kotel_d1_korect_dt_set")


class FakeListApi:
    def __init__(self, devices):
        self.devices = devices

    async def get_all_devices(self):
        return self.devices

    async def get_controller(self, dev_id):
        return next(d for d in self.devices if d.get("id_name") == dev_id)


class CoordinatorStoresCorrectionTests(unittest.IsolatedAsyncioTestCase):
    async def correction(self, **extra):
        dt = {"type": "dt", "id_name": "2000000000002", "name": "Холл", "temp": "24.5", "time": 172, **extra}
        coord = coordinator_mod.KotelCoordinator(None, FakeListApi([dt]))
        return (await coord._async_update_data())["2000000000002"]["korect_dt"]

    async def test_string_values(self):
        self.assertEqual(await self.correction(korect_dt="0"), 0)          # как в настоящем ответе
        self.assertEqual(await self.correction(korect_dt="-2,5"), -2.5)
        self.assertEqual(await self.correction(korect_dt=1.5), 1.5)

    async def test_missing_value(self):
        self.assertIsNone(await self.correction())
        self.assertIsNone(await self.correction(korect_dt=""))


class StringsTests(unittest.TestCase):
    def setUp(self):
        self.strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))

    def test_every_number_has_a_translation(self):
        numbers = self.strings["entity"]["number"]
        for row in number_mod.NUMBER_PARAMS:
            self.assertIn(row[1], numbers, row[0])
        self.assertIn("sensor_correction", numbers)
        self.assertEqual(len(numbers), 15 + 48 + 1)       # параметры котла + расписание + коррекция

    def test_additions_cover_the_new_names(self):
        root = COMPONENT.parent.parent / "translations_additions"
        if not root.is_dir():
            self.skipTest("translations_additions уже слит с переводами и удалён")
        for lang in ("en", "ru"):
            data = json.loads((root / f"{lang}.json").read_text(encoding="utf-8"))
            numbers = data["entity"]["number"]
            self.assertIn("fan_damper", numbers, lang)
            self.assertIn("sensor_correction", numbers, lang)


if __name__ == "__main__":
    unittest.main()
