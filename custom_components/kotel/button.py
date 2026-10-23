"""Кнопки профилей: сохранить, обновить, применить, отменить — отдельно для котла и климата."""

from homeassistant.components.button import ButtonEntity  # pyright: ignore[reportMissingImports]
from homeassistant.exceptions import (  # pyright: ignore[reportMissingImports]
    HomeAssistantError,
    ServiceValidationError,
)

from .const import DEVICE_TYPE_CONTR, DOMAIN
from .coordinator import KotelCoordinator
from .profiles import ALL_KINDS, KIND_BOILER, KIND_CLIMATE

# тип профиля -> действие -> (название, иконка)
BUTTONS = {
    KIND_BOILER: {
        "save": ("Сохранить профиль котла", "mdi:content-save-plus"),
        "update": ("Обновить профиль котла", "mdi:content-save-edit"),
        "apply": ("Применить профиль котла", "mdi:play-circle"),
        "undo": ("Отменить применение (котёл)", "mdi:undo-variant"),
    },
    KIND_CLIMATE: {
        "save": ("Сохранить профиль климата", "mdi:content-save-plus-outline"),
        "update": ("Обновить профиль климата", "mdi:content-save-edit-outline"),
        "apply": ("Применить профиль климата", "mdi:play-circle-outline"),
        "undo": ("Отменить применение (климат)", "mdi:undo"),
    },
}
# Для котла unique_id прежние (kotel_<id>_profile_<действие>), для климата — с префиксом climate_
_UID_PREFIX = {KIND_BOILER: "profile", KIND_CLIMATE: "climate_profile"}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator: KotelCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    manager = hass.data[DOMAIN]["_profiles"]
    async_add_entities(
        KotelProfileButton(coordinator, dev_id, manager, kind, action)
        for dev_id, dev_data in coordinator.data.items()
        if dev_data["type"] == DEVICE_TYPE_CONTR
        for kind in ALL_KINDS
        for action in BUTTONS[kind]
    )


def _summary(report: dict) -> str:
    return (f"применено {report['applied']}, ошибок {len(report['failed'])}, "
            f"не подтверждено {len(report['unverified'])}, пропущено {len(report['skipped'])}")


class KotelProfileButton(ButtonEntity):
    """Кнопка управления профилями котла или климата выбранного контроллера."""

    _attr_should_poll = False

    def __init__(self, coordinator: KotelCoordinator, dev_id: str, manager,
                 kind: str, action: str):
        self._coordinator = coordinator
        self._dev_id = dev_id
        self._manager = manager
        self._kind = kind
        self._action = action
        name, icon = BUTTONS[kind][action]
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"kotel_{dev_id}_{_UID_PREFIX[kind]}_{action}"

    @property
    def available(self) -> bool:
        if not self._coordinator.last_update_success:
            return False
        if self._action == "undo":               # откат доступен только после применения
            return self._manager.has_undo(self._dev_id, self._kind)
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
        name = self._manager.selected(self._dev_id, self._kind)
        if not name:
            title = "котла" if self._kind == KIND_BOILER else "климата"
            raise ServiceValidationError(f"Сначала выберите профиль {title} в списке")
        return name

    async def async_press(self) -> None:
        dev_id, manager, kind = self._dev_id, self._manager, self._kind
        if self._action == "save":
            name = manager.draft_name(dev_id, kind).strip()
            if not name:
                raise ServiceValidationError(
                    "Введите имя в поле «Имя нового профиля» и нажмите кнопку снова")
            await manager.async_save_profile(dev_id, name, kind=kind)
            manager.set_draft_name(dev_id, "", kind)
        elif self._action == "update":
            await manager.async_update_profile(dev_id, self._selected(), kind=kind)
        elif self._action == "apply":
            report = await manager.async_apply(dev_id, self._selected(), kind=kind)
            if not report["ok"]:
                raise HomeAssistantError("Профиль применён не полностью: " + _summary(report))
        elif self._action == "undo":
            report = await manager.async_undo(dev_id, kind=kind)
            if not report["ok"]:
                raise HomeAssistantError("Откат выполнен не полностью: " + _summary(report))
