"""Сверка с приложением ReglerPro (functions.js v1.1 от 16.02.2026) и повторная проверка записи.

Значения в таблицах ниже взяты из functions.js: params_for_range(), onlineOffline(),
fetchPostWithSafeRetry(), check_save_range().
"""

from __future__ import annotations

import asyncio
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


def mod(name: str):
    return importlib.import_module(f"{PKG}.{name}")


const = mod("const")
api_mod = mod("api")
coordinator_mod = mod("coordinator")
number_mod = mod("number")
profiles_mod = mod("profiles")
button_mod = mod("button")
KotelApiError = api_mod.KotelApiError

# params_for_range() из functions.js: параметр -> (минимум, максимум)
JS_RANGES = {
    "temp_w_ust": (10, 90), "temp_w_min": (10, 50), "nasos_t": (0, 80), "korect_": (-7, 7),
    "produv_time": (1, 90), "produv_interval": (1, 90), "vent_min": (5, 100),
    "vent_max": (5, 100), "time_ugas": (10, 90), "shn_roz_vkl": (1, 600), "shn_roz": (1, 600),
    "shn_uga_vkl": (1, 600), "shn_uga": (1, 900),
}


class RangesMatchTheApp(unittest.TestCase):
    def test_boiler_number_ranges(self):
        ours = {row[0]: (row[2], row[3]) for row in number_mod.NUMBER_PARAMS}
        for key, expected in JS_RANGES.items():
            self.assertEqual(ours.get(key), expected, key)

    def test_climate_range(self):
        self.assertEqual((const.CLIMATE_TEMP_MIN, const.CLIMATE_TEMP_MAX, const.CLIMATE_TEMP_STEP),
                         (5.0, 40.0, 0.5))               # temp_dt: 5..40, шаг 0,5

    def test_profiles_accept_values_up_to_the_app_limit(self):
        for key in ("shn_roz_vkl", "shn_uga_vkl"):
            self.assertIsNone(profiles_mod.validate_value("boiler", key, 600), key)
            self.assertIsNotNone(profiles_mod.validate_value("boiler", key, 601), key)


class OnlineRulesMatchTheApp(unittest.TestCase):
    def test_sensor_threshold(self):
        self.assertEqual(const.DT_STALE_SECONDS, 1800)            # dev.time < 1800

    def test_controller_rule(self):
        online = coordinator_mod.controller_online
        grace = const.CONTROLLER_OFFLINE_GRACE_SECONDS
        self.assertEqual(grace, 600)                              # mqtt_offline_time < 600
        for raw in ("1", 1, True):
            self.assertTrue(online(raw, 99999), raw)
        self.assertTrue(online("0", 0))
        self.assertTrue(online("0", grace - 1))
        self.assertTrue(online(0, "599"))
        self.assertFalse(online("0", grace))
        self.assertFalse(online("0", None))
        self.assertFalse(online("0", "n/a"))
        self.assertFalse(online(None, 0))
        self.assertFalse(online("", 0))
        self.assertFalse(online("2", 0))


CONTR = {"type": "contr", "id_name": "1000000000001", "name": "Дом",
         "params": "[47,0,0,45,1,291023]", "mqtt_online": "0", "mqtt_offline_time": 120}


class FakeListApi:
    def __init__(self, devices):
        self.devices = devices

    async def get_all_devices(self):
        return self.devices

    async def get_controller(self, dev_id):
        return next(d for d in self.devices if d.get("id_name") == dev_id)


class CoordinatorOnlineAndIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def update(self, devices):
        coord = coordinator_mod.KotelCoordinator(None, FakeListApi(devices))
        return coord, await coord._async_update_data()

    async def test_short_outage_still_online_but_raw_value_kept(self):
        _, data = await self.update([CONTR])
        controller = data[CONTR["id_name"]]
        self.assertEqual(controller["mqtt_online"], "0")
        self.assertTrue(controller["online"])
        self.assertEqual(controller["mqtt_offline_time"], 120)

    async def test_long_outage_is_offline(self):
        _, data = await self.update([{**CONTR, "mqtt_offline_time": 5000}])
        self.assertFalse(data[CONTR["id_name"]]["online"])

    async def test_one_broken_item_does_not_stop_the_others(self):
        good = {"type": "dt", "id_name": "2000000000002", "name": "Д", "temp": "21", "time": 5}
        broken = {"type": "dt", "id_name": "3000000000003", "name": "Х", "temp": "1", "time": 1}
        original = coordinator_mod.to_number

        def flaky(value):
            if value == 1:
                raise RuntimeError("испорченные данные")
            return original(value)

        coordinator_mod.to_number = flaky
        try:
            coord, data = await self.update([CONTR, broken, good])
        finally:
            coordinator_mod.to_number = original
        self.assertEqual(set(data), {CONTR["id_name"], good["id_name"]})
        self.assertEqual(coord._bad_devices, {"3000000000003"})

    async def test_recovered_item_returns(self):
        coord, _ = await self.update([CONTR])
        coord._bad_devices = {"1000000000001"}
        data = await coord._async_update_data()
        self.assertIn(CONTR["id_name"], data)
        self.assertEqual(coord._bad_devices, set())


class CheckRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_payload_matches_the_app(self):
        client = api_mod.KotelApiClient("https://app.reglerpro.ru", "u", "p", "H1", session=object())
        seen = {}

        async def fake_post(payload, **kwargs):
            seen.update(payload=payload, kwargs=kwargs)
            return {"params": "[1]"}

        client._post = fake_post
        self.assertEqual(await client.get_controller_check("c1"), {"params": "[1]"})
        payload = seen["payload"]
        self.assertEqual(payload["action"], "get_big_contr_check")
        self.assertEqual((payload["id_name"], payload["userId"]), ("c1", "H1"))
        self.assertTrue(seen["kwargs"]["idempotent"])         # чтение: можно повторять на другом сервере
        self.assertIs(seen["kwargs"]["expect"], dict)


class FakeCloud:
    """Облако и контроллер: запись применяется не сразу, может потеряться или кэшироваться."""

    def __init__(self, params, lost_once=(), cached=()):
        self.params = dict(params)           # что сейчас на контроллере
        self.visible = dict(params)          # что показывает get_big_contr (кэш)
        self.lost_once, self.cached = set(lost_once), set(cached)
        self.writes: list[tuple[str, object]] = []
        self.check_calls = 0
        self.check_error = None

    def write(self, key, value):
        self.writes.append((key, value))
        if key in self.lost_once:
            self.lost_once.discard(key)        # первая команда потеряна
            return
        self.params[key] = value
        if key not in self.cached:
            self.visible[key] = value

    def refresh(self):
        for key in self.cached:
            pass                               # кэшируемые ключи get_big_contr не обновляет
        return dict(self.visible)


class FakeApi:
    def __init__(self, cloud):
        self.cloud = cloud

    async def send_command(self, action, dev_id, value=0):
        self.cloud.write(action, value)
        return {}

    async def get_controller_check(self, dev_id):
        self.cloud.check_calls += 1
        if self.cloud.check_error:
            raise self.cloud.check_error
        return {"params": _params_string(self.cloud.params)}

    async def get_climate_sensors(self, dev_id):
        return []


def _params_string(params):
    """params в JSON-формате: индексы 0 (temp_w_ust) и 15 (temp_w_min) из params_parser."""
    arr = [0] * 31
    arr[0] = params["temp_w_ust"]
    arr[15] = params["temp_w_min"]
    return str(arr)


class SecondPassTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved = (profiles_mod.WRITE_DELAY, profiles_mod.VERIFY_FIRST_WAIT,
                       profiles_mod.VERIFY_INTERVAL, profiles_mod.VERIFY_TIMEOUT,
                       profiles_mod.VERIFY_RETRY_TIMEOUT)
        profiles_mod.WRITE_DELAY = 0
        profiles_mod.VERIFY_FIRST_WAIT = 0
        profiles_mod.VERIFY_INTERVAL = 0.001
        profiles_mod.VERIFY_TIMEOUT = 0.01
        profiles_mod.VERIFY_RETRY_TIMEOUT = 0.01

    def tearDown(self):
        (profiles_mod.WRITE_DELAY, profiles_mod.VERIFY_FIRST_WAIT, profiles_mod.VERIFY_INTERVAL,
         profiles_mod.VERIFY_TIMEOUT, profiles_mod.VERIFY_RETRY_TIMEOUT) = self._saved

    def make(self, **cloud_kwargs):
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
        cloud = FakeCloud({"temp_w_ust": 60, "temp_w_min": 40}, **cloud_kwargs)
        api = FakeApi(cloud)
        hass = SimpleNamespace(bus=SimpleNamespace(async_fire=lambda *a, **k: None))
        manager = profiles_mod.KotelProfileManager(hass)
        coord = SimpleNamespace(
            data={"c1": {"type": "contr", "name": "К", "online": True, "detail_ok": True,
                         "mqtt_online": "1", "params": dict(cloud.visible), "climate": {}}},
            last_update_success=True)

        async def refresh():
            coord.data["c1"]["params"] = cloud.refresh()

        coord.async_refresh = refresh
        manager.register_entry("e1", coord, api)
        return manager, cloud

    async def apply(self, manager, verify=True):
        return await manager._apply_values(
            "c1", {"boiler": {"temp_w_ust": 55, "temp_w_min": 35}}, ["boiler"],
            label="тест", kind="boiler", dry_run=False, force=False, verify=verify, backup=False)

    async def test_everything_verified_needs_no_second_pass(self):
        manager, cloud = self.make()
        report = await self.apply(manager)
        self.assertTrue(report["ok"])
        self.assertEqual((report["retried"], report["confirmed_by_check"], cloud.check_calls), (0, 0, 0))
        self.assertEqual(len(cloud.writes), 2)

    async def test_stale_cache_is_resolved_by_the_check_request(self):
        manager, cloud = self.make(cached={"temp_w_min"})        # get_big_contr отстаёт
        report = await self.apply(manager)
        self.assertTrue(report["ok"])
        self.assertEqual(report["confirmed_by_check"], 1)
        self.assertEqual(report["retried"], 0)
        self.assertEqual(len(cloud.writes), 2)                    # повторной записи не было

    async def test_lost_command_is_written_again_once(self):
        manager, cloud = self.make(lost_once={"temp_w_min"})
        report = await self.apply(manager)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["retried"], 1)
        self.assertEqual(report["unverified"], [])
        self.assertEqual([k for k, _ in cloud.writes].count("temp_w_min"), 2)
        self.assertEqual([k for k, _ in cloud.writes].count("temp_w_ust"), 1)

    async def test_hopeless_value_is_retried_only_once_and_reported_with_actual(self):
        manager, cloud = self.make(lost_once={"temp_w_min"})
        cloud.lost_once = set()
        original_write = cloud.write

        def never_applies(key, value):
            if key == "temp_w_min":
                cloud.writes.append((key, value))
                return
            original_write(key, value)

        cloud.write = never_applies
        report = await self.apply(manager)
        self.assertFalse(report["ok"])
        self.assertEqual([k for k, _ in cloud.writes].count("temp_w_min"), 2)   # не больше одного повтора
        self.assertEqual(report["unverified"],
                         [{"scope": "boiler", "key": "temp_w_min", "expected": 35, "actual": 40}])

    async def test_check_request_failure_is_ignored(self):
        manager, cloud = self.make(lost_once={"temp_w_ust"})
        cloud.check_error = KotelApiError("HTTP 500")
        report = await self.apply(manager)
        self.assertTrue(report["ok"])
        self.assertEqual(report["retried"], 1)

    async def test_verify_off_means_no_checks_and_no_retries(self):
        manager, cloud = self.make(lost_once={"temp_w_min"})
        report = await self.apply(manager, verify=False)
        self.assertTrue(report["ok"])
        self.assertEqual((cloud.check_calls, len(cloud.writes)), (0, 2))

    async def test_offline_rule_is_used_before_applying(self):
        manager, cloud = self.make()
        manager._runtime("c1")[0].data["c1"]["online"] = False
        with self.assertRaises(sys.modules["homeassistant.exceptions"].HomeAssistantError):
            await manager._apply_values(
                "c1", {"boiler": {"temp_w_ust": 55}}, ["boiler"], label="т", kind="boiler",
                dry_run=False, force=False, verify=False, backup=False)
        manager._runtime("c1")[0].data["c1"]["online"] = True
        report = await manager._apply_values(
            "c1", {"boiler": {"temp_w_ust": 55}}, ["boiler"], label="т", kind="boiler",
            dry_run=False, force=False, verify=False, backup=False)
        self.assertTrue(report["ok"])

    def test_is_online_falls_back_to_mqtt_online(self):
        self.assertTrue(profiles_mod._is_online({"mqtt_online": "1"}))
        self.assertFalse(profiles_mod._is_online({"mqtt_online": "0"}))
        self.assertTrue(profiles_mod._is_online({"online": True, "mqtt_online": "0"}))
        self.assertFalse(profiles_mod._is_online({"online": False, "mqtt_online": "1"}))


class ButtonMessageTests(unittest.TestCase):
    def test_message_names_the_problem_values(self):
        report = {"applied": 4, "failed": [], "skipped": [], "retried": 1,
                  "unverified": [{"scope": "boiler", "key": "temp_w_min", "expected": 35, "actual": 40}]}
        text = button_mod._summary(report)
        self.assertIn("не подтверждено 1", text)
        self.assertIn("boiler.temp_w_min (ожидалось 35, получено 40)", text)
        self.assertIn("повторно записано 1", text)

    def test_message_is_short_for_many_problems(self):
        report = {"applied": 0, "skipped": [], "unverified": [], "failed": [
            {"scope": "schedule", "key": f"k{i}", "error": "нет связи"} for i in range(9)]}
        text = button_mod._summary(report)
        self.assertIn("ещё 4", text)
        self.assertLess(len(text), 400)

    def test_clean_report_has_no_problem_text(self):
        report = {"applied": 2, "failed": [], "unverified": [], "skipped": []}
        self.assertNotIn("Проблемы", button_mod._summary(report))


if __name__ == "__main__":
    unittest.main()
