"""Тесты работы с несколькими серверами (app.reglerpro.ru и web.reglerpro.ru)."""

from __future__ import annotations

import asyncio
import importlib
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

api_mod = importlib.import_module(f"{PKG}.api")
const = importlib.import_module(f"{PKG}.const")
config_flow_mod = importlib.import_module(f"{PKG}.config_flow")
aiohttp = sys.modules["aiohttp"] if "aiohttp" in sys.modules else importlib.import_module("aiohttp")

APP = "https://app.reglerpro.ru"
WEB = "https://web.reglerpro.ru"


class FakeResponse:
    def __init__(self, status=200, payload=None, bad_json=False):
        self.status, self._payload, self._bad_json = status, payload, bad_json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        if self._bad_json:
            raise ValueError("не JSON")
        return self._payload


class FakeSession:
    """Поведение по каждому серверу: ("ok", данные) / ("status", код) /
    ("connect",) / ("timeout",) / ("badjson",)."""

    closed = False

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls: list[tuple[str, str]] = []

    def post(self, url, json=None, headers=None, timeout=None):
        base = url.split("/php/")[0]
        self.calls.append((base, json.get("action")))
        kind, *rest = self.behaviour[base]
        if kind == "connect":
            raise aiohttp.ClientConnectorError("нет соединения")
        if kind == "timeout":
            raise asyncio.TimeoutError()
        if kind == "status":
            return FakeResponse(status=rest[0])
        if kind == "badjson":
            return FakeResponse(bad_json=True)
        return FakeResponse(payload=rest[0])

    def bases(self):
        return [base for base, _ in self.calls]


def client(behaviour, url=APP, **kwargs):
    session = FakeSession(behaviour)
    return api_mod.KotelApiClient(url, "user", "pass", "H1", session=session, **kwargs), session


DEVICES = [{"type": "contr", "id_name": "c1"}]


class ServerListTests(unittest.TestCase):
    def test_known_server_gets_the_other_as_fallback(self):
        self.assertEqual(client({}, APP)[0].servers, (APP, WEB))
        self.assertEqual(client({}, WEB + "/")[0].servers, (WEB, APP))

    def test_case_and_slash_do_not_duplicate(self):
        c, _ = client({}, "https://APP.reglerpro.ru/", fallback_urls=[APP, WEB + "/"])
        self.assertEqual(c.servers, ("https://APP.reglerpro.ru", WEB))

    def test_custom_server_has_no_fallback(self):
        self.assertEqual(client({}, "https://example.org")[0].servers, ("https://example.org",))

    def test_explicit_fallbacks(self):
        c, _ = client({}, "https://example.org", fallback_urls=[APP])
        self.assertEqual(c.servers, ("https://example.org", APP))
        c, _ = client({}, APP, fallback_urls=[])
        self.assertEqual(c.servers, (APP,))


class ReadFailoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_primary_ok_uses_only_primary(self):
        c, s = client({APP: ("ok", DEVICES), WEB: ("ok", [])})
        self.assertEqual(await c.get_all_devices(), DEVICES)
        self.assertEqual(s.bases(), [APP])
        self.assertEqual(c.active_url, APP)

    async def test_failover_on_connect_error_and_sticky(self):
        c, s = client({APP: ("connect",), WEB: ("ok", DEVICES)})
        self.assertEqual(await c.get_all_devices(), DEVICES)
        self.assertEqual(c.active_url, WEB)
        await c.get_all_devices()
        self.assertEqual(s.bases(), [APP, WEB, WEB])          # дальше сразу web

    async def test_failover_on_timeout_5xx_and_bad_json(self):
        for failure in (("timeout",), ("status", 503), ("badjson",)):
            c, s = client({APP: failure, WEB: ("ok", DEVICES)})
            self.assertEqual(await c.get_all_devices(), DEVICES, failure)
            self.assertEqual(c.active_url, WEB)

    async def test_4xx_is_not_failed_over(self):
        for code in (401, 403, 404):
            c, s = client({APP: ("status", code), WEB: ("ok", DEVICES)})
            with self.assertRaises(api_mod.KotelApiError):
                await c.get_all_devices()
            self.assertEqual(s.bases(), [APP])

    async def test_all_down_reports_every_server(self):
        c, _ = client({APP: ("connect",), WEB: ("status", 502)})
        with self.assertRaises(api_mod.KotelApiError) as ctx:
            await c.get_controller("c1")
        message = str(ctx.exception)
        self.assertIn(APP, message)
        self.assertIn(WEB, message)

    async def test_single_server_keeps_original_error(self):
        c, _ = client({"https://example.org": ("connect",)}, "https://example.org")
        with self.assertRaises(api_mod.KotelApiError) as ctx:
            await c.get_all_devices()
        self.assertTrue(str(ctx.exception).startswith("Ошибка соединения"))

    async def test_single_server_timeout_has_its_own_message(self):
        c, _ = client({"https://example.org": ("timeout",)}, "https://example.org")
        with self.assertRaises(api_mod.KotelTimeoutError) as ctx:
            await c.get_all_devices()
        self.assertTrue(str(ctx.exception).startswith("Нет ответа от сервера"))

    async def test_climate_sensor_list_is_a_read(self):
        c, s = client({APP: ("timeout",), WEB: ("ok", {"sensors": [{"id_name": "d1"}]})})
        self.assertEqual(await c.get_climate_sensors("c1"), [{"id_name": "d1"}])
        self.assertEqual(s.bases(), [APP, WEB])


class WriteSafetyTests(unittest.IsolatedAsyncioTestCase):
    """Команда не повторяется на другом сервере, если она могла быть выполнена."""

    async def test_timeout_after_send_is_not_retried(self):
        c, s = client({APP: ("timeout",), WEB: ("ok", {})})
        with self.assertRaises(api_mod.KotelApiError) as ctx:
            await c.send_command("start__", "c1")
        self.assertEqual(s.bases(), [APP])
        self.assertIn("повтор не выполнялся", str(ctx.exception))

    async def test_server_error_after_send_is_not_retried(self):
        c, s = client({APP: ("status", 500), WEB: ("ok", {})})
        with self.assertRaises(api_mod.KotelApiError):
            await c.send_command("temp_w_ust", "c1", 60)
        self.assertEqual(s.bases(), [APP])

    async def test_climate_toggle_is_never_doubled(self):
        c, s = client({APP: ("timeout",), WEB: ("ok", {})})
        with self.assertRaises(api_mod.KotelApiError):
            await c.climate_on_off("c1")
        self.assertEqual(s.bases(), [APP])

    async def test_connect_error_is_safe_to_retry(self):
        c, s = client({APP: ("connect",), WEB: ("ok", {})})
        await c.climate_on_off("c1")                           # запрос не дошёл до app
        self.assertEqual(s.bases(), [APP, WEB])
        self.assertEqual(c.active_url, WEB)

    async def test_add_sensor_is_not_retried_after_timeout(self):
        c, s = client({APP: ("timeout",), WEB: ("ok", "3631427318216")})
        with self.assertRaises(api_mod.KotelApiError):
            await c.add_temperature_sensor()
        self.assertEqual(s.bases(), [APP])

    async def test_add_sensor_fails_over_when_unreachable(self):
        c, s = client({APP: ("connect",), WEB: ("ok", "3631427318216")})
        self.assertEqual(await c.add_temperature_sensor(), "3631427318216")

    async def test_k_correction_shift_preserved_on_failover(self):
        c, s = client({APP: ("connect",), WEB: ("ok", {})})
        await c.send_command("korect_", "c1", 3)
        self.assertEqual(s.calls[-1], (WEB, "korect_"))


class FailbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_to_primary_after_interval(self):
        behaviour = {APP: ("connect",), WEB: ("ok", DEVICES)}
        c, s = client(behaviour)
        await c.get_all_devices()                              # ушли на web
        behaviour[APP] = ("ok", DEVICES)                       # основной ожил
        await c.get_all_devices()
        self.assertEqual(c.active_url, WEB)                    # пока рано возвращаться
        c._switched_at -= api_mod.FAILBACK_SECONDS + 1
        await c.get_all_devices()
        self.assertEqual(c.active_url, APP)

    async def test_failed_failback_is_not_retried_on_every_call(self):
        c, s = client({APP: ("connect",), WEB: ("ok", DEVICES)})
        await c.get_all_devices()
        c._switched_at -= api_mod.FAILBACK_SECONDS + 1
        await c.get_all_devices()                              # проба основного: неудача
        before = s.bases().count(APP)
        await c.get_all_devices()
        await c.get_all_devices()
        self.assertEqual(s.bases().count(APP), before)         # до следующего интервала не пробуем
        self.assertEqual(c.active_url, WEB)


class AccountDuplicateTests(unittest.TestCase):
    def flow(self, entries):
        return SimpleNamespace(_async_current_entries=lambda: entries)

    def check(self, entries, login, home_id):
        return config_flow_mod.KotelConfigFlow._account_already_configured(
            self.flow(entries), login, home_id)

    def test_same_account_on_other_server_is_duplicate(self):
        entry = SimpleNamespace(data={"url": APP, "login": "User", "home_id": "H1"})
        self.assertTrue(self.check([entry], " user ", "H1"))

    def test_other_home_or_login_is_not_duplicate(self):
        entry = SimpleNamespace(data={"url": APP, "login": "user", "home_id": "H1"})
        self.assertFalse(self.check([entry], "user", "H2"))
        self.assertFalse(self.check([entry], "other", "H1"))
        self.assertFalse(self.check([], "user", "H1"))

    def test_url_is_not_part_of_identity(self):
        source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
        self.assertIn("casefold()}|{home_id}", source)


class DefaultsTests(unittest.TestCase):
    def test_known_servers(self):
        self.assertEqual(const.KNOWN_SERVERS, (APP, WEB))
        self.assertEqual(const.DEFAULT_URL, APP)


if __name__ == "__main__":
    unittest.main()
