"""Выбор датчика температуры помещения для климат-контроля."""

import logging

from homeassistant.components.select import SelectEntity  # pyright: ignore[reportMissingImports]
from homeassistant.exceptions import (  # pyright: ignore[reportMissingImports]
    HomeAssistantError,
    ServiceValidationError,
)

from .api import KotelApiError
from .const import DEVICE_TYPE_CONTR, DEVICE_TYPE_DT, DOMAIN
from .coordinator import KotelCoordinator
from .profiles import ALL_KINDS, KIND_BOILER

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator: KotelCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    manager = hass.data[DOMAIN]["_profiles"]
    entities = []
    for dev_id, dev_data in coordinator.data.items():
        if dev_data["type"] == DEVICE_TYPE_CONTR:
            entities.append(KotelClimateSensorSelect(coordinator, dev_id, entry.entry_id))
            for kind in ALL_KINDS:
                entities.append(KotelProfileSelect(coordinator, dev_id, manager, kind))
    async_add_entities(entities)


class KotelClimateSensorSelect(SelectEntity):
    """Какой датчик температуры использует климат-контроль."""

    _attr_should_poll = False
    _attr_icon = "mdi:thermometer-check"

    def __init__(self, coordinator: KotelCoordinator, dev_id: str, entry_id: str):
        self._coordinator = coordinator
        self._dev_id = dev_id
        self._entry_id = entry_id
        self._fetched: list[dict] = []     # ответ get_climat_sensors (если он непустой)
        self._attr_name = "Датчик климат-контроля"
        self._attr_unique_id = f"kotel_{dev_id}_climate_sensor"

    # ------------------------------------------------------------------
    @property
    def _data(self) -> dict:
        return self._coordinator.data.get(self._dev_id, {})

    @property
    def _api(self):
        return self._coordinator.hass.data[DOMAIN][self._entry_id]["api"]

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._dev_id)},
            "name": self._data.get("name", self._dev_id),
            "manufacturer": "Kotel",
            "model": "Контроллер",
        }

    def _candidates(self) -> list[tuple[str, str]]:
        """Список (id, имя) датчиков: ответ сервера или датчики из get_all_dev."""
        items = [(s["id_name"], str(s.get("name") or s["id_name"])) for s in self._fetched]
        if not items:
            items = [
                (dev_id, str(dev.get("name") or dev_id))
                for dev_id, dev in self._coordinator.data.items()
                if dev.get("type") == DEVICE_TYPE_DT
            ]
        # текущий выбранный датчик должен быть в списке, даже если его нет в ответах
        climate = self._data.get("climate", {})
        current_id = climate.get("sensor_id")
        if current_id and current_id not in {i for i, _ in items}:
            items.append((current_id, str(climate.get("sensor_name") or current_id)))
        return items

    def _label_map(self) -> dict[str, str]:
        """label -> id. Одинаковые имена различаем суффиксом с id."""
        candidates = self._candidates()
        names = [name for _, name in candidates]
        mapping: dict[str, str] = {}
        for sensor_id, name in candidates:
            label = name if names.count(name) == 1 else f"{name} ({sensor_id[-4:]})"
            mapping[label] = sensor_id
        return mapping

    @property
    def options(self) -> list[str]:
        return list(self._label_map())

    @property
    def current_option(self):
        current_id = self._data.get("climate", {}).get("sensor_id")
        if not current_id:
            return None
        for label, sensor_id in self._label_map().items():
            if sensor_id == current_id:
                return label
        return None

    # ------------------------------------------------------------------
    async def async_added_to_hass(self):
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )
        # Один раз запрашиваем список у сервера (get_big_contr_climat его не отдаёт).
        try:
            self._fetched = await self._api.get_climate_sensors(self._dev_id)
        except KotelApiError as err:
            _LOGGER.debug("get_climat_sensors не удался, берём датчики из списка: %s", err)
            self._fetched = []
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        sensor_id = self._label_map().get(option)
        if sensor_id is None:
            raise ServiceValidationError(f"Неизвестный датчик: {option}")
        try:
            await self._api.select_climate_sensor(self._dev_id, sensor_id)
        except KotelApiError as err:
            raise HomeAssistantError(f"Не удалось выбрать датчик: {err}") from err
        await self._coordinator.async_request_refresh()


# тип профиля -> (название, иконка, суффикс unique_id).
# Для котла unique_id прежний (kotel_<id>_profile), чтобы сущность не пересоздавалась.
PROFILE_SELECTS = {
    "boiler": ("Профиль котла", "mdi:content-save-cog", "profile"),
    "climate": ("Профиль климата", "mdi:content-save-cog-outline", "climate_profile"),
}


class KotelProfileSelect(SelectEntity):
    """Выбор сохранённого профиля (применяется кнопкой «Применить профиль …»)."""

    _attr_should_poll = False

    def __init__(self, coordinator: KotelCoordinator, dev_id: str, manager,
                 kind: str = KIND_BOILER):
        self._coordinator = coordinator
        self._dev_id = dev_id
        self._manager = manager
        self._kind = kind
        name, icon, suffix = PROFILE_SELECTS[kind]
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"kotel_{dev_id}_{suffix}"

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success

    @property
    def device_info(self):
        data = self._coordinator.data.get(self._dev_id, {})
        return {
            "identifiers": {(DOMAIN, self._dev_id)},
            "name": data.get("name", self._dev_id),
            "manufacturer": "Kotel",
            "model": "Контроллер",
        }

    @property
    def options(self) -> list[str]:
        return self._manager.profile_names(self._dev_id, self._kind)

    @property
    def current_option(self):
        return self._manager.selected(self._dev_id, self._kind)

    async def async_added_to_hass(self):
        self.async_on_remove(self._coordinator.async_add_listener(self.async_write_ha_state))
        self.async_on_remove(self._manager.async_add_listener(self.async_write_ha_state))

    async def async_select_option(self, option: str) -> None:
        await self._manager.async_set_selected(self._dev_id, option, kind=self._kind)
