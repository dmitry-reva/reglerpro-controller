"""Числовые настройки котла (уставки температуры, вентилятора и т.д.)."""

import logging

from homeassistant.components.number import NumberEntity, NumberMode  # pyright: ignore[reportMissingImports]
from homeassistant.const import UnitOfTemperature, PERCENTAGE, UnitOfTime  # pyright: ignore[reportMissingImports]
from homeassistant.helpers.entity import EntityCategory # pyright: ignore[reportMissingImports]

from .const import DOMAIN
from .coordinator import KotelCoordinator

_LOGGER = logging.getLogger(__name__)

# Описание настраиваемых параметров: (key, name, min, max, step, unit, category)
NUMBER_PARAMS = [
    ("temp_w_ust", "Уставка температуры (max)", 10, 90, 1, UnitOfTemperature.CELSIUS, None),
    ("temp_w_min", "Уставка температуры (min)", 10, 50, 1, UnitOfTemperature.CELSIUS, None),
    ("nasos_t", "Температура насоса", 0, 80, 1, UnitOfTemperature.CELSIUS, EntityCategory.CONFIG),
    ("korect_", "Коррекция температуры", -7, 7, 1, UnitOfTemperature.CELSIUS, EntityCategory.CONFIG),
    ("produv_time", "Время продувки", 1, 90, 1, UnitOfTime.SECONDS, EntityCategory.CONFIG),
    ("produv_interval", "Интервал продувки", 1, 90, 1, UnitOfTime.MINUTES, EntityCategory.CONFIG),
    ("vent_min", "Вентилятор (min)", 5, 100, 1, PERCENTAGE, EntityCategory.CONFIG),
    ("vent_max", "Вентилятор (max)", 5, 100, 1, PERCENTAGE, EntityCategory.CONFIG),
    ("time_ugas", "Время угасания", 10, 90, 1, UnitOfTime.MINUTES, EntityCategory.CONFIG),
    ("shn_roz", "Шнек интервал (нагрев)", 1, 600, 1, UnitOfTime.SECONDS, EntityCategory.CONFIG),
    ("shn_uga", "Шнек интервал (угасание)", 1, 900, 1, UnitOfTime.SECONDS, EntityCategory.CONFIG),
]


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator: KotelCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = []

    for dev_id, dev_data in coordinator.data.items():
        if dev_data["type"] == "contr":
            for key, name, min_val, max_val, step, unit, category in NUMBER_PARAMS:
                entities.append(KotelNumberEntity(
                    coordinator, dev_id, entry.entry_id,
                    key, name, min_val, max_val, step, unit, category,
                ))

    async_add_entities(entities)


class KotelNumberEntity(NumberEntity):
    """Регулятор числового параметра котла."""

    def __init__(self, coordinator, dev_id, entry_id, key, name,
                 min_val, max_val, step, unit, category):
        self._coordinator = coordinator
        self._dev_id = dev_id
        self._entry_id = entry_id
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"kotel_{dev_id}_{key}_set"
        self._attr_native_min_value = min_val
        self._attr_native_max_value = max_val
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_mode = NumberMode.SLIDER
        self._attr_should_poll = False
        if category:
            self._attr_entity_category = category

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success

    @property
    def native_value(self):
        data = self._coordinator.data.get(self._dev_id, {})
        params = data.get("params", {})
        return params.get(self._key)

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
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )

    async def async_set_native_value(self, value):
        api = self._coordinator.hass.data[DOMAIN][self._entry_id]["api"]
        await api.send_command(self._key, self._dev_id, value)
        await self._coordinator.async_request_refresh()
