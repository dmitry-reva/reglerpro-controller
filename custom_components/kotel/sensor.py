"""Сенсоры для интеграции Kotel."""

import logging

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature, PERCENTAGE, UnitOfTime
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN, DEVICE_TYPE_CONTR, DEVICE_TYPE_DT
from .coordinator import KotelCoordinator
from .params_parser import get_params, sost_rab_text, is_kotel_auto

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Настройка сенсоров при добавлении интеграции."""
    coordinator: KotelCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = []

    for dev_id, dev_data in coordinator.data.items():
        if dev_data["type"] == DEVICE_TYPE_CONTR:
            entities.extend(_create_contr_sensors(coordinator, dev_id, dev_data))
        elif dev_data["type"] == DEVICE_TYPE_DT:
            entities.append(KotelDtSensor(coordinator, dev_id, dev_data))

    async_add_entities(entities)


def _create_contr_sensors(coordinator, dev_id, dev_data):
    """Создать набор сенсоров для контроллера котла."""
    sensors = []

    # Температура воды
    sensors.append(KotelContrSensor(
        coordinator, dev_id, "temp_w", "Температура воды",
        SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS,
        SensorStateClass.MEASUREMENT, None,
    ))

    # Режим климат-контроля (нагрев / охлаждение)
    sensors.append(KotelClimateModeSensor(coordinator, dev_id))
      
    # Уставка температуры воды (max)
    sensors.append(KotelContrSensor(
        coordinator, dev_id, "temp_w_ust", "Уставка температуры (max)",
        SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS,
        SensorStateClass.MEASUREMENT, None,
    ))
    # Минимальная температура воды
    sensors.append(KotelContrSensor(
        coordinator, dev_id, "temp_w_min", "Уставка температуры (min)",
        SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS,
        SensorStateClass.MEASUREMENT, None,
    ))
    # Температура шнека (для авто-котлов)
    sensors.append(KotelContrSensor(
        coordinator, dev_id, "shnek_temp", "Температура шнека",
        SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS,
        SensorStateClass.MEASUREMENT, None,
    ))
    # Состояние работы (текстовый)
    sensors.append(KotelContrStateSensor(coordinator, dev_id))
    # Вентилятор (проценты)
    sensors.append(KotelContrSensor(
        coordinator, dev_id, "vent_max", "Вентилятор (max)",
        None, PERCENTAGE, SensorStateClass.MEASUREMENT, None,
        EntityCategory.CONFIG,
    ))
    sensors.append(KotelContrSensor(
        coordinator, dev_id, "vent_min", "Вентилятор (min)",
        None, PERCENTAGE, SensorStateClass.MEASUREMENT, None,
        EntityCategory.CONFIG,
    ))
    # Насос
    sensors.append(KotelContrSensor(
        coordinator, dev_id, "nasos_t", "Температура насоса",
        SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS,
        SensorStateClass.MEASUREMENT, None, EntityCategory.CONFIG,
    ))
    # Время продувки
    sensors.append(KotelContrSensor(
        coordinator, dev_id, "produv_time", "Время продувки",
        SensorDeviceClass.DURATION, UnitOfTime.SECONDS,
        SensorStateClass.MEASUREMENT, None, EntityCategory.CONFIG,
    ))
    # Интервал продувки
    sensors.append(KotelContrSensor(
        coordinator, dev_id, "produv_interval", "Интервал продувки",
        SensorDeviceClass.DURATION, UnitOfTime.MINUTES,
        SensorStateClass.MEASUREMENT, None, EntityCategory.CONFIG,
    ))
    # Время угасания
    sensors.append(KotelContrSensor(
        coordinator, dev_id, "time_ugas", "Время угасания",
        SensorDeviceClass.DURATION, UnitOfTime.MINUTES,
        SensorStateClass.MEASUREMENT, None, EntityCategory.CONFIG,
    ))
    # Коррекция температуры
    sensors.append(KotelContrSensor(
        coordinator, dev_id, "korect_", "Коррекция температуры",
        SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS,
        SensorStateClass.MEASUREMENT, None, EntityCategory.CONFIG,
    ))
    # Заслонка вентилятора
    sensors.append(KotelContrSensor(
        coordinator, dev_id, "servo_cooler", "Заслонка вентилятора",
        None, PERCENTAGE, SensorStateClass.MEASUREMENT, None,
        EntityCategory.CONFIG,
    ))
    # Статус онлайн
    sensors.append(KotelContrOnlineSensor(coordinator, dev_id))

    return sensors


class KotelBaseSensor(SensorEntity):
    """Базовый класс сенсора Kotel."""

    def __init__(self, coordinator: KotelCoordinator, dev_id: str):
        self._coordinator = coordinator
        self._dev_id = dev_id
        self._attr_should_poll = False

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success

    async def async_added_to_hass(self):
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )

    def _get_device_data(self) -> dict:
        return self._coordinator.data.get(self._dev_id, {})

    @property
    def device_info(self):
        data = self._get_device_data()
        return {
            "identifiers": {(DOMAIN, self._dev_id)},
            "name": data.get("name", self._dev_id),
            "manufacturer": "Kotel",
            "model": "Контроллер" if data.get("type") == "contr" else "Датчик t",
        }


class KotelContrSensor(KotelBaseSensor):
    """Сенсор параметра контроллера (числовое значение из params)."""

    def __init__(self, coordinator, dev_id, param_key, name,
                 device_class, unit, state_class, icon=None,
                 entity_category=None):
        super().__init__(coordinator, dev_id)
        self._param_key = param_key
        self._attr_name = f"{name}"
        self._attr_unique_id = f"kotel_{dev_id}_{param_key}"
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class
        if icon:
            self._attr_icon = icon
        if entity_category:
            self._attr_entity_category = entity_category

    @property
    def native_value(self):
        data = self._get_device_data()
        params = data.get("params", {})
        return params.get(self._param_key)


class KotelContrStateSensor(KotelBaseSensor):
    """Сенсор состояния работы контроллера (sost_rab)."""

    def __init__(self, coordinator, dev_id):
        super().__init__(coordinator, dev_id)
        self._attr_name = "Состояние"
        self._attr_unique_id = f"kotel_{dev_id}_state"
        self._attr_icon = "mdi:state-machine"

    @property
    def native_value(self) -> str:
        data = self._get_device_data()
        params = data.get("params", {})
        sost = params.get("sost_rab", 0)
        return sost_rab_text(sost)

    @property
    def extra_state_attributes(self):
        data = self._get_device_data()
        params = data.get("params", {})
        attrs = {"sost_rab_raw": params.get("sost_rab", 0)}
        if "vent_on" in params:
            attrs["vent_on"] = params["vent_on"]
        if "vers" in params:
            attrs["version"] = params["vers"]
            attrs["auto"] = is_kotel_auto(params.get("vers", 0))
        return attrs
    
    class KotelClimateModeSensor(KotelBaseSensor):
    """Режим климат-контроля: Нагрев / Охлаждение / Выключен (tstatNow)."""

    _ICONS = {
        "Нагрев": "mdi:fire",
        "Охлаждение": "mdi:snowflake",
        "Выключен": "mdi:thermostat-off",
    }

    def __init__(self, coordinator, dev_id):
        super().__init__(coordinator, dev_id)
        self._attr_name = "Климат-контроль"
        self._attr_unique_id = f"kotel_{dev_id}_tstat_now"

    @property
    def native_value(self):
        data = self._get_device_data()
        if not data.get("climate", {}).get("active"):
            return "Выключен"
        now = data.get("params", {}).get("tstatNow")
        if now is None:
            return None          # данных нет -> unknown, а не ложный «нагрев»
        return "Охлаждение" if now else "Нагрев"

    @property
    def icon(self):
        return self._ICONS.get(self.native_value, "mdi:thermostat")

    @property
    def extra_state_attributes(self):
        data = self._get_device_data()
        climate = data.get("climate", {})
        params = data.get("params", {})
        attrs = {
            "tstat_now_raw": params.get("tstatNow"),
            "climate_enabled": climate.get("enabled"),
            "sensor_selected": climate.get("sensor_selected"),
        }
        if climate.get("active"):
            attrs.update({
                "sensor_name": climate.get("sensor_name"),
                "sensor_temp": climate.get("sensor_temp"),
                "target_temp": climate.get("target_temp"),
                "period": climate.get("period_text"),
            })
        return attrs


class KotelContrOnlineSensor(KotelBaseSensor):
    """Сенсор статуса онлайн/офлайн контроллера."""

    def __init__(self, coordinator, dev_id):
        super().__init__(coordinator, dev_id)
        self._attr_name = "Статус подключения"
        self._attr_unique_id = f"kotel_{dev_id}_online"
        self._attr_icon = "mdi:wifi"

    @property
    def native_value(self) -> str:
        data = self._get_device_data()
        return "online" if data.get("mqtt_online") == "1" else "offline"


class KotelDtSensor(KotelBaseSensor):
    """Сенсор температуры датчика (dt)."""

    def __init__(self, coordinator, dev_id, dev_data):
        super().__init__(coordinator, dev_id)
        self._attr_name = "Температура"
        self._attr_unique_id = f"kotel_{dev_id}_temp"
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        data = self._get_device_data()
        return data.get("temp")

    @property
    def extra_state_attributes(self):
        data = self._get_device_data()
        return {
            "last_update_seconds": data.get("time", 0),
            "online": data.get("time", 999) < 300,
        }
