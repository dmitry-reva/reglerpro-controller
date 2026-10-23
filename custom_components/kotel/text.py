"""Поля ввода имени нового профиля настроек (котёл и климат)."""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode

from .const import DEVICE_TYPE_CONTR, DOMAIN
from .coordinator import KotelCoordinator
from .entity import KotelEntity
from .profiles import ALL_KINDS, KIND_BOILER, MAX_NAME_LENGTH

# тип профиля -> (translation_key, суффикс unique_id); для котла суффикс прежний
PROFILE_NAMES = {
    "boiler": ("boiler_profile_name", "profile_name"),
    "climate": ("climate_profile_name", "climate_profile_name"),
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


class KotelProfileNameText(KotelEntity, TextEntity):
    """Имя для кнопки «Сохранить профиль …»."""

    _attr_icon = "mdi:form-textbox"
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = MAX_NAME_LENGTH

    def __init__(self, coordinator: KotelCoordinator, dev_id: str, manager,
                 kind: str = KIND_BOILER):
        translation_key, suffix = PROFILE_NAMES[kind]
        super().__init__(
            coordinator, dev_id,
            unique_suffix=suffix,
            translation_key=translation_key,
        )
        self._manager = manager
        self._kind = kind

    @property
    def native_value(self) -> str:
        return self._manager.draft_name(self._dev_id, self._kind)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self._manager.async_add_listener(self.async_write_ha_state))

    async def async_set_value(self, value: str) -> None:
        self._manager.set_draft_name(self._dev_id, value, self._kind)
