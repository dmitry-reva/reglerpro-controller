"""Поля ввода имени нового профиля настроек (котёл и климат)."""

from homeassistant.components.text import TextEntity, TextMode  # pyright: ignore[reportMissingImports]

from .const import DEVICE_TYPE_CONTR, DOMAIN
from .coordinator import KotelCoordinator
from .profiles import ALL_KINDS, KIND_BOILER, MAX_NAME_LENGTH

# тип профиля -> (название, суффикс unique_id); для котла unique_id прежний
PROFILE_NAMES = {
    "boiler": ("Имя нового профиля котла", "profile_name"),
    "climate": ("Имя нового профиля климата", "climate_profile_name"),
}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator: KotelCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    manager = hass.data[DOMAIN]["_profiles"]
    async_add_entities(
        KotelProfileNameText(coordinator, dev_id, manager, kind)
        for dev_id, dev_data in coordinator.data.items()
        if dev_data["type"] == DEVICE_TYPE_CONTR
        for kind in ALL_KINDS
    )


class KotelProfileNameText(TextEntity):
    """Имя для кнопки «Сохранить профиль …»."""

    _attr_should_poll = False
    _attr_icon = "mdi:form-textbox"
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = MAX_NAME_LENGTH

    def __init__(self, coordinator: KotelCoordinator, dev_id: str, manager,
                 kind: str = KIND_BOILER):
        self._coordinator = coordinator
        self._dev_id = dev_id
        self._manager = manager
        self._kind = kind
        name, suffix = PROFILE_NAMES[kind]
        self._attr_name = name
        self._attr_unique_id = f"kotel_{dev_id}_{suffix}"

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success

    @property
    def native_value(self) -> str:
        return self._manager.draft_name(self._dev_id, self._kind)

    @property
    def device_info(self):
        data = self._coordinator.data.get(self._dev_id, {})
        return {
            "identifiers": {(DOMAIN, self._dev_id)},
            "name": data.get("name", self._dev_id),
            "manufacturer": "Kotel",
            "model": "Контроллер",
        }

    async def async_added_to_hass(self):
        self.async_on_remove(self._coordinator.async_add_listener(self.async_write_ha_state))
        self.async_on_remove(self._manager.async_add_listener(self.async_write_ha_state))

    async def async_set_value(self, value: str) -> None:
        self._manager.set_draft_name(self._dev_id, value, self._kind)
