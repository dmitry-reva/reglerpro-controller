"""Config flow для интеграции Kotel."""

import voluptuous as vol  # pyright: ignore[reportMissingImports]
from homeassistant import config_entries  # pyright: ignore[reportMissingImports]
from homeassistant.core import callback  # pyright: ignore[reportMissingImports]

from .const import DOMAIN, DEFAULT_URL
from .api import KotelApiClient, KotelApiError

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required("url", default=DEFAULT_URL): str,
    vol.Required("login"): str,
    vol.Required("password"): str,
    vol.Optional("home_id", default=""): str,
})


class KotelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow для Kotel."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Первый шаг — ввод учётных данных."""
        errors = {}

        if user_input is not None:
            # Проверяем соединение
            api = KotelApiClient(
                base_url=user_input["url"],
                login=user_input["login"],
                password=user_input["password"],
                home_id=user_input.get("home_id", ""),
            )
            try:
                devices = await api.get_all_devices()
                await api.close()

                if not devices and not user_input.get("home_id"):
                    errors["base"] = "no_home_id"
                else:
                    return self.async_create_entry(
                        title=f"Kotel ({user_input['login']})",
                        data=user_input,
                    )
            except KotelApiError:
                await api.close()
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return KotelOptionsFlow(config_entry)


class KotelOptionsFlow(config_entries.OptionsFlow):
    """Опции интеграции."""

    def __init__(self, config_entry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    "scan_interval",
                    default=30,
                ): vol.All(int, vol.Range(min=10, max=300)),
            }),
        )
