"""Тесты изменений версии 1.4.6 (находки 36, 43, 45, 48, 49, 54, 57, 58, 44)."""

from __future__ import annotations

import importlib
import json
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ha_stubs  # noqa: E402

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "kotel"
PKG = "kotel_under_test"
ha_stubs.load_integration(COMPONENT, PKG)


def mod(name: str):
    return importlib.import_module(f"{PKG}.{name}")


const = mod("const")
api_mod = mod("api")
coordinator_mod = mod("coordinator")
climate_mod = mod("climate")
switch_mod = mod("switch")
sensor_mod = mod("sensor")
profiles_mod = mod("profiles")
services_mod = mod("services")
config_flow_mod = mod("config_flow")
text_mod = mod("text")
HomeAssistantError = sys.modules["homeassistant.exceptions"].HomeAssistantError
ServiceValidationError = sys.modules["homeassistant.exceptions"].ServiceValidationError


class FakeApi:
    def __init__(self, devices, details, fail=()):
        self.devices, self.details, self.fail = devices, details, set(fail)
        self.calls: list = []

    async def get_all_devices(self):
        return self.devices

    async def get_controller(self, dev_id):
        if dev_id in self.fail:
            raise api_mod.KotelApiError("нет ответа")
        return self.details.get(dev_id, {})

    async def climate_on_off(self, dev_id):
        self.calls.append(("on_off", dev_id))

    async def send_command(self, action, dev_id, value=0):
        self.calls.append((action, dev_id, value))
        if "boom" in self.fail:
            raise api_mod.KotelApiError("сбой")
        return {}


def make_coordinator(api):
    return coordinator_mod.KotelCoordinator(None, api)


CONTR = {"type": "contr", "id_name": "c1", "name": "Котёл", "params": "[45,0,0,52,1,291500]",
         "mqtt_online": 1}
DETAIL = {"params": "[45,0,0,52,1,291500]", "mqtt_online": 1,
          "tstatArr": {"tstatOn": 1, "tstatDtId": "d1", "tstat_mode": 0, "tstatTemp": 22},
          "sensor_temp": "24"}


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_detail_ok_flag(self):
        coord = make_coordinator(FakeApi([CONTR], {"c1": DETAIL}))
        data = await coord._async_update_data()
        self.assertTrue(data["c1"]["detail_ok"])
        self.assertTrue(data["c1"]["climate"]["enabled"])

    async def test_detail_failed_gives_degraded_data(self):
        coord = make_coordinator(FakeApi([CONTR], {"c1": DETAIL}, fail={"c1"}))
        data = await coord._async_update_data()
        self.assertFalse(data["c1"]["detail_ok"])
        self.assertFalse(data["c1"]["climate"]["enabled"])   # из списка климата не видно

    async def test_dt_time_missing_is_none(self):
        dt = {"type": "dt", "id_name": "d1", "name": "Комната", "temp": 21.5}
        data = await make_coordinator(FakeApi([CONTR, dt], {"c1": DETAIL}))._async_update_data()
        self.assertIsNone(data["d1"]["time"])
        self.assertEqual(data["c1"]["climate"]["sensor_temp"], 21.5)   # как раньше

    async def test_fresh_sensor_replaces_temperature(self):
        dt = {"type": "dt", "id_name": "d1", "name": "К", "temp": 21.5, "time": 10}
        data = await make_coordinator(FakeApi([CONTR, dt], {"c1": DETAIL}))._async_update_data()
        self.assertEqual(data["c1"]["climate"]["sensor_temp"], 21.5)

    async def test_stale_sensor_does_not_replace_temperature(self):
        dt = {"type": "dt", "id_name": "d1", "name": "К", "temp": 21.5,
              "time": const.DT_STALE_SECONDS}
        data = await make_coordinator(FakeApi([CONTR, dt], {"c1": DETAIL}))._async_update_data()
        self.assertEqual(data["c1"]["climate"]["sensor_temp"], 24)    # значение контроллера


def make_entity_env(detail_ok=True, success=True, enabled=True):
    api = FakeApi([], {})
    coord = SimpleNamespace(
        data={"c1": {"type": "contr", "name": "Котёл", "detail_ok": detail_ok,
                     "params": {"sost_rab": 0},
                     "climate": {"enabled": enabled, "mode": 0, "active": True}}},
        last_update_success=success, refreshes=0,
        hass=SimpleNamespace(data={const.DOMAIN: {"e1": {"api": api}}}),
    )

    async def refresh():
        coord.refreshes += 1

    coord.async_refresh = refresh
    coord.async_request_refresh = refresh
    return api, coord


class ClimateTests(unittest.IsolatedAsyncioTestCase):
    async def test_refused_when_detail_missing(self):
        api, coord = make_entity_env(detail_ok=False, enabled=False)
        entity = climate_mod.KotelClimate(coord, "c1", "e1")
        with self.assertRaises(HomeAssistantError):
            await entity.async_turn_on()
        self.assertEqual(api.calls, [])           # переключатель не отправлен

    async def test_refused_when_refresh_failed(self):
        api, coord = make_entity_env(success=False)
        entity = climate_mod.KotelClimate(coord, "c1", "e1")
        with self.assertRaises(HomeAssistantError):
            await entity.async_turn_off()
        self.assertEqual(api.calls, [])

    async def test_normal_toggle_still_works(self):
        api, coord = make_entity_env(enabled=False)
        entity = climate_mod.KotelClimate(coord, "c1", "e1")
        await entity.async_turn_on()
        self.assertEqual(api.calls, [("on_off", "c1")])

    def test_schedule_attribute_not_recorded(self):
        self.assertIn("schedule", climate_mod.KotelClimate._unrecorded_attributes)


class SwitchTests(unittest.IsolatedAsyncioTestCase):
    def make(self, sost_rab=0):
        api, coord = make_entity_env()
        if sost_rab is None:
            coord.data["c1"]["params"] = {}
        else:
            coord.data["c1"]["params"] = {"sost_rab": sost_rab}
        entity = switch_mod.KotelStartStopSwitch(coord, "c1", "e1")
        entity.hass = SimpleNamespace()
        timers = []
        switch_mod.async_call_later = lambda hass, delay, action: (
            timers.append((delay, action)) or (lambda: timers.append("cancel")))
        return api, coord, entity, timers

    def test_unknown_without_data(self):
        _, _, entity, _ = self.make(sost_rab=None)
        self.assertIsNone(entity.is_on)

    def test_known_values(self):
        for value, expected in ((0, False), (1, True), (2, True)):
            _, _, entity, _ = self.make(sost_rab=value)
            self.assertIs(entity.is_on, expected)

    async def test_optimistic_state_until_cloud_catches_up(self):
        api, coord, entity, timers = self.make(sost_rab=0)
        await entity.async_turn_on()
        self.assertEqual(api.calls, [("start__", "c1", 0)])
        self.assertIs(entity.is_on, True)                 # облако ещё отдаёт «остановлен»
        coord.data["c1"]["params"] = {"sost_rab": 1}      # облако догнало
        self.assertIs(entity.is_on, True)
        self.assertIsNone(entity._pending)

    async def test_pending_expires(self):
        _, coord, entity, timers = self.make(sost_rab=0)
        await entity.async_turn_on()
        entity._pending_until = time.monotonic() - 1
        self.assertIs(entity.is_on, False)                # показывается реальное состояние

    async def test_timer_scheduled_and_expiry_callback(self):
        _, _, entity, timers = self.make(sost_rab=0)
        await entity.async_turn_on()
        delay, action = timers[0]
        self.assertEqual(delay, switch_mod.PENDING_SECONDS + 1)
        writes = entity.writes
        action(None)
        self.assertIsNone(entity._pending)
        self.assertEqual(entity.writes, writes + 1)

    async def test_failed_command_sets_no_pending(self):
        api, _, entity, _ = self.make(sost_rab=0)
        api.fail.add("boom")
        with self.assertRaises(HomeAssistantError):
            await entity.async_turn_on()
        self.assertIsNone(entity._pending)

    async def test_removal_cancels_timer(self):
        _, _, entity, timers = self.make(sost_rab=0)
        await entity.async_turn_on()
        await entity.async_will_remove_from_hass()
        self.assertIn("cancel", timers)


class SensorTests(unittest.TestCase):
    def make_dt(self, time_value):
        coord = SimpleNamespace(data={"d1": {"type": "dt", "name": "К", "temp": 20, "time": time_value}},
                                last_update_success=True)
        return sensor_mod.KotelDtSensor(coord, "d1")

    def test_online_attribute(self):
        self.assertIsNone(self.make_dt(None).extra_state_attributes["online"])
        self.assertIs(self.make_dt(0).extra_state_attributes["online"], True)
        self.assertIs(self.make_dt(299).extra_state_attributes["online"], True)
        self.assertIs(self.make_dt(300).extra_state_attributes["online"], False)

    def test_debug_sensor_disabled_and_unrecorded(self):
        cls = sensor_mod.KotelContrDebugSensor
        self.assertIs(cls._attr_entity_registry_enabled_default, False)
        for name in ("params_raw", "params", "climate", "tstatArr"):
            self.assertIn(name, cls._unrecorded_attributes)


class TextTests(unittest.TestCase):
    def test_name_must_not_be_empty(self):
        self.assertEqual(text_mod.KotelProfileNameText._attr_native_min, 1)


class ProfileSaveTests(unittest.IsolatedAsyncioTestCase):
    def make_manager(self, success=True, detail_ok=True):
        dt_util = sys.modules["homeassistant.util.dt"]
        from datetime import datetime, timezone
        dt_util.utcnow = lambda: datetime.now(timezone.utc)
        dt_util.now = dt_util.utcnow
        store_mod = sys.modules["homeassistant.helpers.storage"]

        class Store:
            def __init__(self, *a, **k):
                self.saved = 0

            async def async_load(self):
                return None

            async def async_save(self, data):
                self.saved += 1

        store_mod.Store = Store
        profiles_mod.Store = Store
        hass = SimpleNamespace(bus=SimpleNamespace(async_fire=lambda *a, **k: None))
        manager = profiles_mod.KotelProfileManager(hass)
        coord = SimpleNamespace(
            data={"c1": {"type": "contr", "name": "Котёл", "detail_ok": detail_ok,
                         "params": {"temp_w_ust": 60, "temp_w_min": 40, "vers": 291500},
                         "climate": {}}},
            last_update_success=success, refreshes=0)

        async def refresh():
            coord.refreshes += 1

        coord.async_refresh = refresh
        manager.register_entry("e1", coord, SimpleNamespace())
        return manager, coord

    async def test_refreshes_before_snapshot(self):
        manager, coord = self.make_manager()
        result = await manager.async_save_profile("c1", "Зима")
        self.assertEqual(coord.refreshes, 1)
        self.assertEqual(result["profile"], "Зима")
        self.assertEqual(manager._store.saved, 1)

    async def test_refused_when_refresh_failed(self):
        manager, _ = self.make_manager(success=False)
        with self.assertRaises(HomeAssistantError):
            await manager.async_save_profile("c1", "Зима")
        self.assertEqual(manager.profile_names("c1"), [])

    async def test_refused_when_detail_missing(self):
        manager, _ = self.make_manager(detail_ok=False)
        with self.assertRaises(HomeAssistantError):
            await manager.async_save_profile("c1", "Зима")

    async def test_choices_use_given_labels(self):
        manager, _ = self.make_manager()
        await manager.async_save_profile("c1", "Зима")
        choices = manager.profile_choices({"boiler": "Boiler", "climate": "Climate"})
        self.assertEqual(list(choices.values()), ["Boiler · Зима"])
        self.assertEqual(list(manager.profile_choices().values()), ["Котёл · Зима"])


class ServicesTests(unittest.TestCase):
    def call(self, **data):
        return SimpleNamespace(data=data)

    def test_unsupported_targets_rejected(self):
        for key in ("entity_id", "area_id", "floor_id", "label_id"):
            with self.assertRaises(ServiceValidationError):
                services_mod._reject_unsupported_targets(self.call(**{key: ["x"]}))

    def test_device_target_and_empty_values_pass(self):
        services_mod._reject_unsupported_targets(self.call(device_id=["abc"]))
        services_mod._reject_unsupported_targets(self.call(entity_id=[]))

    def test_resolve_rejects_before_picking_controllers(self):
        manager = SimpleNamespace(controllers=lambda: {"c1": {}})
        with self.assertRaises(ServiceValidationError):
            services_mod.resolve_device_ids(None, manager, self.call(area_id=["kitchen"]))

    def test_schema_source_has_no_allow_extra(self):
        source = (COMPONENT / "services.py").read_text(encoding="utf-8")
        self.assertNotIn("ALLOW_EXTRA", source)


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_kind_labels_translated_with_fallback(self):
        prefix = f"component.{const.DOMAIN}.selector.profile_kind.options."
        hass = SimpleNamespace(config=SimpleNamespace(language="en"))

        async def fake(hass_, language, category, integrations):
            self.assertEqual((language, category, integrations), ("en", "selector", [const.DOMAIN]))
            return {prefix + "boiler": "Boiler"}

        config_flow_mod.async_get_translations = fake
        labels = await config_flow_mod._kind_labels(hass)
        self.assertEqual(labels, {"boiler": "Boiler", "climate": "Климат"})   # запас — русский

    def test_import_choices(self):
        profiles = [{"kind": "boiler", "name": "A"}, {"kind": "climate", "name": "B"}]
        labels = {"boiler": "Boiler", "climate": "Climate"}
        self.assertEqual(config_flow_mod._import_choices_for(profiles, labels),
                         {"boiler|A": "Boiler · A", "climate|B": "Climate · B"})

    def test_no_hardcoded_conflict_labels(self):
        source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
        self.assertNotIn("Добавить с номером", source)


class ManifestAndStringsTests(unittest.TestCase):
    def test_manifest(self):
        manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("file_upload", manifest["dependencies"])
        self.assertEqual(manifest["version"], "1.4.6")

    def test_strings(self):
        strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
        self.assertIn("already_configured", strings["config"]["abort"])
        self.assertEqual(set(strings["selector"]["on_conflict"]["options"]),
                         set(config_flow_mod.ON_CONFLICT_OPTIONS))
        self.assertEqual(set(strings["selector"]["profile_kind"]["options"]),
                         set(profiles_mod.ALL_KINDS))

    def test_translation_additions_have_same_keys(self):
        root = COMPONENT.parent.parent / "translations_additions"
        if not root.is_dir():
            self.skipTest("translations_additions уже слит с переводами и удалён")
        en = json.loads((root / "en.json").read_text(encoding="utf-8"))
        ru = json.loads((root / "ru.json").read_text(encoding="utf-8"))

        def keys(node, prefix=""):
            for key, value in node.items():
                yield from (keys(value, f"{prefix}{key}.") if isinstance(value, dict)
                            else [f"{prefix}{key}"])
        self.assertEqual(set(keys(en)), set(keys(ru)))


if __name__ == "__main__":
    unittest.main()
