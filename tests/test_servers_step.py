"""Тесты шага «Серверы» в настройках и передачи списка серверов клиенту API."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ha_stubs  # noqa: E402

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "kotel"
PKG = "kotel_under_test"
if PKG not in sys.modules:
    ha_stubs.load_integration(COMPONENT, PKG)

config_flow_mod = importlib.import_module(f"{PKG}.config_flow")
api_mod = importlib.import_module(f"{PKG}.api")
const = importlib.import_module(f"{PKG}.const")

APP = "https://app.reglerpro.ru"
WEB = "https://web.reglerpro.ru"


class UrlHelperTests(unittest.TestCase):
    def test_valid_urls(self):
        valid = config_flow_mod._is_valid_server_url
        self.assertTrue(valid("https://web.reglerpro.ru"))
        self.assertTrue(valid("http://192.168.1.5:8080"))
        for bad in ("", "web.reglerpro.ru", "ftp://x.ru", "https://", "https://a b.ru",
                    "https://x.ru?a=1", "https://x.ru#f"):
            self.assertFalse(valid(bad), bad)

    def test_clean(self):
        clean = config_flow_mod._clean_server_urls
        self.assertEqual(clean(" https://APP.reglerpro.ru/ ", [APP, WEB + "/", WEB, "", None]),
                         ("https://APP.reglerpro.ru", [WEB]))
        self.assertEqual(clean(APP, None), (APP, []))


class FakeApi:
    """Клиент API для проверки серверов: ответ задаётся по адресу."""

    answers: dict = {}
    created: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        FakeApi.created.append(kwargs)

    async def get_all_devices(self):
        answer = FakeApi.answers[self.kwargs["base_url"]]
        if isinstance(answer, Exception):
            raise answer
        return answer


def make_flow(options=None, url=APP, data_extra=None):
    updates = []
    entry = SimpleNamespace(
        data={"url": url, "login": "u", "password": "p", "home_id": "H1", **(data_extra or {})},
        options=dict(options or {}))

    def update_entry(entry_, data=None, options=None):
        updates.append({"data": data, "options": options})

    flow = config_flow_mod.KotelOptionsFlow()
    flow.hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update_entry))
    flow.config_entry = entry
    flow.async_show_form = lambda **kw: {"type": "form", **kw}
    flow.async_create_entry = lambda **kw: {"type": "create", **kw}
    flow.async_show_menu = lambda **kw: {"type": "menu", **kw}
    return flow, entry, updates


class ServersStepTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeApi.created = []
        FakeApi.answers = {APP: [{"id_name": "c1"}], WEB: [{"id_name": "c1"}]}
        self._orig = config_flow_mod.KotelApiClient
        config_flow_mod.KotelApiClient = FakeApi

    def tearDown(self):
        config_flow_mod.KotelApiClient = self._orig

    async def test_menu_has_servers(self):
        flow, _, _ = make_flow()
        result = await flow.async_step_init()
        self.assertIn("servers", result["menu_options"])

    async def test_form_shown_first(self):
        flow, _, updates = make_flow()
        result = await flow.async_step_servers()
        self.assertEqual((result["type"], result["step_id"]), ("form", "servers"))
        self.assertEqual(updates, [])

    async def test_save_fallbacks_checks_each_server_alone(self):
        flow, entry, updates = make_flow(options={"scan_interval": 60})
        result = await flow.async_step_servers({"url": APP, "fallback_urls": [WEB]})
        self.assertEqual(result["type"], "create")
        self.assertEqual(result["data"], {"scan_interval": 60, "fallback_urls": [WEB]})
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["options"], result["data"])
        self.assertEqual(updates[0]["data"], entry.data)               # адрес не менялся
        self.assertEqual({c["base_url"] for c in FakeApi.created}, {APP, WEB})
        self.assertTrue(all(c["fallback_urls"] == [] for c in FakeApi.created))

    async def test_primary_change_updates_entry_data_once(self):
        flow, entry, updates = make_flow()
        result = await flow.async_step_servers({"url": WEB + "/", "fallback_urls": [APP]})
        self.assertEqual(result["type"], "create")
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["data"]["url"], WEB)
        self.assertEqual(updates[0]["data"]["login"], "u")
        self.assertEqual(updates[0]["options"]["fallback_urls"], [APP])

    async def test_cleared_list_disables_switching(self):
        flow, _, updates = make_flow()
        result = await flow.async_step_servers({"url": APP})            # поле очищено: ключа нет
        self.assertEqual(result["data"]["fallback_urls"], [])
        self.assertEqual([c["base_url"] for c in FakeApi.created], [APP])

    async def test_primary_is_removed_from_fallbacks(self):
        flow, _, _ = make_flow()
        result = await flow.async_step_servers({"url": APP, "fallback_urls": [APP, WEB]})
        self.assertEqual(result["data"]["fallback_urls"], [WEB])

    async def test_invalid_url_is_rejected_without_network(self):
        flow, _, updates = make_flow()
        result = await flow.async_step_servers({"url": APP, "fallback_urls": ["web.reglerpro.ru"]})
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"base": "invalid_url"})
        self.assertEqual(result["description_placeholders"]["server"], "web.reglerpro.ru")
        self.assertEqual((updates, FakeApi.created), ([], []))

    async def test_unreachable_server_blocks_save_and_names_it(self):
        FakeApi.answers[WEB] = api_mod.KotelApiError("HTTP 502")
        flow, _, updates = make_flow()
        result = await flow.async_step_servers({"url": APP, "fallback_urls": [WEB]})
        self.assertEqual(result["errors"], {"base": "server_check_failed"})
        self.assertEqual(result["description_placeholders"], {"server": WEB, "error": "HTTP 502"})
        self.assertEqual(updates, [])

    async def test_empty_device_list_counts_as_failure(self):
        FakeApi.answers[APP] = []
        flow, _, updates = make_flow()
        result = await flow.async_step_servers({"url": APP})
        self.assertEqual(result["errors"], {"base": "server_check_failed"})
        self.assertEqual(updates, [])

    async def test_skip_check_saves_without_requests(self):
        FakeApi.answers[WEB] = api_mod.KotelApiError("недоступен")
        flow, _, updates = make_flow()
        result = await flow.async_step_servers(
            {"url": APP, "fallback_urls": [WEB], "skip_check": True})
        self.assertEqual(result["type"], "create")
        self.assertEqual(FakeApi.created, [])
        self.assertEqual(len(updates), 1)

    async def test_settings_step_keeps_servers(self):
        flow, _, _ = make_flow(options={"fallback_urls": [WEB]})
        result = await flow.async_step_settings({"scan_interval": 45})
        self.assertEqual(result["data"], {"fallback_urls": [WEB], "scan_interval": 45})


class SetupPassesServersTests(unittest.IsolatedAsyncioTestCase):
    """Настоящий __init__.py: список серверов из options доходит до клиента API."""

    async def run_setup(self, options):
        spec = importlib.util.spec_from_file_location(
            "kotel_init_under_test", COMPONENT / "__init__.py",
            submodule_search_locations=[str(COMPONENT)])
        module = importlib.util.module_from_spec(spec)
        sys.modules["kotel_init_under_test"] = module
        spec.loader.exec_module(module)
        captured = {}

        class Stop(Exception):
            pass

        class FakeClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        def stop(*args, **kwargs):
            raise Stop

        module.KotelApiClient = FakeClient
        module.KotelCoordinator = stop
        entry = SimpleNamespace(
            data={"url": APP, "login": "u", "password": "p", "home_id": "H1"}, options=options)
        with self.assertRaises(Stop):
            await module.async_setup_entry(SimpleNamespace(), entry)
        return captured

    async def test_default_is_automatic(self):
        captured = await self.run_setup({})
        self.assertIsNone(captured["fallback_urls"])

    async def test_explicit_list_and_disabled(self):
        self.assertEqual((await self.run_setup({"fallback_urls": [WEB]}))["fallback_urls"], [WEB])
        self.assertEqual((await self.run_setup({"fallback_urls": []}))["fallback_urls"], [])


class EffectiveServersTests(unittest.TestCase):
    def test_options_decide(self):
        build = api_mod.build_servers
        self.assertEqual(build(APP, None), [APP, WEB])
        self.assertEqual(build(APP, []), [APP])
        self.assertEqual(build(APP, [WEB, "https://example.org"]), [APP, WEB, "https://example.org"])


class StringsTests(unittest.TestCase):
    def test_strings_cover_the_step(self):
        d = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
        options = d["options"]
        self.assertIn("servers", options["step"]["init"]["menu_options"])
        self.assertEqual(set(options["step"]["servers"]["data"]),
                         {"url", "fallback_urls", "skip_check"})
        for key in ("invalid_url", "server_check_failed"):
            self.assertIn(key, options["error"])
        self.assertIn("{server}", options["error"]["invalid_url"])
        self.assertIn("{error}", options["error"]["server_check_failed"])


if __name__ == "__main__":
    unittest.main()
