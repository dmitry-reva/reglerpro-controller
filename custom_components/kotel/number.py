"""Числовые настройки котла (уставки температуры, вентилятора и т.д.)."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory

from .api import KotelApiError
from .const import (
    CLIMATE_TEMP_MAX,
    CLIMATE_TEMP_MIN,
    CLIMATE_TEMP_STEP,
    DOMAIN,
    SCHEDULE_GROUPS,
    SCHEDULE_PERIODS,
)
from .coordinator import KotelCoordinator, to_number
from .entity import KotelEntity

_LOGGER = logging.getLogger(__name__)

# Описание настраиваемых параметров:
# (ключ, translation_key, min, max, step, unit, category)
NUMBER_PARAMS = (
    ("temp_w_ust", "boiler_target_temperature_max", 10, 90, 1,
     UnitOfTemperature.CELSIUS, None),
    ("temp_w_min", "boiler_target_temperature_min", 10, 50, 1,
     UnitOfTemperature.CELSIUS, None),
    ("nasos_t", "pump_temperature", 0, 80, 1,
     UnitOfTemperature.CELSIUS, EntityCategory.CONFIG),
    ("korect_", "temperature_correction", -7, 7, 1,
     UnitOfTemperature.CELSIUS, EntityCategory.CONFIG),
    ("produv_time", "purge_time", 1, 90, 1,
     UnitOfTime.SECONDS, EntityCategory.CONFIG),
    ("produv_interval", "purge_interval", 1, 90, 1,
     UnitOfTime.MINUTES, EntityCategory.CONFIG),
    ("vent_min", "fan_min", 5, 100, 1,
     PERCENTAGE, EntityCategory.CONFIG),
    ("vent_max", "fan_max", 5, 100, 1,
     PERCENTAGE, EntityCategory.CONFIG),
    ("time_ugas", "burnout_time", 10, 90, 1,
     UnitOfTime.MINUTES, EntityCategory.CONFIG),
    ("shn_roz", "auger_interval_heating", 1, 600, 1,
     UnitOfTime.SECONDS, EntityCategory.CONFIG),
    ("shn_uga", "auger_interval_maintenance", 1, 900, 1,
     UnitOfTime.SECONDS, EntityCategory.CONFIG),
)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator: KotelCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = []

    for dev_id, dev_data in coordinator.data.items():
        if dev_data["type"] != "contr":
            continue
        for key, translation_key, min_val, max_val, step, unit, category in NUMBER_PARAMS:
            entities.append(KotelNumberEntity(
                coordinator, dev_id, entry.entry_id,
                key, translation_key, min_val, max_val, step, unit, category,
            ))

    # Расписание климат-контроля: 6 групп × 4 периода × (время + температура)
    for dev_id, dev_data in coordinator.data.items():
        if dev_data["type"] != "contr":
            continue
        for template, _label in SCHEDULE_GROUPS:
            for period in SCHEDULE_PERIODS:
                for kind in ("time", "temp"):
                    entities.append(KotelScheduleNumber(
                        coordinator, dev_id, entry.entry_id,
                        template, period, kind,
                    ))

    async_add_entities(entities)


class KotelNumberEntity(KotelEntity, NumberEntity):
    """Регулятор числового параметра котла."""

    def __init__(self, coordinator, dev_id, entry_id, key, translation_key,
                 min_val, max_val, step, unit, category):
        super().__init__(
            coordinator, dev_id,
            unique_suffix=f"{key}_set",
            translation_key=translation_key,
        )
        self._entry_id = entry_id
        self._key = key
        self._attr_native_min_value = min_val
        self._attr_native_max_value = max_val
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_mode = NumberMode.SLIDER
        if category:
            self._attr_entity_category = category

    @property
    def native_value(self):
        data = self.coordinator.data.get(self._dev_id, {})
        return (data.get("params") or {}).get(self._key)

    async def async_set_native_value(self, value):
        api = self.coordinator.hass.data[DOMAIN][self._entry_id]["api"]
        try:
            await api.send_command(self._key, self._dev_id, value)
        except KotelApiError as err:
            raise HomeAssistantError(
                f"Не удалось записать «{self._key}»: {err}") from err
        await self.coordinator.async_request_refresh()


class KotelScheduleNumber(KotelNumberEntity):
    """Значение расписания климат-контроля: час начала периода или его температура.

    Для времени границы зависят от соседних периодов (как в climate.js):
    период N должен начинаться позже N-1 и раньше N+1; первый — с 0 ч,
    последний — до 23 ч.
    """

    def __init__(self, coordinator, dev_id, entry_id, template, period, kind):
        self._template = template
        self._period = period
        self._kind = kind
        key = template.format(n=period, kind=kind)
        if kind == "time":
            min_val, max_val, step, unit = 0, 23, 1, UnitOfTime.HOURS
        else:
            min_val, max_val, step, unit = (
                CLIMATE_TEMP_MIN, CLIMATE_TEMP_MAX, CLIMATE_TEMP_STEP,
                UnitOfTemperature.CELSIUS,
            )
        super().__init__(
            coordinator, dev_id, entry_id, key,
            f"schedule_{key.lower()}",
            min_val, max_val, step, unit, EntityCategory.CONFIG,
        )
        # Прежний формат unique_id сохраняется для существующих установок.
        self._attr_unique_id = f"kotel_{dev_id}_{key}_sched"
        self._attr_mode = NumberMode.BOX
        self._attr_icon = "mdi:clock-outline" if kind == "time" else "mdi:thermometer"

    def _schedule(self) -> dict:
        data = self.coordinator.data.get(self._dev_id, {})
        return (data.get("climate") or {}).get("schedule", {})

    def _neighbor_hour(self, period: int):
        key = self._template.format(n=period, kind="time")
        return to_number(self._schedule().get(key))

    @property
    def native_value(self):
        return to_number(self._schedule().get(self._key))

    @property
    def native_min_value(self):
        if self._kind != "time" or self._period == SCHEDULE_PERIODS[0]:
            return self._attr_native_min_value
        previous = self._neighbor_hour(self._period - 1)
        return previous + 1 if previous is not None else self._attr_native_min_value

    @property
    def native_max_value(self):
        if self._kind != "time" or self._period == SCHEDULE_PERIODS[-1]:
            return self._attr_native_max_value
        following = self._neighbor_hour(self._period + 1)
        return following - 1 if following is not None else self._attr_native_max_value

    async def async_set_native_value(self, value):
        value = float(value)
        value = int(value) if self._kind == "time" or value.is_integer() else value
        api = self.coordinator.hass.data[DOMAIN][self._entry_id]["api"]
        try:
            await api.set_climate_value(self._dev_id, self._key, value)
        except KotelApiError as err:
            raise HomeAssistantError(
                f"Не удалось записать {self._key}: {err}") from err
        await self.coordinator.async_request_refresh()
