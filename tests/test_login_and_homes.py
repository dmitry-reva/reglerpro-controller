"""Тесты находок 81 и 82: проверка логина через login_user и выбор дома из списка get_data_sets."""

from __future__ import annotations

import asyncio
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

api_mod = importlib.import_module(f"{PKG}.api")
config_flow_mod = importlib.import_module(f"{PKG}.config_flow")
aiohttp = importlib.import_module("aiohttp")
KotelApiError = api_mod.KotelApiError
KotelAuthError = api_mod.KotelAuthError
KotelResponseError = api_mod.KotelResponseError
KotelTimeoutError = api_mod.KotelTimeoutError

APP = "https://app.reglerpro.ru"
HOME_A, HOME_B = "1001886061872", "-1002211493629"      # номера домов бывают отрицательными


def make_client(login="user", password="s3cretPass"):
    return api_mod.KotelApiClient(APP, login, password, "", session=object())


def scripted(client, answer):
    """Подменить _post: вернуть answer и запомнить запрос."""
    seen = {}

    async def fake_post(payload, **kwargs):
        seen.update(payload=payload, kwargs=kwargs)
        return answer

    client._post = fake_post
    return seen


class LoginUserTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_and_payload(self):
        client = make_client()
        seen = scripted(client, {"bool": True, "text": "", "extra": 1})
        self.assertEqual((await client.login_user())["extra"], 1)
        payload = seen["payload"]
        self.assertEqual(payload["action"], "login_user")
        self.assertEqual((payload["login"], payload["password"]), ("user", "s3cretPass"))
        self.assertNotIn("userId", payload)                       # номер дома не нужен
        self.assertTrue(seen["kwargs"]["idempotent"])
        self.assertIs(seen["kwargs"]["expect"], dict)

    async def test_rejected_with_server_text(self):
        client = make_client()
        scripted(client, {"bool": False, "text": "Неверный пароль"})
        with self.assertRaises(KotelAuthError) as ctx:
            await client.login_user()
        self.assertEqual(ctx.exception.server_text, "Неверный пароль")
        self.assertIn("Неверный пароль", str(ctx.exception))

    async def test_server_text_is_cleaned(self):
        client = make_client()
        scripted(client, {"bool": False,
                          "text": "<b>Неверный</b> пароль<br>\n  для s3cretPass  " + "я" * 400})
        with self.assertRaises(KotelAuthError) as ctx:
            await client.login_user()
        text = ctx.exception.server_text
        self.assertNotIn("<", text)
        self.assertNotIn("s3cretPass", text)
        self.assertTrue(text.startswith("Неверный пароль для ***"))
        self.assertLessEqual(len(text), 160)

    async def test_rejected_without_text(self):
        for answer in ({"bool": False}, {"bool": 0, "text": None}, {"bool": "", "text": "  "}):
            client = make_client()
            scripted(client, answer)
            with self.assertRaises(KotelAuthError) as ctx:
                await client.login_user()
            self.assertEqual(ctx.exception.server_text, "", answer)

    async def test_truthiness_follows_the_app(self):
        # в приложении: !data.bool — строка "0" считается истинной
        client = make_client()
        scripted(client, {"bool": "0"})
        self.assertEqual((await client.login_user())["bool"], "0")

    async def test_answer_without_bool_is_unexpected_not_a_rejection(self):
        client = make_client()
        scripted(client, {"status": "maintenance"})
        with self.assertRaises(KotelResponseError) as ctx:
            await client.login_user()
        self.assertNotIsInstance(ctx.exception, KotelAuthError)
        self.assertIn("maintenance", ctx.exception.snippet)


class FakeResponse:
    def __init__(self, payload):
        self.status, self._payload = 200, payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self._payload


class FakeSession:
    closed = False

    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(json)
        return FakeResponse(self.payload)


class LoginThroughRealTransportTests(unittest.IsolatedAsyncioTestCase):
    async def run_login(self, payload):
        session = FakeSession(payload)
        client = api_mod.KotelApiClient(APP, "user", "pass", "", session=session, fallback_urls=[])
        return await client.login_user()

    async def test_exact_server_string_is_an_auth_error(self):
        with self.assertRaises(KotelAuthError):
            await self.run_login("login_pass_in_db_ok - NO")

    async def test_list_instead_of_dict_is_a_response_error(self):
        with self.assertRaises(KotelResponseError):
            await self.run_login([])

    async def test_normal_answer(self):
        self.assertTrue((await self.run_login({"bool": True}))["bool"])


class GetHomesTests(unittest.IsolatedAsyncioTestCase):
    async def homes(self, answer):
        client = make_client()
        seen = scripted(client, answer)
        result = await client.get_homes()
        return result, seen

    async def test_dict_of_homes_and_payload(self):
        result, seen = await self.homes({"homes": {HOME_A: "Дом", HOME_B: "Дача"}, "etc": 3})
        self.assertEqual(result, {HOME_A: "Дом", HOME_B: "Дача"})
        payload = seen["payload"]
        self.assertEqual(payload["action"], "get_data_sets")
        self.assertEqual(payload["action_value1"], "")            # список домов, а не данные одного дома
        self.assertNotIn("userId", payload)
        self.assertTrue(seen["kwargs"]["idempotent"])

    async def test_no_homes(self):
        for empty in ([], {}, None, ""):
            result, _ = await self.homes({"homes": empty})
            self.assertEqual(result, {}, repr(empty))

    async def test_names_are_cleaned_and_never_empty(self):
        result, _ = await self.homes({"homes": {HOME_A: "<b>Дом</b>  у леса", HOME_B: None, "5": 7,
                                                 "6": "x" * 300}})
        self.assertEqual(result[HOME_A], "Дом у леса")
        self.assertEqual(result[HOME_B], HOME_B)                  # нет названия — номер
        self.assertEqual(result["5"], "7")
        self.assertLessEqual(len(result["6"]), 80)

    async def test_missing_homes_key_is_unexpected(self):
        with self.assertRaises(KotelResponseError):
            await self.homes({"etc": 3})

    async def test_non_empty_list_is_unexpected(self):
        with self.assertRaises(KotelResponseError):
            await self.homes({"homes": ["Дом"]})

    async def test_rejection_is_an_auth_error(self):
        with self.assertRaises(KotelAuthError) as ctx:
            await self.homes({"bool": False, "text": "Нет такого пользователя"})
        self.assertEqual(ctx.exception.server_text, "Нет такого пользователя")


class FakeApi:
    """Клиент API для окон: ответы задаются классом, запросы записываются."""

    login_result = None
    homes_result = {HOME_A: "Дом"}
    devices_result = [{"id_name": "c1"}]
    log: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def login_user(self):
        FakeApi.log.append("login_user")
        if isinstance(FakeApi.login_result, Exception):
            raise FakeApi.login_result
        return {"bool": True}

    async def get_homes(self):
        FakeApi.log.append("get_homes")
        if isinstance(FakeApi.homes_result, Exception):
            raise FakeApi.homes_result
        return FakeApi.homes_result

    async def get_all_devices(self):
        FakeApi.log.append(f"get_all_devices:{self.kwargs.get('home_id')}")
        if isinstance(FakeApi.devices_result, Exception):
            raise FakeApi.devices_result
        return FakeApi.devices_result


def make_flow(entries=()):
    flow = config_flow_mod.KotelConfigFlow()
    flow.hass = SimpleNamespace(config_entries=SimpleNamespace(
        async_update_entry=lambda entry, data=None: True,
        async_schedule_reload=lambda entry_id: None))
    flow.async_show_form = lambda **kw: {"type": "form", **kw}
    flow.async_create_entry = lambda **kw: {"type": "create", **kw}
    flow.async_abort = lambda **kw: {"type": "abort", **kw}
    flow.unique_ids = []

    async def set_unique_id(value):
        flow.unique_ids.append(value)

    flow.async_set_unique_id = set_unique_id
    flow._abort_if_unique_id_configured = lambda: None
    flow._async_current_entries = lambda: list(entries)
    return flow


def user_input(**extra):
    return {"url": APP + "/", "login": "User", "password": "p", "home_id": "", **extra}


class UserFlowBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeApi.login_result = None
        FakeApi.homes_result = {HOME_A: "Дом"}
        FakeApi.devices_result = [{"id_name": "c1"}]
        FakeApi.log = []
        self._orig = config_flow_mod.KotelApiClient
        config_flow_mod.KotelApiClient = FakeApi

    def tearDown(self):
        config_flow_mod.KotelApiClient = self._orig


class UserStepTests(UserFlowBase):
    async def test_rejected_login_shows_the_server_text(self):
        FakeApi.login_result = KotelAuthError("x", server_text="Пользователь не подтверждён")
        result = await make_flow().async_step_user(user_input())
        self.assertEqual(result["errors"], {"base": "login_rejected"})
        self.assertEqual(result["description_placeholders"]["response"], "Пользователь не подтверждён")
        self.assertEqual(FakeApi.log, ["login_user"])             # дальше не идём

    async def test_rejected_login_without_text_is_invalid_auth(self):
        FakeApi.login_result = KotelAuthError("HTTP 401")
        result = await make_flow().async_step_user(user_input())
        self.assertEqual(result["errors"], {"base": "invalid_auth"})

    async def test_other_login_errors(self):
        cases = ((KotelResponseError("x", "dict: {}"), "unexpected_response"),
                 (KotelTimeoutError("x"), "no_response"), (KotelApiError("x"), "cannot_connect"))
        for error, key in cases:
            FakeApi.login_result = error
            result = await make_flow().async_step_user(user_input())
            self.assertEqual(result["errors"], {"base": key})

    async def test_single_home_is_chosen_automatically(self):
        flow = make_flow()
        result = await flow.async_step_user(user_input())
        self.assertEqual(result["type"], "create")
        self.assertEqual(result["data"]["home_id"], HOME_A)
        self.assertEqual(result["data"]["url"], APP)              # адрес нормализован
        self.assertEqual(result["title"], "ReglerPro (Дом)")
        self.assertEqual(flow.unique_ids, [f"user|{HOME_A}"])
        self.assertEqual(FakeApi.log, ["login_user", "get_homes", f"get_all_devices:{HOME_A}"])

    async def test_several_homes_lead_to_a_choice(self):
        FakeApi.homes_result = {HOME_A: "Дом", HOME_B: "Дача"}
        flow = make_flow()
        result = await flow.async_step_user(user_input())
        self.assertEqual((result["type"], result["step_id"]), ("form", "select_home"))
        self.assertEqual(FakeApi.log, ["login_user", "get_homes"])
        result = await flow.async_step_select_home({"home_id": HOME_B})
        self.assertEqual(result["type"], "create")
        self.assertEqual(result["data"]["home_id"], HOME_B)
        self.assertEqual(result["title"], "ReglerPro (Дача)")
        self.assertEqual(result["data"]["login"], "User")         # введённые данные сохранены

    async def test_known_home_id_is_accepted(self):
        FakeApi.homes_result = {HOME_A: "Дом", HOME_B: "Дача"}
        result = await make_flow().async_step_user(user_input(home_id=f" {HOME_B} "))
        self.assertEqual(result["type"], "create")
        self.assertEqual(result["data"]["home_id"], HOME_B)

    async def test_foreign_home_id_is_refused_without_asking_for_devices(self):
        FakeApi.homes_result = {HOME_A: "Дом", HOME_B: "Дача"}
        result = await make_flow().async_step_user(user_input(home_id="1000000000009"))
        self.assertEqual(result["errors"], {"base": "home_not_found"})
        self.assertIn("Дом (1001886061872)", result["description_placeholders"]["homes"])
        self.assertFalse([entry for entry in FakeApi.log if entry.startswith("get_all_devices")])

    async def test_account_without_homes(self):
        FakeApi.homes_result = {}
        result = await make_flow().async_step_user(user_input())
        self.assertEqual(result["errors"], {"base": "no_homes"})

    async def test_unavailable_home_list_falls_back_to_manual_entry(self):
        FakeApi.homes_result = KotelResponseError("x", "dict: {}")
        flow = make_flow()
        result = await flow.async_step_user(user_input())
        self.assertEqual(result["errors"], {"base": "no_home_id"})        # просим ввести номер
        result = await flow.async_step_user(user_input(home_id=HOME_A))
        self.assertEqual(result["type"], "create")                        # прежний путь работает
        self.assertEqual(result["title"], f"ReglerPro ({HOME_A})")

    async def test_duplicate_account_aborts_before_the_device_check(self):
        entry = SimpleNamespace(data={"login": "user", "home_id": HOME_A})
        result = await make_flow([entry]).async_step_user(user_input())
        self.assertEqual(result, {"type": "abort", "reason": "already_configured"})
        self.assertFalse([entry for entry in FakeApi.log if entry.startswith("get_all_devices")])

    async def test_empty_device_list_is_reported_on_the_selection_form(self):
        FakeApi.homes_result = {HOME_A: "Дом", HOME_B: "Дача"}
        flow = make_flow()
        await flow.async_step_user(user_input())
        FakeApi.devices_result = []
        result = await flow.async_step_select_home({"home_id": HOME_A})
        self.assertEqual((result["step_id"], result["errors"]), ("select_home", {"base": "no_devices"}))

    async def test_selection_form_lists_name_and_number(self):
        FakeApi.homes_result = {HOME_A: "Дом", HOME_B: "Дача"}
        flow = make_flow()
        await flow.async_step_user(user_input())
        result = await flow.async_step_select_home()
        self.assertEqual(result["step_id"], "select_home")


class ReauthUsesLoginTests(UserFlowBase):
    def make(self):
        flow = make_flow()
        entry = SimpleNamespace(entry_id="E1", data={"url": APP, "login": "u", "password": "old",
                                                      "home_id": HOME_A})
        flow._get_reauth_entry = lambda: entry
        flow.updates, flow.reloads = [], []
        flow.hass.config_entries.async_update_entry = lambda e, data=None: flow.updates.append(data) or True
        flow.hass.config_entries.async_schedule_reload = lambda entry_id: flow.reloads.append(entry_id)
        return flow

    async def test_only_the_login_is_checked(self):
        flow = self.make()
        result = await flow.async_step_reauth_confirm({"password": "new"})
        self.assertEqual(result, {"type": "abort", "reason": "reauth_successful"})
        self.assertEqual(FakeApi.log, ["login_user"])             # без запроса списка устройств
        self.assertEqual(flow.updates[0]["password"], "new")
        self.assertEqual(flow.reloads, ["E1"])

    async def test_wrong_password_and_server_text(self):
        flow = self.make()
        FakeApi.login_result = KotelAuthError("x")
        result = await flow.async_step_reauth_confirm({"password": "bad"})
        self.assertEqual(result["errors"], {"base": "invalid_auth"})
        FakeApi.login_result = KotelAuthError("x", server_text="Аккаунт заблокирован")
        result = await flow.async_step_reauth_confirm({"password": "bad"})
        self.assertEqual(result["errors"], {"base": "login_rejected"})
        self.assertEqual(result["description_placeholders"]["response"], "Аккаунт заблокирован")
        self.assertEqual((flow.updates, flow.reloads), ([], []))


class HelperTests(UserFlowBase):
    async def test_map_error(self):
        mapping = config_flow_mod._map_error
        self.assertEqual(mapping(KotelAuthError("x")), ("invalid_auth", ""))
        self.assertEqual(mapping(KotelAuthError("x", "текст")), ("login_rejected", "текст"))
        self.assertEqual(mapping(KotelResponseError("x", "s")), ("unexpected_response", "s"))
        self.assertEqual(mapping(KotelTimeoutError("x")), ("no_response", ""))
        self.assertEqual(mapping(KotelApiError("x")), ("cannot_connect", ""))

    async def test_load_homes_swallows_api_errors(self):
        FakeApi.homes_result = KotelApiError("HTTP 500")
        data = {"url": APP, "login": "u", "password": "p"}
        self.assertIsNone(await config_flow_mod._load_homes(SimpleNamespace(), data))

    def test_homes_text_is_short(self):
        homes = {str(i): f"Дом {i}" for i in range(9)}
        text = config_flow_mod._homes_text(homes)
        self.assertIn("Дом 0 (0)", text)
        self.assertIn("ещё 4", text)


class StringsTests(unittest.TestCase):
    def test_strings_cover_the_new_steps(self):
        config = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))["config"]
        self.assertEqual(set(config["step"]["select_home"]["data"]), {"home_id"})
        self.assertIn("home_id", config["step"]["user"]["data_description"])
        for key in ("login_rejected", "no_homes", "home_not_found", "no_home_id"):
            self.assertIn(key, config["error"], key)
        self.assertIn("{response}", config["error"]["login_rejected"])
        self.assertIn("{homes}", config["error"]["home_not_found"])

    def test_additions_have_matching_keys(self):
        root = COMPONENT.parent.parent / "translations_additions"
        if not root.is_dir():
            self.skipTest("translations_additions уже слит с переводами и удалён")
        en = json.loads((root / "en.json").read_text(encoding="utf-8"))
        ru = json.loads((root / "ru.json").read_text(encoding="utf-8"))
        for key in ("login_rejected", "no_homes", "home_not_found"):
            self.assertIn(key, en["config"]["error"])
            self.assertIn(key, ru["config"]["error"])
        self.assertIn("select_home", en["config"]["step"])
        self.assertIn("select_home", ru["config"]["step"])


if __name__ == "__main__":
    unittest.main()
