"""Config flow для интеграции Kotel."""

import asyncio
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
from .api import KotelApiClient, KotelApiError, build_servers

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


ON_CONFLICT_OPTIONS = ["rename", "overwrite", "skip"]   # подписи — selector.on_conflict


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

    async def async_step_user(self, user_input=None):
        """Первый шаг — ввод учётных данных."""
        errors = {}

        if user_input is not None:
            # Одна запись на связку «логин + дом»: на разных серверах учётная запись
            # одна и та же, поэтому адрес сервера в идентификатор не входит.
            home_id = (user_input.get("home_id") or "").strip()
            login = user_input["login"].strip()
            user_input = {**user_input, "url": user_input["url"].strip().rstrip("/")}
            await self.async_set_unique_id(f"{login.casefold()}|{home_id}")
            self._abort_if_unique_id_configured()
            if self._account_already_configured(login, home_id):
                return self.async_abort(reason="already_configured")

            # Проверяем соединение
            api = KotelApiClient(
                base_url=user_input["url"],
                login=user_input["login"],
                password=user_input["password"],
                home_id=home_id,
            )
            try:
                devices = await api.get_all_devices()
            except KotelApiError:
                errors["base"] = "cannot_connect"
            else:
                if devices:
                    return self.async_create_entry(
                        title=f"ReglerPro ({home_id or user_input['login']})",
                        data=user_input,
                    )
                errors["base"] = "no_home_id" if not home_id else "no_devices"
            finally:
                await api.close()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
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
                api = self.hass.data[DOMAIN][self.config_entry.entry_id]["api"]
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
