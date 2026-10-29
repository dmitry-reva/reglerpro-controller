"""Config flow для интеграции Kotel."""

import os
import re
import shutil

import voluptuous as vol  # pyright: ignore[reportMissingImports]
from homeassistant import config_entries  # pyright: ignore[reportMissingImports]
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.core import callback  # pyright: ignore[reportMissingImports]
from homeassistant.exceptions import HomeAssistantError  # pyright: ignore[reportMissingImports]

from homeassistant.helpers import config_validation as cv  # pyright: ignore[reportMissingImports]
from homeassistant.helpers.selector import (
    FileSelector,
    FileSelectorConfig,
)

from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DEFAULT_URL, DOMAIN
from .profiles import EXPORT_DIR, KIND_LABELS
from .api import KotelApiClient, KotelApiError

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required("url", default=DEFAULT_URL): str,
    vol.Required("login"): str,
    vol.Required("password"): str,
    vol.Optional("home_id", default=""): str,
})


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
            # Одна запись на связку «сервер + логин + дом»: защита от дублей.
            home_id = (user_input.get("home_id") or "").strip()
            unique_id = "|".join((
                user_input["url"].strip().rstrip("/").lower(),
                user_input["login"].strip().casefold(),
                home_id,
            ))
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

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

    @property
    def _manager(self):
        return self.hass.data[DOMAIN]["_profiles"]

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", "export", "import_file", "upload", "delete"],
        )

    # --- параметры -----------------------------------------------------------
    async def async_step_settings(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

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

    # --- экспорт профилей ----------------------------------------------------
    async def async_step_export(self, user_input=None):
        choices = self._manager.profile_choices()
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
                self._import_choices = {
                    f"{p['kind']}|{p['name']}": f"{KIND_LABELS[p['kind']]} · {p['name']}"
                    for p in profiles
                }
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
                    self._import_choices = {
                        f"{p['kind']}|{p['name']}": f"{KIND_LABELS[p['kind']]} · {p['name']}"
                        for p in profiles
                    }
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
            renamed = ", ".join(
                f"{KIND_LABELS[r['kind']]} · {r['from']} → {r['to']}" for r in result["renamed"]
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
                vol.Required("on_conflict", default="rename"): vol.In({
                    "rename": "Добавить с номером «(2)»",
                    "overwrite": "Заменить существующий",
                    "skip": "Пропустить",
                }),
            }),
        )

    # --- удаление профилей ---------------------------------------------------
    async def async_step_delete(self, user_input=None):
        choices = self._manager.profile_choices()
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
