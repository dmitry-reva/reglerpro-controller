"""Числовые настройки котла (уставки температуры, вентилятора и т.д.)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory

from .api import KotelApiError
from .const import (
    CLIMATE_TEMP_MAX,
    CLIMATE_TEMP_MIN,
    CLIMATE_TEMP_STEP,
    SCHEDULE_GROUPS,
    SCHEDULE_PERIODS,
)
from .coordinator import to_number
from .entity import KotelEntity
from .params_parser import is_kotel_auto

if TYPE_CHECKING:  # pragma: no cover - только для типов
    from .runtime import KotelConfigEntry

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
    ("shn_roz_vkl", "auger_run_heating", 1, 600, 1,
     UnitOfTime.SECONDS, EntityCategory.CONFIG),
    ("shn_uga_vkl", "auger_run_maintenance", 1, 600, 1,
     UnitOfTime.SECONDS, EntityCategory.CONFIG),
    ("shn_rev_auto_i", "auger_reverse_interval", 1, 600, 1,
     None, EntityCategory.CONFIG),
    # заслонка вентилятора: 1 % — закрыта, 99 % — открыта (params_for_range в приложении)
    ("servo_cooler", "fan_damper", 1, 99, 1,
     PERCENTAGE, EntityCategory.CONFIG),
)

# Параметры шнека есть только у «авто»-котлов (в приложении is_kotel_auto), а минимальная
# мощность вентилятора vent_min — только у остальных.
AUGER_KEYS = frozenset({"shn_roz_vkl", "shn_roz", "shn_uga_vkl", "shn_uga"})

# Коррекция датчика температуры помещения (action korect_dt): −15…15 °C, шаг 0,5
SENSOR_CORRECTION_MIN = -15
SENSOR_CORRECTION_MAX = 15
SENSOR_CORRECTION_STEP = 0.5


def param_supported(key: str, params: dict) -> bool:
    """Показывает ли приложение ReglerPro этот параметр для данного контроллера.

    Правила взяты из functions.js (page_device_settings и страница контроллера):
    параметра нет в ответе — не показывается; шнек — только у «авто»-котлов;
    vent_min — только у остальных; «Коррекция t° воды» и «Время угасания» — только
    при korect_20 > 0; заслонка — только при ненулевом значении.
    """
    if params.get(key) is None:
        return False
    auto = is_kotel_auto(params.get("vers"))
    if key in AUGER_KEYS:
        return bool(auto)
    if key == "vent_min":
        return not auto
    if key in ("korect_", "time_ugas"):
        return (to_number(params.get("korect_20")) or 0) > 0
    if key == "servo_cooler":
        return bool(params[key])
    return True


# Записи параметров котла уходят в облако по одной
PARALLEL_UPDATES = 1


async def async_setup_entry(hass, entry: KotelConfigEntry, async_add_entities):
    coordinator = entry.runtime_data.coordinator
    entities = []

    for dev_id, dev_data in coordinator.data.items():
        if dev_data["type"] != "contr":
            continue
        for key, translation_key, min_val, max_val, step, unit, category in NUMBER_PARAMS:
            entities.append(KotelNumberEntity(
                coordinator, dev_id,
                key, translation_key, min_val, max_val, step, unit, category,
            ))

    # Коррекция датчика температуры помещения — у каждого датчика
    for dev_id, dev_data in coordinator.data.items():
        if dev_data["type"] == "dt":
            entities.append(KotelSensorCorrectionNumber(coordinator, dev_id))

    # Расписание климат-контроля: 6 групп × 4 периода × (время + температура)
    for dev_id, dev_data in coordinator.data.items():
        if dev_data["type"] != "contr":
            continue
        for template, _label in SCHEDULE_GROUPS:
            for period in SCHEDULE_PERIODS:
                for kind in ("time", "temp"):
                    entities.append(KotelScheduleNumber(
                        coordinator, dev_id,
                        template, period, kind,
                    ))

    async_add_entities(entities)


class KotelNumberEntity(KotelEntity, NumberEntity):
    """Регулятор числового параметра котла."""

    def __init__(self, coordinator, dev_id, key, translation_key,
                 min_val, max_val, step, unit, category):
        super().__init__(
            coordinator, dev_id,
            unique_suffix=f"{key}_set",
            translation_key=translation_key,
        )
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

    def _is_supported(self) -> bool:
        data = self.coordinator.data.get(self._dev_id, {})
        return param_supported(self._key, data.get("params") or {})

    @property
    def available(self) -> bool:
        """Недоступно, если приложение этот параметр для контроллера не показывает."""
        return super().available and self._is_supported()

    async def async_set_native_value(self, value):
        api = self.coordinator.api
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

    def __init__(self, coordinator, dev_id, template, period, kind):
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
            coordinator, dev_id, key,
            f"schedule_{key.lower()}",
            min_val, max_val, step, unit, EntityCategory.CONFIG,
        )
        # Прежний формат unique_id сохраняется для существующих установок.
        self._attr_unique_id = f"kotel_{dev_id}_{key}_sched"
        self._attr_mode = NumberMode.BOX
        self._attr_icon = "mdi:clock-outline" if kind == "time" else "mdi:thermometer"

    def _is_supported(self) -> bool:
        return True                       # расписание: правила параметров котла не применяются

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
        api = self.coordinator.api
        try:
            await api.set_climate_value(self._dev_id, self._key, value)
        except KotelApiError as err:
            raise HomeAssistantError(
                f"Не удалось записать {self._key}: {err}") from err
        await self.coordinator.async_request_refresh()


class KotelSensorCorrectionNumber(KotelEntity, NumberEntity):
    """Коррекция показаний датчика температуры помещения, °C (action korect_dt)."""

    _attr_native_min_value = SENSOR_CORRECTION_MIN
    _attr_native_max_value = SENSOR_CORRECTION_MAX
    _attr_native_step = SENSOR_CORRECTION_STEP
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:thermometer-plus"

    def __init__(self, coordinator, dev_id):
        super().__init__(
            coordinator, dev_id,
            unique_suffix="korect_dt_set",
            translation_key="sensor_correction",
        )

    @property
    def native_value(self):
        data = self.coordinator.data.get(self._dev_id, {})
        return to_number(data.get("korect_dt"))

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    async def async_set_native_value(self, value):
        value = float(value)
        value = int(value) if value.is_integer() else value
        api = self.coordinator.api
        try:
            await api.send_command("korect_dt", self._dev_id, value)
        except KotelApiError as err:
            raise HomeAssistantError(
                f"Не удалось записать коррекцию датчика: {err}") from err
        await self.coordinator.async_request_refresh()
