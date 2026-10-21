"""Кнопки профилей настроек: сохранить, обновить, применить, отменить."""

from homeassistant.components.button import ButtonEntity  # pyright: ignore[reportMissingImports]
from homeassistant.exceptions import (  # pyright: ignore[reportMissingImports]
    HomeAssistantError,
    ServiceValidationError,
)

from .const import DEVICE_TYPE_CONTR, DOMAIN
from .coordinator import KotelCoordinator

# вид кнопки -> (название, иконка)
BUTTONS = {
    "save": ("Сохранить профиль", "mdi:content-save-plus"),
    "update": ("Обновить профиль", "mdi:content-save-edit"),
    "apply": ("Применить профиль", "mdi:play-circle"),
    "undo": ("Отменить применение", "mdi:undo-variant"),
}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator: KotelCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    manager = hass.data[DOMAIN]["_profiles"]
    async_add_entities(
        KotelProfileButton(coordinator, dev_id, manager, kind)
        for dev_id, dev_data in coordinator.data.items()
        if dev_data["type"] == DEVICE_TYPE_CONTR
        for kind in BUTTONS
    )


def _summary(report: dict) -> str:
    return (f"применено {report['applied']}, ошибок {len(report['failed'])}, "
            f"не подтверждено {len(report['unverified'])}, пропущено {len(report['skipped'])}")


class KotelProfileButton(ButtonEntity):
    """Кнопка управления профилями выбранного контроллера."""

    _attr_should_poll = False

    def __init__(self, coordinator: KotelCoordinator, dev_id: str, manager, kind: str):
        self._coordinator = coordinator
        self._dev_id = dev_id
        self._manager = manager
        self._kind = kind
        name, icon = BUTTONS[kind]
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"kotel_{dev_id}_profile_{kind}"

    @property
    def available(self) -> bool:
        if not self._coordinator.last_update_success:
            return False
        if self._kind == "undo":                 # откат доступен только после применения
            return self._manager.has_undo(self._dev_id)
        return True

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

    def _selected(self) -> str:
        name = self._manager.selected(self._dev_id)
        if not name:
            raise ServiceValidationError("Сначала выберите профиль в списке «Профиль»")
        return name

    async def async_press(self) -> None:
        dev_id, manager = self._dev_id, self._manager
        if self._kind == "save":
            name = manager.draft_name(dev_id).strip()
            if not name:
                raise ServiceValidationError(
                    "Введите имя в поле «Имя нового профиля» и нажмите кнопку снова")
            await manager.async_save_profile(dev_id, name)
            manager.set_draft_name(dev_id, "")
        elif self._kind == "update":
            await manager.async_update_profile(dev_id, self._selected())
        elif self._kind == "apply":
            report = await manager.async_apply(dev_id, self._selected())
            if not report["ok"]:
                raise HomeAssistantError("Профиль применён не полностью: " + _summary(report))
        elif self._kind == "undo":
            report = await manager.async_undo(dev_id)
            if not report["ok"]:
                raise HomeAssistantError("Откат выполнен не полностью: " + _summary(report))
