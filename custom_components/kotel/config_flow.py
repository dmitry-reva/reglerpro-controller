"""Config flow для интеграции Kotel."""

import asyncio
import logging
import os
import re
import shutil
from urllib.parse import urlparse

import voluptuous as vol  # pyright: ignore[reportMissingImports]
from homeassistant import config_entries  # pyright: ignore[reportMissingImports]
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.core import callback  # pyright: ignore[reportMissingImports]
from homeassistant.exceptions import HomeAssistantError  # pyright: ignore[reportMissingImports]

from homeassistant.helpers import config_validation as cv  # pyright: ignore[reportMissingImports]
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    FileSelector,
    FileSelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.translation import async_get_translations

from .const import (
    CONF_FALLBACK_URLS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_URL,
    DOMAIN,
    KNOWN_SERVERS,
)
from .profiles import EXPORT_DIR, KIND_LABELS
from .api import (
    KotelApiClient,
    KotelApiError,
    KotelAuthError,
    KotelResponseError,
    KotelTimeoutError,
    build_servers,
)

STEP_USER_DATA_SCHEMA = vol.Schema({
    # основной сервер: из списка или свой адрес (у серверов из списка клиент API
    # при сбое сам переходит на остальные — логин, пароль и дом у них общие)
    vol.Required("url", default=DEFAULT_URL): SelectSelector(
        SelectSelectorConfig(
            options=list(KNOWN_SERVERS),
            custom_value=True,
            mode=SelectSelectorMode.DROPDOWN,
        )
    ),
    vol.Required("login"): str,
    vol.Required("password"): str,
    vol.Optional("home_id", default=""): str,
})


_LOGGER = logging.getLogger(__name__)

ON_CONFLICT_OPTIONS = ["rename", "overwrite", "skip"]   # подписи — selector.on_conflict


def _client(hass, data) -> KotelApiClient:
    return KotelApiClient(
        base_url=data["url"],
        login=data["login"],
        password=data["password"],
        home_id=(data.get("home_id") or "").strip(),
        session=async_get_clientsession(hass),
    )


def _map_error(err: KotelApiError) -> tuple[str, str]:
    """Ошибка API -> (ключ ошибки окна, выдержка из ответа сервера для сообщения)."""
    if isinstance(err, KotelAuthError):
        # пояснение сервера (login_user.text) показываем, если оно есть
        return ("login_rejected", err.server_text) if err.server_text else ("invalid_auth", "")
    if isinstance(err, KotelResponseError):
        return "unexpected_response", err.snippet
    if isinstance(err, KotelTimeoutError):
        # при неверном номере дома у пользователя без домов сервер не отвечает вовсе
        return "no_response", ""
    return "cannot_connect", ""


async def _check_login(hass, data) -> tuple[str | None, str]:
    """Проверить логин и пароль запросом login_user (номер дома не нужен).

    Возвращает (ключ ошибки или None, выдержка из ответа сервера для сообщения).
    """
    try:
        await _client(hass, data).login_user()
    except KotelApiError as err:
        return _map_error(err)
    return None, ""


async def _load_homes(hass, data) -> dict[str, str] | None:
    """Дома учётной записи {номер: название}; None — список получить не удалось.

    Тогда номер дома остаётся вводить вручную (прежний порядок), а принадлежность
    дома пользователю не проверяется.
    """
    try:
        return await _client(hass, data).get_homes()
    except KotelApiError as err:
        _LOGGER.debug("Список домов не получен: %s", err)
        return None


async def _check_credentials(hass, data) -> tuple[str | None, str]:
    """Проверить, что по этому номеру дома есть устройства (запрос get_all_dev).

    Возвращает (ключ ошибки или None, выдержка из ответа сервера для сообщения).
    """
    home_id = (data.get("home_id") or "").strip()
    try:
        devices = await _client(hass, data).get_all_devices()
    except KotelApiError as err:
        return _map_error(err)
    if devices:
        return None, ""
    return ("no_devices" if home_id else "no_home_id"), ""


def _homes_text(homes: dict[str, str], limit: int = 5) -> str:
    items = [f"{name} ({home_id})" for home_id, name in homes.items()]
    text = ", ".join(items[:limit])
    return text + (f", ещё {len(items) - limit}" if len(items) > limit else "")


def _is_valid_server_url(url: str) -> bool:
    """Адрес сервера: http(s)://хост, без пробелов, параметров и фрагмента."""
    if not url or any(ch.isspace() for ch in url):
        return False
    parsed = urlparse(url)
    return (parsed.scheme in ("http", "https") and bool(parsed.netloc)
            and not parsed.query and not parsed.fragment)


def _clean_server_urls(primary: str, fallbacks) -> tuple[str, list[str]]:
    """Убрать пробелы и «/» на конце, дубли (без учёта регистра) и основной адрес из запасных."""
    primary = (primary or "").strip().rstrip("/")
    seen = {primary.lower()}
    result: list[str] = []
    for url in fallbacks or []:
        url = (url or "").strip().rstrip("/")
        if url and url.lower() not in seen:
            seen.add(url.lower())
            result.append(url)
    return primary, result


async def _kind_labels(hass) -> dict[str, str]:
    """Подписи типов профилей на языке интерфейса (selector.profile_kind).

    Если перевода нет, используются русские подписи из KIND_LABELS.
    """
    translations = await async_get_translations(
        hass, hass.config.language, "selector", [DOMAIN])
    prefix = f"component.{DOMAIN}.selector.profile_kind.options."
    return {
        kind: translations.get(prefix + kind) or label
        for kind, label in KIND_LABELS.items()
    }


def _import_choices_for(profiles: list[dict], labels: dict[str, str]) -> dict[str, str]:
    """{"тип|имя": подпись} для выбора профилей из файла."""
    return {
        f"{p['kind']}|{p['name']}": f"{labels[p['kind']]} · {p['name']}"
        for p in profiles
    }


def _sanitize_filename(name: str) -> str:
    """Оставить в имени только символы, допустимые для файлов экспорта."""
    clean = re.sub(r"[^\w.\- ]+", "_", name).strip(" .")
    if not clean:
        clean = "kotel_profiles"
    return clean[:80]


def _save_uploaded_json(hass, file_id: str, directory: str) -> str:
    """Сохранить загруженный файл в каталог kotel_profiles; вернуть имя файла.

    Работает в потоке исполнителя: process_uploaded_file требует этого.
    """
    os.makedirs(directory, exist_ok=True)
    with process_uploaded_file(hass, file_id) as file_path:
        ext = file_path.suffix if file_path.suffix.lower() == ".json" else ".json"
        name = f"{_sanitize_filename(file_path.stem)}{ext}"
        dest = os.path.join(directory, name)
        counter = 2
        while os.path.exists(dest):
            dest = os.path.join(directory, f"{_sanitize_filename(file_path.stem)}_{counter}{ext}")
            counter += 1
        shutil.copy2(file_path, dest)
        return os.path.basename(dest)


def _remove_json_file(directory: str, filename: str) -> None:
    """Удалить файл из каталога kotel_profiles (игнорируя отсутствие)."""
    path = os.path.join(directory, os.path.basename(filename))
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


class KotelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow для Kotel."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._pending: dict = {}            # введённые данные, пока выбирается дом
        self._homes: dict[str, str] | None = None

    async def async_step_user(self, user_input=None):
        """Первый шаг: сервер, логин и пароль; номер дома можно не вводить."""
        errors = {}
        placeholders = {"response": "", "homes": ""}

        if user_input is not None:
            home_id = (user_input.get("home_id") or "").strip()
            data = {**user_input, "url": user_input["url"].strip().rstrip("/"),
                    "home_id": home_id}

            # 1. логин и пароль (login_user — без номера дома)
            error, response = await _check_login(self.hass, data)
            if error is None:
                # 2. дома учётной записи (для выбора и проверки принадлежности)
                homes = await _load_homes(self.hass, data)
                if home_id:
                    if homes is not None and home_id not in homes:
                        error = "home_not_found"
                        placeholders["homes"] = _homes_text(homes) or "—"
                    else:
                        result, error, response = await self._try_create(data, homes)
                        if result is not None:
                            return result
                elif homes is None:
                    error = "no_home_id"            # список домов недоступен: ввести вручную
                elif not homes:
                    error = "no_homes"
                elif len(homes) == 1:
                    data["home_id"] = next(iter(homes))
                    result, error, response = await self._try_create(data, homes)
                    if result is not None:
                        return result
                else:
                    self._pending, self._homes = data, homes
                    return await self.async_step_select_home()
            errors["base"] = error
            placeholders["response"] = response

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_select_home(self, user_input=None):
        """Выбор дома из списка домов учётной записи."""
        errors = {}
        placeholders = {"response": ""}
        homes = self._homes or {}
        if user_input is not None:
            data = {**self._pending, "home_id": user_input["home_id"]}
            result, error, response = await self._try_create(data, homes)
            if result is not None:
                return result
            errors["base"] = error
            placeholders["response"] = response
        options = [{"value": home_id, "label": f"{name} ({home_id})"}
                   for home_id, name in homes.items()]
        return self.async_show_form(
            step_id="select_home",
            data_schema=vol.Schema({
                vol.Required("home_id"): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)),
            }),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _try_create(self, data: dict, homes: dict[str, str] | None):
        """Создать запись для выбранного дома.

        Возвращает (результат потока, None, "") или (None, ключ ошибки, выдержка).
        """
        home_id = data["home_id"]
        # Одна запись на связку «логин + дом»: на разных серверах учётная запись одна и
        # та же, поэтому адрес сервера в идентификатор не входит.
        await self.async_set_unique_id(f"{data['login'].strip().casefold()}|{home_id}")
        self._abort_if_unique_id_configured()
        if self._account_already_configured(data["login"], home_id):
            return self.async_abort(reason="already_configured"), None, ""
        error, response = await _check_credentials(self.hass, data)
        if error is not None:
            return None, error, response
        name = (homes or {}).get(home_id)
        return self.async_create_entry(
            title=f"ReglerPro ({name or home_id or data['login']})",
            data=data,
        ), None, ""

    # --- повторная авторизация -------------------------------------------------
    async def async_step_reauth(self, entry_data):
        """Сервер отклонил сохранённые учётные данные."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        entry = self._get_reauth_entry()
        errors = {}
        placeholders = {"login": entry.data.get("login", ""), "response": ""}
        if user_input is not None:
            data = {**entry.data, "password": user_input["password"]}
            error, response = await _check_login(self.hass, data)
            if error is None:
                # Перезагружаем запись явно, а не через слушатель изменений: если
                # первый опрос закончился отказом, слушателя ещё нет, а после отказа
                # при работе опрос остановлен. Слушатель на смену пароля не реагирует
                # (см. _reload_key в __init__.py), поэтому перезагрузка одна.
                self.hass.config_entries.async_update_entry(entry, data=data)
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            errors["base"] = error
            placeholders["response"] = response
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required("password"): str}),
            errors=errors,
            description_placeholders=placeholders,
        )

    def _account_already_configured(self, login: str, home_id: str) -> bool:
        """Есть ли запись с этой учётной записью (в том числе созданная до 1.4.7,
        когда в unique_id входил адрес сервера)."""
        login = login.strip().casefold()
        for entry in self._async_current_entries():
            if ((entry.data.get("login") or "").strip().casefold() == login
                    and (entry.data.get("home_id") or "").strip() == home_id):
                return True
        return False

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return KotelOptionsFlow()


class KotelOptionsFlow(config_entries.OptionsFlow):
    """Опции интеграции: параметры, экспорт и импорт профилей, удаление профилей."""

    def __init__(self, config_entry=None) -> None:
        super().__init__()
        self._import_file = None
        self._import_choices = {}
        self._new_temperature_sensor_id = None

    @property
    def _manager(self):
        return self.hass.data[DOMAIN]["_profiles"]

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "add_temperature_sensor",
                "settings",
                "servers",
                "export",
                "import_file",
                "upload",
                "delete",
            ],
        )

    # --- параметры -----------------------------------------------------------
    async def async_step_settings(self, user_input=None):
        if user_input is not None:
            # другие параметры (например, список серверов) не должны сбрасываться
            return self.async_create_entry(
                title="", data={**self.config_entry.options, **user_input})

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(int, vol.Range(min=10, max=300)),
            }),
        )

    # --- серверы -------------------------------------------------------------
    async def async_step_servers(self, user_input=None):
        """Основной и запасные серверы (учётная запись одна и та же на всех)."""
        entry = self.config_entry
        errors = {}
        placeholders = {"server": "", "error": ""}
        primary = entry.data.get("url") or DEFAULT_URL
        stored = entry.options.get(CONF_FALLBACK_URLS)
        fallbacks = list(stored) if stored is not None else build_servers(primary, None)[1:]

        if user_input is not None:
            primary, fallbacks = _clean_server_urls(
                user_input["url"], user_input.get(CONF_FALLBACK_URLS))
            urls = [primary, *fallbacks]
            invalid = next((url for url in urls if not _is_valid_server_url(url)), None)
            if invalid is not None:
                errors["base"] = "invalid_url"
                placeholders["server"] = invalid
            elif not user_input.get("skip_check"):
                failure = await self._check_servers(urls)
                if failure is not None:
                    errors["base"] = "server_check_failed"
                    placeholders["server"], placeholders["error"] = failure
            if not errors:
                return self._save_servers(primary, fallbacks)

        selector_config = dict(
            options=list(KNOWN_SERVERS), custom_value=True, mode=SelectSelectorMode.DROPDOWN)
        return self.async_show_form(
            step_id="servers",
            data_schema=vol.Schema({
                vol.Required("url", default=primary): SelectSelector(
                    SelectSelectorConfig(**selector_config)),
                # suggested_value, а не default: иначе список нельзя очистить (отключить переход)
                vol.Optional(
                    CONF_FALLBACK_URLS, description={"suggested_value": fallbacks},
                ): SelectSelector(SelectSelectorConfig(multiple=True, **selector_config)),
                vol.Optional("skip_check", default=False): bool,
            }),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _check_servers(self, urls: list[str]):
        """Проверить каждый сервер отдельно под этой учётной записью.

        Возвращает None, если все ответили списком устройств, иначе (адрес, причина).
        """
        data = self.config_entry.data
        session = async_get_clientsession(self.hass)

        async def check(url):
            api = KotelApiClient(
                base_url=url, login=data["login"], password=data["password"],
                home_id=(data.get("home_id") or "").strip(),
                session=session, fallback_urls=[],     # именно этот сервер, без переходов
            )
            try:
                devices = await api.get_all_devices()
            except KotelApiError as err:
                return url, str(err)
            return None if devices else (url, "список устройств пуст")

        results = await asyncio.gather(*(check(url) for url in urls))
        return next((result for result in results if result), None)

    def _save_servers(self, primary: str, fallbacks: list[str]):
        entry = self.config_entry
        options = {**entry.options, CONF_FALLBACK_URLS: fallbacks}
        data = entry.data if primary == entry.data.get("url") else {**entry.data, "url": primary}
        # одним обновлением: слушатель записи перезагружает интеграцию один раз
        self.hass.config_entries.async_update_entry(entry, data=data, options=options)
        return self.async_create_entry(title="", data=options)

    async def async_step_add_temperature_sensor(self, user_input=None):
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is None:                      # запись не загружена: клиента API нет
            return self.async_abort(reason="not_loaded")
        errors = {}
        placeholders = {"error": ""}
        configured_home_id = (self.config_entry.data.get("home_id") or "").strip()

        if user_input is not None:
            home_id = configured_home_id or user_input.get("home_id", "").strip()
            if not home_id:
                errors["base"] = "home_id_required"
            elif not user_input["confirm"]:
                errors["base"] = "confirmation_required"
            else:
                api = runtime.api
                try:
                    sensor_id = await api.add_temperature_sensor(home_id)
                except KotelApiError as err:
                    errors["base"] = "add_sensor_failed"
                    placeholders["error"] = str(err)
                else:
                    self._new_temperature_sensor_id = sensor_id
                    return await self.async_step_add_temperature_sensor_result()

        schema = {}
        if not configured_home_id:
            schema[vol.Required("home_id")] = str
        schema[vol.Required("confirm", default=False)] = bool
        return self.async_show_form(
            step_id="add_temperature_sensor",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_add_temperature_sensor_result(self, user_input=None):
        if user_input is not None:
            self._new_temperature_sensor_id = None
            return await self.async_step_init()

        if self._new_temperature_sensor_id is None:
            return await self.async_step_init()

        return self.async_show_form(
            step_id="add_temperature_sensor_result",
            data_schema=vol.Schema({
                vol.Required(
                    "sensor_id",
                    default=self._new_temperature_sensor_id,
                ): str,
            }),
        )

    # --- экспорт профилей ----------------------------------------------------
    async def async_step_export(self, user_input=None):
        choices = self._manager.profile_choices(await _kind_labels(self.hass))
        if not choices:
            return self.async_abort(reason="no_profiles")
        errors = {}
        if user_input is not None:
            try:
                result = await self._manager.async_export(
                    user_input["profiles"], to_www=user_input.get("to_www", False))
            except HomeAssistantError:
                errors["base"] = "export_failed"
            else:
                url = result["url"]
                return self.async_abort(
                    reason="export_done",
                    description_placeholders={
                        "count": str(result["count"]),
                        "path": result["path"],
                        "url": (f"[{url}]({url})" if url else "—"),
                    },
                )
        return self.async_show_form(
            step_id="export",
            data_schema=vol.Schema({
                vol.Required("profiles", default=list(choices)): cv.multi_select(choices),
                vol.Optional("to_www", default=False): bool,
            }),
            errors=errors,
        )

    # --- импорт профилей -----------------------------------------------------
    async def async_step_import_file(self, user_input=None):
        files = await self._manager.async_list_export_files()
        if not files:
            return self.async_abort(
                reason="no_files", description_placeholders={"dir": EXPORT_DIR})
        errors = {}
        placeholders = {"dir": EXPORT_DIR, "error": ""}
        if user_input is not None:
            try:
                profiles = await self._manager.async_read_export_file(user_input["file"])
            except HomeAssistantError as err:
                errors["base"] = "invalid_file"
                placeholders["error"] = str(err)
            else:
                self._import_file = user_input["file"]
                self._import_choices = _import_choices_for(
                    profiles, await _kind_labels(self.hass))
                return await self.async_step_import_select()
        return self.async_show_form(
            step_id="import_file",
            data_schema=vol.Schema({vol.Required("file"): vol.In(files)}),
            errors=errors,
            description_placeholders=placeholders,
        )

    # --- загрузка файла профилей извне ----------------------------------------
    async def async_step_upload(self, user_input=None):
        """Загрузить файл профилей, хранящийся вне Home Assistant.

        Файл копируется в каталог kotel_profiles и дальше импортируется обычным
        путём (выбор профилей и контроллера), как после выбора существующего файла.
        """
        errors = {}
        placeholders = {"dir": EXPORT_DIR, "error": ""}
        if user_input is not None:
            filename = None
            try:
                filename = await self.hass.async_add_executor_job(
                    _save_uploaded_json, self.hass, user_input["file"],
                    self.hass.config.path(EXPORT_DIR))
                profiles = await self._manager.async_read_export_file(filename)
            except HomeAssistantError as err:
                errors["base"] = "invalid_file"
                placeholders["error"] = str(err)
            except (ValueError, OSError) as err:
                errors["base"] = "upload_failed"
                placeholders["error"] = str(err)
            else:
                if filename:
                    self._import_file = filename
                    self._import_choices = _import_choices_for(
                        profiles, await _kind_labels(self.hass))
                    return await self.async_step_import_select()
            if filename:
                await self.hass.async_add_executor_job(
                    _remove_json_file, self.hass.config.path(EXPORT_DIR), filename)
        return self.async_show_form(
            step_id="upload",
            data_schema=vol.Schema({
                vol.Required("file"): FileSelector(FileSelectorConfig(accept=".json")),
            }),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_import_select(self, user_input=None):
        controllers = {dev_id: info["name"] for dev_id, info in self._manager.controllers().items()}
        if not controllers:
            return self.async_abort(reason="no_controllers")
        if user_input is not None:
            result = await self._manager.async_import(
                self._import_file,
                keys=user_input["profiles"],
                dev_id=user_input["controller"],
                on_conflict=user_input["on_conflict"],
            )
            labels = await _kind_labels(self.hass)
            renamed = ", ".join(
                f"{labels[r['kind']]} · {r['from']} → {r['to']}" for r in result["renamed"]
            ) or "—"
            return self.async_abort(
                reason="import_done",
                description_placeholders={
                    "count": str(len(result["imported"])),
                    "skipped": str(len(result["skipped"])),
                    "renamed": renamed,
                },
            )
        return self.async_show_form(
            step_id="import_select",
            data_schema=vol.Schema({
                vol.Required("profiles", default=list(self._import_choices)): cv.multi_select(
                    self._import_choices),
                vol.Required("controller", default=next(iter(controllers))): vol.In(controllers),
                vol.Required("on_conflict", default="rename"): SelectSelector(
                    SelectSelectorConfig(
                        options=ON_CONFLICT_OPTIONS,
                        translation_key="on_conflict",
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
        )

    # --- удаление профилей ---------------------------------------------------
    async def async_step_delete(self, user_input=None):
        choices = self._manager.profile_choices(await _kind_labels(self.hass))
        if not choices:
            return self.async_abort(reason="no_profiles")
        if user_input is not None:
            count = await self._manager.async_delete_many(user_input["profiles"])
            return self.async_abort(
                reason="delete_done", description_placeholders={"count": str(count)})
        return self.async_show_form(
            step_id="delete",
            data_schema=vol.Schema({
                vol.Required("profiles", default=[]): cv.multi_select(choices),
            }),
        )
