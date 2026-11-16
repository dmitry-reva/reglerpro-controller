"""Тесты находок 18, 51 и 70: runtime_data, PARALLEL_UPDATES, службы в async_setup."""

from __future__ import annotations

import importlib
import importlib.util
import json
import re
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


const = mod("const")
runtime_mod = mod("runtime")
services_mod = mod("services")
coordinator_mod = mod("coordinator")
config_flow_mod = mod("config_flow")
exceptions = sys.modules["homeassistant.exceptions"]
ServiceValidationError = exceptions.ServiceValidationError
ConfigEntryAuthFailed = exceptions.ConfigEntryAuthFailed
HomeAssistantError = exceptions.HomeAssistantError


def load_init():
    """Настоящий __init__.py интеграции в отдельном модуле."""
    spec = importlib.util.spec_from_file_location(
        "kotel_init_runtime", COMPONENT / "__init__.py",
        submodule_search_locations=[str(COMPONENT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["kotel_init_runtime"] = module
    spec.loader.exec_module(module)
    return module


class FakeManager:
    def __init__(self, hass=None):
        self.loaded = 0
        self.registered, self.unregistered = [], []

    async def async_load(self):
        self.loaded += 1

    def register_entry(self, entry_id, coordinator, api):
        self.registered.append((entry_id, coordinator, api))

    def unregister_entry(self, entry_id):
        self.unregistered.append(entry_id)


class FakeApi:
    def __init__(self, **kwargs):
        self.kwargs, self.closed = kwargs, 0

    async def close(self):
        self.closed += 1


class FakeCoordinator:
    error = None

    def __init__(self, hass, api, scan_interval=30, config_entry=None):
        self.api, self.config_entry = api, config_entry

    async def async_config_entry_first_refresh(self):
        if FakeCoordinator.error:
            raise FakeCoordinator.error


def make_entry():
    entry = SimpleNamespace(
        entry_id="E1", data={"url": "https://app.reglerpro.ru", "login": "u", "password": "p",
                             "home_id": "H1"}, options={})
    entry.add_update_listener = lambda listener: (lambda: None)
    entry.async_on_unload = lambda func: None
    return entry


def make_hass():
    forwarded = []

    async def forward(entry, platforms):
        forwarded.append((entry, list(platforms)))

    async def unload(entry, platforms):
        return True

    hass = SimpleNamespace(
        data={},
        config_entries=SimpleNamespace(
            async_forward_entry_setups=forward, async_unload_platforms=unload,
            async_reload=None))
    return hass, forwarded


class SetupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.init = load_init()
        self.registered_services = []
        self.init.KotelProfileManager = FakeManager
        self.init.KotelApiClient = FakeApi
        self.init.KotelCoordinator = FakeCoordinator
        self.init.async_get_clientsession = lambda hass: "session"
        self.init.async_register_services = lambda hass: self.registered_services.append(hass)
        FakeCoordinator.error = None

    async def test_async_setup_creates_manager_and_registers_services_once(self):
        hass, _ = make_hass()
        self.assertTrue(await self.init.async_setup(hass, {}))
        manager = hass.data[const.DOMAIN]["_profiles"]
        self.assertIsInstance(manager, FakeManager)
        self.assertEqual(manager.loaded, 1)
        self.assertEqual(self.registered_services, [hass])

    async def test_setup_entry_uses_runtime_data_not_hass_data(self):
        hass, forwarded = make_hass()
        await self.init.async_setup(hass, {})
        self.registered_services.clear()
        entry = make_entry()
        self.assertTrue(await self.init.async_setup_entry(hass, entry))
        runtime = entry.runtime_data
        # __init__.py загружен как отдельный модуль, поэтому сравниваем по имени класса
        self.assertEqual(type(runtime).__name__, runtime_mod.KotelRuntimeData.__name__)
        self.assertIsInstance(runtime.api, FakeApi)
        self.assertIs(runtime.coordinator.api, runtime.api)
        self.assertIs(runtime.coordinator.config_entry, entry)
        self.assertEqual(set(hass.data[const.DOMAIN]), {"_profiles"})   # записи в hass.data нет
        self.assertEqual(hass.data[const.DOMAIN]["_profiles"].registered,
                         [("E1", runtime.coordinator, runtime.api)])
        self.assertEqual(len(forwarded[0][1]), 7)
        self.assertEqual(self.registered_services, [])                  # службы уже есть

    async def test_failed_first_refresh_leaves_nothing_behind(self):
        hass, forwarded = make_hass()
        await self.init.async_setup(hass, {})
        FakeCoordinator.error = ConfigEntryAuthFailed("отказ")
        entry = make_entry()
        with self.assertRaises(ConfigEntryAuthFailed):
            await self.init.async_setup_entry(hass, entry)
        self.assertFalse(hasattr(entry, "runtime_data"))
        self.assertEqual(hass.data[const.DOMAIN]["_profiles"].registered, [])
        self.assertEqual(forwarded, [])

    async def test_unload_unregisters_entry_and_closes_client(self):
        hass, _ = make_hass()
        await self.init.async_setup(hass, {})
        entry = make_entry()
        await self.init.async_setup_entry(hass, entry)
        self.assertTrue(await self.init.async_unload_entry(hass, entry))
        self.assertEqual(hass.data[const.DOMAIN]["_profiles"].unregistered, ["E1"])
        self.assertEqual(entry.runtime_data.api.closed, 1)

    async def test_failed_platform_unload_changes_nothing(self):
        hass, _ = make_hass()
        await self.init.async_setup(hass, {})
        entry = make_entry()
        await self.init.async_setup_entry(hass, entry)

        async def refuse(entry_, platforms):
            return False

        hass.config_entries.async_unload_platforms = refuse
        self.assertFalse(await self.init.async_unload_entry(hass, entry))
        self.assertEqual(hass.data[const.DOMAIN]["_profiles"].unregistered, [])
        self.assertEqual(entry.runtime_data.api.closed, 0)

    def test_platforms_and_schema(self):
        self.assertEqual(len(self.init.PLATFORMS), 7)
        self.assertTrue(hasattr(self.init, "CONFIG_SCHEMA"))


class ServicesTests(unittest.TestCase):
    def test_all_services_registered_once_without_entries(self):
        names = []
        hass = SimpleNamespace(services=SimpleNamespace(
            async_register=lambda domain, name, func, **kw: names.append((domain, name))))
        services_mod.async_register_services(hass)
        strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
        self.assertEqual({name for _, name in names}, set(strings["services"]))
        self.assertEqual({domain for domain, _ in names}, {const.DOMAIN})
        self.assertEqual(len(names), 9)

    def test_no_per_entry_unregistration_left(self):
        self.assertFalse(hasattr(services_mod, "async_unregister_services"))
        self.assertFalse(hasattr(services_mod, "SERVICE_NAMES"))

    def test_service_without_loaded_integration_gives_clear_error(self):
        with self.assertRaises(ServiceValidationError):
            services_mod._manager(SimpleNamespace(data={}))
        with self.assertRaises(ServiceValidationError):
            services_mod._manager(SimpleNamespace(data={const.DOMAIN: {}}))
        manager = object()
        self.assertIs(services_mod._manager(SimpleNamespace(data={const.DOMAIN: {"_profiles": manager}})),
                      manager)


class ParallelUpdatesTests(unittest.TestCase):
    def test_values(self):
        expected = {"sensor": 0, "switch": 1, "number": 1, "select": 1, "text": 1,
                    "button": 1, "climate": 1}
        for name, value in expected.items():
            self.assertEqual(getattr(mod(name), "PARALLEL_UPDATES", None), value, name)


class FakeCommandApi:
    def __init__(self):
        self.calls = []

    async def send_command(self, action, dev_id, value=0):
        self.calls.append(("send_command", action, dev_id, value))
        return {}

    async def set_climate_value(self, dev_id, key, value):
        self.calls.append(("set_climate_value", dev_id, key, value))


def make_coordinator(api):
    coord = SimpleNamespace(data={"c1": {"type": "contr", "name": "К", "params": {}, "climate": {}}},
                            last_update_success=True, api=api, refreshes=0,
                            hass=SimpleNamespace(data={}))

    async def refresh():
        coord.refreshes += 1

    coord.async_request_refresh = refresh
    coord.async_refresh = refresh
    return coord


class EntitiesUseCoordinatorApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_number_entity(self):
        api = FakeCommandApi()
        coord = make_coordinator(api)
        entity = mod("number").KotelNumberEntity(
            coord, "c1", "temp_w_ust", "boiler_target_temperature_max", 10, 90, 1, "°C", None)
        await entity.async_set_native_value(55)
        self.assertEqual(api.calls, [("send_command", "temp_w_ust", "c1", 55)])
        self.assertEqual(coord.refreshes, 1)

    async def test_schedule_number(self):
        api = FakeCommandApi()
        entity = mod("number").KotelScheduleNumber(
            make_coordinator(api), "c1", "m3_P{n}_{kind}", 1, "temp")
        await entity.async_set_native_value(22.5)
        self.assertEqual(api.calls, [("set_climate_value", "c1", "m3_P1_temp", 22.5)])

    async def test_select_and_climate_use_coordinator_api(self):
        api = FakeCommandApi()
        coord = make_coordinator(api)
        self.assertIs(mod("select").KotelClimateSensorSelect(coord, "c1")._api, api)
        self.assertIs(mod("climate").KotelClimate(coord, "c1")._api, api)

    def test_coordinator_exposes_api(self):
        api = FakeCommandApi()
        coord = coordinator_mod.KotelCoordinator(None, api)
        self.assertIs(coord.api, api)


class NoLegacyLookupsTests(unittest.TestCase):
    def test_no_module_reads_entry_data_from_hass_data(self):
        pattern = re.compile(r"hass\.data\[DOMAIN\]\[(entry|self\._entry_id|self\.config_entry)")
        for path in sorted(COMPONENT.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            self.assertIsNone(pattern.search(source), path.name)

    def test_entity_constructors_do_not_take_entry_id(self):
        for name in ("climate", "number", "switch", "select"):
            source = (COMPONENT / f"{name}.py").read_text(encoding="utf-8")
            self.assertNotIn("entry_id", source, name)


class OptionsFlowNotLoadedTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_sensor_step_aborts_when_entry_is_not_loaded(self):
        flow = config_flow_mod.KotelOptionsFlow()
        flow.config_entry = SimpleNamespace(entry_id="E1", data={"home_id": "H1"}, options={})
        flow.hass = SimpleNamespace(data={})
        flow.async_abort = lambda **kw: {"type": "abort", **kw}
        result = await flow.async_step_add_temperature_sensor()
        self.assertEqual(result, {"type": "abort", "reason": "not_loaded"})

    def test_strings(self):
        strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
        self.assertIn("not_loaded", strings["options"]["abort"])


class DiagnosticsWithoutRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_entry_without_runtime_data(self):
        diagnostics = mod("diagnostics")
        entry = SimpleNamespace(entry_id="E1", title="t", version=1, options={},
                                data={"url": "u", "login": "abc", "password": "pass", "home_id": "123456"})
        result = await diagnostics.async_get_config_entry_diagnostics(SimpleNamespace(data={}), entry)
        self.assertIsNone(result["api"]["servers"])
        self.assertEqual(result["coordinator"]["devices"], {})


if __name__ == "__main__":
    unittest.main()
