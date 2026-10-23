"""Термостат: климат-контроль контроллера Kotel.

Соответствие режимам контроллера (tstat_mode):
    HVACMode.OFF   — климат-контроль выключен (tstatOn = 0)
    HVACMode.HEAT  — включён, режим 0 «постоянная температура»
    HVACMode.AUTO  — включён, режимы расписания (3, 4, 5)
Пресеты: «Расписание 7/7», «Расписание 5/2», «Расписание 4/1/2».

hvac_action: tstatNow = 0 — нагрев, tstatNow = 1 — охлаждение.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    PRECISION_TENTHS,
    UnitOfTemperature,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .api import KotelApiError
from .const import (
    CLIMATE_TEMP_MAX,
    CLIMATE_TEMP_MIN,
    CLIMATE_TEMP_STEP,
    DOMAIN,
    MODE_BY_PRESET,
    PRESET_BY_MODE,
    SCHEDULE_MODES,
    TSTAT_MODE_7_7,
    TSTAT_MODE_CONST,
)
from .coordinator import KotelCoordinator, to_number
from .entity import KotelEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator: KotelCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        KotelClimate(coordinator, dev_id, entry.entry_id)
        for dev_id, dev_data in coordinator.data.items()
        if dev_data["type"] == "contr"
    )


class KotelClimate(KotelEntity, ClimateEntity):
    """Климат-контроль котла (по датчику температуры помещения)."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_TENTHS          # текущая температура — с десятыми
    _attr_target_temperature_step = CLIMATE_TEMP_STEP
    _attr_min_temp = CLIMATE_TEMP_MIN
    _attr_max_temp = CLIMATE_TEMP_MAX
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.AUTO]
    _attr_preset_modes = list(PRESET_BY_MODE.values())
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_icon = "mdi:home-thermometer"

    def __init__(self, coordinator: KotelCoordinator, dev_id: str, entry_id: str):
        super().__init__(
            coordinator, dev_id,
            unique_suffix="climate",
            translation_key="thermostat",
        )
        self._entry_id = entry_id
        self._lock = asyncio.Lock()   # команды климата выполняются строго по очереди

    # ------------------------------------------------------------------
    # Данные
    # ------------------------------------------------------------------
    @property
    def _data(self) -> dict:
        return self.coordinator.data.get(self._dev_id, {})

    @property
    def _climate(self) -> dict:
        return self._data.get("climate", {})

    @property
    def _api(self):
        return self.coordinator.hass.data[DOMAIN][self._entry_id]["api"]

    # ------------------------------------------------------------------
    # Состояние
    # ------------------------------------------------------------------
    @property
    def hvac_mode(self):
        climate = self._climate
        if not climate.get("enabled"):
            return HVACMode.OFF
        if climate.get("mode") in SCHEDULE_MODES:
            return HVACMode.AUTO
        return HVACMode.HEAT

    @property
    def hvac_action(self):
        climate = self._climate
        if not climate.get("enabled"):
            return HVACAction.OFF
        if not climate.get("active"):          # включён, но датчик не выбран
            return HVACAction.IDLE
        now = to_number(self._data.get("params", {}).get("tstatNow"))
        if now is None:
            return None
        return HVACAction.COOLING if now else HVACAction.HEATING

    @property
    def current_temperature(self):
        return self._climate.get("sensor_temp")

    @property
    def target_temperature(self):
        climate = self._climate
        if climate.get("mode") in SCHEDULE_MODES:
            return climate.get("target_temp")            # цель текущего периода
        target = climate.get("target_const")
        return target if target is not None else climate.get("target_temp")

    @property
    def preset_mode(self):
        return PRESET_BY_MODE.get(self._climate.get("mode"))

    @property
    def extra_state_attributes(self):
        climate = self._climate
        return {
            "tstat_mode": climate.get("mode"),
            "tstat_now": self._data.get("params", {}).get("tstatNow"),
            "sensor_id": climate.get("sensor_id"),
            "sensor_name": climate.get("sensor_name"),
            "period": climate.get("period"),
            "period_text": climate.get("period_text"),
            "target_const": climate.get("target_const"),
            "schedule": climate.get("schedule"),
        }

    # ------------------------------------------------------------------
    # Команды
    # ------------------------------------------------------------------
    async def _fresh_climate(self) -> dict:
        """Обновить данные перед решением «включать/выключать».

        climat_on_off — переключатель без явного значения, поэтому решение
        нельзя принимать по устаревшему состоянию.
        """
        await self.coordinator.async_refresh()
        return self._climate

    async def _ensure_enabled(self, want: bool) -> None:
        if bool(self._climate.get("enabled")) != want:
            await self._api.climate_on_off(self._dev_id)

    async def _run(self, action):
        """Выполнить цепочку команд под блокировкой с обработкой ошибок API."""
        async with self._lock:
            try:
                await action()
            except KotelApiError as err:
                raise HomeAssistantError(f"Ошибка команды климат-контроля: {err}") from err
            finally:
                await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode):
        async def action():
            climate = await self._fresh_climate()
            if hvac_mode == HVACMode.OFF:
                await self._ensure_enabled(False)
            elif hvac_mode == HVACMode.HEAT:
                await self._ensure_enabled(True)
                if climate.get("mode") != TSTAT_MODE_CONST:
                    await self._api.set_climate_mode(self._dev_id, TSTAT_MODE_CONST)
            elif hvac_mode == HVACMode.AUTO:
                await self._ensure_enabled(True)
                if climate.get("mode") not in SCHEDULE_MODES:
                    await self._api.set_climate_mode(self._dev_id, TSTAT_MODE_7_7)
            else:
                raise ServiceValidationError(f"Режим {hvac_mode} не поддерживается")

        await self._run(action)

    async def async_turn_on(self):
        async def action():
            await self._fresh_climate()
            await self._ensure_enabled(True)

        await self._run(action)

    async def async_turn_off(self):
        async def action():
            await self._fresh_climate()
            await self._ensure_enabled(False)

        await self._run(action)

    async def async_set_preset_mode(self, preset_mode):
        mode = MODE_BY_PRESET.get(preset_mode)
        if mode is None:
            raise ServiceValidationError(f"Неизвестный пресет: {preset_mode}")

        async def action():
            await self._fresh_climate()
            await self._ensure_enabled(True)       # режимы доступны только при включённом климате
            await self._api.set_climate_mode(self._dev_id, mode)

        await self._run(action)

    async def async_set_temperature(self, **kwargs):
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        # шаг 0.5 °C, диапазон как в приложении
        value = round(float(temperature) / CLIMATE_TEMP_STEP) * CLIMATE_TEMP_STEP
        value = min(max(value, CLIMATE_TEMP_MIN), CLIMATE_TEMP_MAX)
        if float(value).is_integer():
            value = int(value)

        async def action():
            climate = await self._fresh_climate()
            if climate.get("mode") in SCHEDULE_MODES:
                raise ServiceValidationError(
                    "В режиме расписания температура задаётся для периодов "
                    "(number «Расписание …»). Для постоянной температуры "
                    "выберите режим «Нагрев»."
                )
            await self._api.set_climate_value(self._dev_id, "temp_dt", value)

        await self._run(action)
