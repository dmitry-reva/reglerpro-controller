"""Тесты находок 17 и 28: повторная авторизация и обработка неожиданных ответов."""

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

import asyncio  # noqa: E402

APP = "https://app.reglerpro.ru"
WEB = "https://web.reglerpro.ru"
DEVICES = [{"type": "contr", "id_name": "c1"}]
aiohttp = importlib.import_module("aiohttp")


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
    """Поведение по каждому серверу: ("ok", данные) / ("status", код) / ("connect",) /
    ("timeout",) / ("badjson",)."""

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
        if kind == "connect_timeout":
            raise aiohttp.ConnectionTimeoutError()
        if kind == "client_error":
            raise aiohttp.ClientError()
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

api_mod = importlib.import_module(f"{PKG}.api")
coordinator_mod = importlib.import_module(f"{PKG}.coordinator")
config_flow_mod = importlib.import_module(f"{PKG}.config_flow")
exceptions = sys.modules["homeassistant.exceptions"]
ConfigEntryAuthFailed = exceptions.ConfigEntryAuthFailed
UpdateFailed = sys.modules["homeassistant.helpers.update_coordinator"].UpdateFailed
KotelApiError = api_mod.KotelApiError
KotelAuthError = api_mod.KotelAuthError
KotelResponseError = api_mod.KotelResponseError
KotelTimeoutError = api_mod.KotelTimeoutError


class AuthHintTests(unittest.TestCase):
    def test_hints(self):
        looks = api_mod.looks_like_auth_failure
        for yes in ("Wrong password", "Authorization required", {"error": "неверный логин"},
                    {"wrong_login": True}, "Доступ запрещён", "login_pass_in_db_ok - NO"):
            self.assertTrue(looks(yes), yes)
        for no in ([], 0, None, False, "ok", "0", {"status": "error"}, {"a": 1}, ["login"],
                   "login_pass_in_db_ok - YES"):
            self.assertFalse(looks(no), no)

    def test_exact_server_signature(self):
        rejected = api_mod.is_login_rejected
        for yes in ("login_pass_in_db_ok - NO", "login_pass_in_db_ok - no", 'x login_pass_in_db_ok-NO'):
            self.assertTrue(rejected(yes), yes)
        for no in ("login_pass_in_db_ok - YES", "login_pass_in_db_ok - OK", "NO", None, [],
                   {"a": "login_pass_in_db_ok - NO"}):
            self.assertFalse(rejected(no), no)


class ShapeTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_list_is_legitimate(self):
        c, _ = client({APP: ("ok", []), WEB: ("ok", [])})
        self.assertEqual(await c.get_all_devices(), [])

    async def test_non_list_is_an_error_not_an_empty_list(self):
        c, s = client({APP: ("ok", {"status": "maintenance"}), WEB: ("ok", {"status": "maintenance"})})
        with self.assertRaises(KotelResponseError) as ctx:
            await c.get_all_devices()
        self.assertEqual(s.bases(), [APP, WEB])                  # второй сервер спрошен
        self.assertIn("dict", ctx.exception.snippet)
        self.assertNotIsInstance(ctx.exception, KotelAuthError)

    async def test_bad_response_fails_over_to_good_server(self):
        c, s = client({APP: ("ok", {"status": "maintenance"}), WEB: ("ok", DEVICES)})
        self.assertEqual(await c.get_all_devices(), DEVICES)
        self.assertEqual(c.active_url, WEB)

    async def test_auth_like_response_is_not_failed_over(self):
        for answer in ("Wrong password", {"error": "неверный логин или пароль"}):
            c, s = client({APP: ("ok", answer), WEB: ("ok", DEVICES)})
            with self.assertRaises(KotelAuthError):
                await c.get_all_devices()
            self.assertEqual(s.bases(), [APP])

    async def test_real_server_answer_for_wrong_password(self):
        c, s = client({APP: ("ok", "login_pass_in_db_ok - NO"), WEB: ("ok", DEVICES)})
        with self.assertRaises(KotelAuthError) as ctx:
            await c.get_all_devices()
        self.assertEqual(s.bases(), [APP])                      # на другой сервер не переходим
        # логин и пароль в выдержке скрыты (у тестового клиента пароль «pass»)
        self.assertIn("_in_db_ok - NO", str(ctx.exception))

    async def test_login_ok_string_is_not_an_auth_failure(self):
        c, _ = client({"https://example.org": ("ok", "login_pass_in_db_ok - YES")},
                      "https://example.org")
        with self.assertRaises(KotelResponseError) as ctx:
            await c.get_all_devices()
        self.assertNotIsInstance(ctx.exception, KotelAuthError)

    async def test_write_with_wrong_password_is_not_silently_ok(self):
        calls = (lambda c: c.send_command("start__", "c1"),
                 lambda c: c.climate_on_off("c1"),
                 lambda c: c.add_temperature_sensor())
        for call in calls:
            c, s = client({APP: ("ok", "login_pass_in_db_ok - NO"), WEB: ("ok", {})})
            with self.assertRaises(KotelAuthError):
                await call(c)
            self.assertEqual(s.bases(), [APP])

    async def test_http_401_403_are_auth_errors_without_failover(self):
        for code in (401, 403):
            c, s = client({APP: ("status", code), WEB: ("ok", DEVICES)})
            with self.assertRaises(KotelAuthError):
                await c.get_all_devices()
            self.assertEqual(s.bases(), [APP])

    async def test_other_4xx_stay_generic(self):
        c, _ = client({APP: ("status", 404), WEB: ("ok", DEVICES)})
        with self.assertRaises(KotelApiError) as ctx:
            await c.get_all_devices()
        self.assertNotIsInstance(ctx.exception, (KotelAuthError, KotelResponseError))

    async def test_mixed_failures_are_generic(self):
        c, _ = client({APP: ("connect",), WEB: ("ok", {"status": "maintenance"})})
        with self.assertRaises(KotelApiError) as ctx:
            await c.get_all_devices()
        self.assertNotIsInstance(ctx.exception, KotelResponseError)

    async def test_single_server_bad_response(self):
        c, _ = client({"https://example.org": ("ok", 42)}, "https://example.org")
        with self.assertRaises(KotelResponseError) as ctx:
            await c.get_all_devices()
        self.assertIn("int: 42", ctx.exception.snippet)

    async def test_snippet_hides_credentials_and_is_short(self):
        answer = {"note": "user=user pass=pass", "blob": "x" * 500}
        c, _ = client({"https://example.org": ("ok", answer)}, "https://example.org")
        c._password = "s3cretPass"
        answer["note"] = "bad s3cretPass for user"
        with self.assertRaises(KotelResponseError) as ctx:
            await c.get_all_devices()
        snippet = ctx.exception.snippet
        self.assertNotIn("s3cretPass", snippet)
        self.assertLessEqual(len(snippet), 140)
        self.assertTrue(snippet.endswith("..."))

    async def test_controller_detail_must_be_a_dict(self):
        c, _ = client({"https://example.org": ("ok", [1, 2])}, "https://example.org")
        with self.assertRaises(KotelResponseError):
            await c.get_controller("c1")
        c, _ = client({"https://example.org": ("ok", {"params": "x"})}, "https://example.org")
        self.assertEqual(await c.get_controller("c1"), {"params": "x"})

    async def test_hanging_servers_give_a_timeout_error(self):
        # верный логин, пользователь без домов, несуществующий дом: сервер не отвечает
        c, s = client({APP: ("timeout",), WEB: ("timeout",)})
        with self.assertRaises(KotelTimeoutError) as ctx:
            await c.get_all_devices()
        self.assertEqual(s.bases(), [APP, WEB])
        self.assertIn("Нет ответа от сервера", str(ctx.exception))

    async def test_single_hanging_server_gives_a_timeout_error(self):
        c, _ = client({"https://example.org": ("timeout",)}, "https://example.org")
        with self.assertRaises(KotelTimeoutError):
            await c.get_all_devices()

    async def test_timeout_plus_other_failure_is_generic(self):
        c, _ = client({APP: ("timeout",), WEB: ("connect",)})
        with self.assertRaises(KotelApiError) as ctx:
            await c.get_all_devices()
        self.assertNotIsInstance(ctx.exception, KotelTimeoutError)

    async def test_write_timeout_is_not_reported_as_plain_timeout(self):
        c, s = client({APP: ("timeout",), WEB: ("ok", {})})
        with self.assertRaises(KotelApiError) as ctx:
            await c.send_command("start__", "c1")
        self.assertNotIsInstance(ctx.exception, KotelTimeoutError)
        self.assertEqual(s.bases(), [APP])

    async def test_error_messages_are_never_empty(self):
        for kind in ("connect_timeout", "client_error"):
            c, _ = client({"https://example.org": (kind,)}, "https://example.org")
            with self.assertRaises(KotelApiError) as ctx:
                await c.get_all_devices()
            text = str(ctx.exception)
            self.assertFalse(text.endswith(": "), text)
            self.assertIn("Error", text)

    async def test_write_responses_stay_lenient(self):
        c, _ = client({APP: ("ok", "ok"), WEB: ("ok", "ok")})
        self.assertEqual(await c.send_command("start__", "c1"), {})


class FakeApi:
    def __init__(self, devices=None, error=None, details=None, detail_error=None):
        self.devices, self.error = devices if devices is not None else [], error
        self.details, self.detail_error = details or {}, detail_error

    async def get_all_devices(self):
        if self.error:
            raise self.error
        return self.devices

    async def get_controller(self, dev_id):
        if self.detail_error:
            raise self.detail_error
        return self.details.get(dev_id, {})


CONTR = {"type": "contr", "id_name": "c1", "name": "Котёл", "params": "[45,0,0,52,1,291500]"}


def coordinator(api, entry=object()):
    return coordinator_mod.KotelCoordinator(None, api, config_entry=entry)


class CoordinatorAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_refresh_auth_error_starts_reauth_immediately(self):
        coord = coordinator(FakeApi(error=KotelAuthError("HTTP 401")))
        with self.assertRaises(ConfigEntryAuthFailed):
            await coord._async_update_data()

    async def test_later_auth_errors_need_two_in_a_row(self):
        api = FakeApi(devices=[CONTR])
        coord = coordinator(api)
        coord.data = await coord._async_update_data()
        api.error = KotelAuthError("Wrong password")
        with self.assertRaises(UpdateFailed):
            await coord._async_update_data()
        with self.assertRaises(ConfigEntryAuthFailed):
            await coord._async_update_data()

    async def test_success_resets_the_auth_counter(self):
        api = FakeApi(devices=[CONTR])
        coord = coordinator(api)
        coord.data = await coord._async_update_data()
        api.error = KotelAuthError("x")
        with self.assertRaises(UpdateFailed):
            await coord._async_update_data()
        api.error = None
        coord.data = await coord._async_update_data()
        api.error = KotelAuthError("x")
        with self.assertRaises(UpdateFailed):                    # снова «первый» отказ
            await coord._async_update_data()

    async def test_controller_detail_auth_error_starts_reauth(self):
        coord = coordinator(FakeApi(devices=[CONTR], detail_error=KotelAuthError("HTTP 403")))
        with self.assertRaises(ConfigEntryAuthFailed):
            await coord._async_update_data()

    async def test_other_errors_are_update_failed(self):
        for error in (KotelApiError("HTTP 404"), KotelResponseError("неожиданный", "dict: {}")):
            coord = coordinator(FakeApi(error=error))
            with self.assertRaises(UpdateFailed) as ctx:
                await coord._async_update_data()
            self.assertNotIsInstance(ctx.exception, ConfigEntryAuthFailed)

    async def test_message_does_not_repeat_the_phrase(self):
        phrase = "Сервер отклонил учётные данные"
        for answer in (("ok", "login_pass_in_db_ok - NO"), ("status", 401)):
            c, _ = client({APP: answer, WEB: answer})
            coord = coordinator(c)
            with self.assertRaises(ConfigEntryAuthFailed) as ctx:
                await coord._async_update_data()
            self.assertEqual(str(ctx.exception).count(phrase), 1, str(ctx.exception))

    async def test_config_entry_is_passed_to_base_class(self):
        entry = SimpleNamespace()
        self.assertIs(coordinator(FakeApi(), entry).config_entry, entry)


class EmptyListTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_list_without_history_is_accepted(self):
        coord = coordinator(FakeApi(devices=[]))
        self.assertEqual(await coord._async_update_data(), {})

    async def test_empty_list_after_devices_is_a_failure_three_times(self):
        api = FakeApi(devices=[CONTR])
        coord = coordinator(api)
        coord.data = await coord._async_update_data()
        api.devices = []
        for _ in range(coordinator_mod.EMPTY_LIST_TOLERANCE):
            with self.assertRaises(UpdateFailed):
                await coord._async_update_data()
        self.assertEqual(await coord._async_update_data(), {})   # дальше — дом пуст

    async def test_devices_back_resets_the_streak(self):
        api = FakeApi(devices=[CONTR])
        coord = coordinator(api)
        coord.data = await coord._async_update_data()
        api.devices = []
        with self.assertRaises(UpdateFailed):
            await coord._async_update_data()
        api.devices = [CONTR]
        coord.data = await coord._async_update_data()
        api.devices = []
        for _ in range(coordinator_mod.EMPTY_LIST_TOLERANCE):
            with self.assertRaises(UpdateFailed):
                await coord._async_update_data()


class FakeCheckApi:
    result = None
    created: list = []

    def __init__(self, **kwargs):
        FakeCheckApi.created.append(kwargs)

    async def get_all_devices(self):
        if isinstance(FakeCheckApi.result, Exception):
            raise FakeCheckApi.result
        return FakeCheckApi.result


class CheckCredentialsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeCheckApi.created = []
        self._orig = config_flow_mod.KotelApiClient
        config_flow_mod.KotelApiClient = FakeCheckApi

    def tearDown(self):
        config_flow_mod.KotelApiClient = self._orig

    async def check(self, result, home_id="H1"):
        FakeCheckApi.result = result
        data = {"url": APP, "login": "u", "password": "p", "home_id": home_id}
        return await config_flow_mod._check_credentials(SimpleNamespace(), data)

    async def test_mapping(self):
        self.assertEqual(await self.check(KotelAuthError("x")), ("invalid_auth", ""))
        self.assertEqual(await self.check(KotelResponseError("x", "dict: {}")),
                         ("unexpected_response", "dict: {}"))
        self.assertEqual(await self.check(KotelTimeoutError("x")), ("no_response", ""))
        self.assertEqual(await self.check(KotelApiError("x")), ("cannot_connect", ""))
        self.assertEqual(await self.check([]), ("no_devices", ""))
        self.assertEqual(await self.check([], home_id=""), ("no_home_id", ""))
        self.assertEqual(await self.check(DEVICES), (None, ""))

    async def test_uses_home_assistant_session(self):
        await self.check(DEVICES)
        self.assertIsNotNone(FakeCheckApi.created[0]["session"])


def make_flow(entry_data=None):
    calls = SimpleNamespace(update=[], reload=[], update_result=True)
    entry = SimpleNamespace(entry_id="E1", data={"url": APP, "login": "u", "password": "old",
                                                  "home_id": "H1", **(entry_data or {})})

    def update_entry(entry_, data=None, options=None):
        calls.update.append(data)
        return calls.update_result

    flow = config_flow_mod.KotelConfigFlow()
    flow.hass = SimpleNamespace(config_entries=SimpleNamespace(
        async_update_entry=update_entry,
        async_schedule_reload=lambda entry_id: calls.reload.append(entry_id)))
    flow._get_reauth_entry = lambda: entry
    flow.async_show_form = lambda **kw: {"type": "form", **kw}
    flow.async_create_entry = lambda **kw: {"type": "create", **kw}
    flow.async_abort = lambda **kw: {"type": "abort", **kw}

    async def set_unique_id(value):
        return None

    flow.async_set_unique_id = set_unique_id
    flow._abort_if_unique_id_configured = lambda: None
    flow._async_current_entries = lambda: []
    return flow, entry, calls


class ReauthFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeCheckApi.created = []
        self._orig = config_flow_mod.KotelApiClient
        config_flow_mod.KotelApiClient = FakeCheckApi

    def tearDown(self):
        config_flow_mod.KotelApiClient = self._orig

    async def test_reauth_shows_confirm_form_with_login(self):
        flow, _, _ = make_flow()
        result = await flow.async_step_reauth({"login": "u"})
        self.assertEqual((result["type"], result["step_id"]), ("form", "reauth_confirm"))
        self.assertEqual(result["description_placeholders"]["login"], "u")

    async def test_wrong_password_keeps_form_and_changes_nothing(self):
        FakeCheckApi.result = KotelAuthError("Wrong password")
        flow, _, calls = make_flow()
        result = await flow.async_step_reauth_confirm({"password": "bad"})
        self.assertEqual(result["errors"], {"base": "invalid_auth"})
        self.assertEqual((calls.update, calls.reload), ([], []))

    async def test_unexpected_response_is_shown(self):
        FakeCheckApi.result = KotelResponseError("x", "dict: {'a': 1}")
        flow, _, _ = make_flow()
        result = await flow.async_step_reauth_confirm({"password": "p"})
        self.assertEqual(result["errors"], {"base": "unexpected_response"})
        self.assertEqual(result["description_placeholders"]["response"], "dict: {'a': 1}")

    async def test_new_password_is_saved_and_entry_is_reloaded_explicitly(self):
        FakeCheckApi.result = DEVICES
        flow, entry, calls = make_flow()
        result = await flow.async_step_reauth_confirm({"password": "new"})
        self.assertEqual(result, {"type": "abort", "reason": "reauth_successful"})
        self.assertEqual(calls.update, [{**entry.data, "password": "new"}])
        self.assertEqual(calls.update[0]["login"], "u")              # логин не менялся
        # слушателя может не быть (первый опрос закончился отказом) — перезагрузка явная
        self.assertEqual(calls.reload, ["E1"])

    async def test_unchanged_password_still_reloads_the_entry(self):
        FakeCheckApi.result = DEVICES
        flow, _, calls = make_flow()
        calls.update_result = False                                  # запись не изменилась
        await flow.async_step_reauth_confirm({"password": "old"})
        self.assertEqual(calls.reload, ["E1"])

    async def test_user_step_reports_auth_error_and_creates_nothing(self):
        FakeCheckApi.result = KotelAuthError("HTTP 401")
        flow, _, _ = make_flow()
        user_input = {"url": APP, "login": "u", "password": "bad", "home_id": "H1"}
        result = await flow.async_step_user(user_input)
        self.assertEqual(result["errors"], {"base": "invalid_auth"})
        self.assertEqual(result["description_placeholders"], {"response": ""})

    async def test_user_step_creates_entry_without_closing_shared_session(self):
        FakeCheckApi.result = DEVICES                                # у FakeCheckApi нет close()
        flow, _, _ = make_flow()
        result = await flow.async_step_user(
            {"url": APP + "/", "login": "u", "password": "p", "home_id": "H1"})
        self.assertEqual(result["type"], "create")
        self.assertEqual(result["data"]["url"], APP)


class StringsTests(unittest.TestCase):
    def test_strings(self):
        config = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))["config"]
        step = config["step"]["reauth_confirm"]
        self.assertIn("{login}", step["description"])
        self.assertIn("password", step["data"])
        self.assertIn("invalid_auth", config["error"])
        self.assertIn("no_response", config["error"])
        self.assertIn("{response}", config["error"]["unexpected_response"])
        self.assertIn("reauth_successful", config["abort"])


if __name__ == "__main__":
    unittest.main()
