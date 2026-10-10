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


# ---------------------------------------------------------------------------
# Диагностические сенсоры «для изучения состояний»
# ---------------------------------------------------------------------------
# (ключ в params, название, device_class, единица, state_class, иконка)
# Для флагов/кодов state_class не задаётся: в истории HA они отображаются
# как временная шкала состояний, что удобно для изучения.
# Смысл значений уточняйте по наблюдениям.
DIAG_PARAM_SENSORS = [
    ("vers", "Версия прошивки", None, None, None, "mdi:chip"),
    ("vent_on", "Вентилятор включён", None, None, None, "mdi:fan"),
    # Аварии и ошибки
    ("err_temp_w", "Ошибка датчика t воды", None, None, None, "mdi:alert-circle-outline"),
    ("err_temp_shnek", "Ошибка датчика t шнека", None, None, None, "mdi:alert-circle-outline"),
    ("shnek_avaria", "Авария шнека", None, None, None, "mdi:alert"),
    ("alarm_w", "Тревога по воде", None, None, None, "mdi:alarm-light"),
    ("simistor_err", "Ошибка симистора", None, None, None, "mdi:flash-alert"),
    # Шнек (авто-котлы)
    ("shn_roz", "Шнек: интервал (нагрев)", SensorDeviceClass.DURATION,
     UnitOfTime.SECONDS, SensorStateClass.MEASUREMENT, "mdi:timer-outline"),
    ("shn_uga", "Шнек: интервал (поддержание)", SensorDeviceClass.DURATION,
     UnitOfTime.SECONDS, SensorStateClass.MEASUREMENT, "mdi:timer-outline"),
    ("shn_roz_vkl", "Шнек: работа (нагрев)", SensorDeviceClass.DURATION,
     UnitOfTime.SECONDS, SensorStateClass.MEASUREMENT, "mdi:timer-play-outline"),
    ("shn_uga_vkl", "Шнек: работа (поддержание)", SensorDeviceClass.DURATION,
     UnitOfTime.SECONDS, SensorStateClass.MEASUREMENT, "mdi:timer-play-outline"),
    ("shn_rev_auto", "Шнек: автореверс", None, None, None, "mdi:swap-horizontal"),
    ("shn_rev_auto_i", "Шнек: интервал автореверса", None, None,
     SensorStateClass.MEASUREMENT, "mdi:swap-horizontal"),
    ("shn_rev_now", "Шнек: реверс сейчас", None, None, None, "mdi:swap-horizontal"),
    # Климат-контроль
    ("klimat_max", "Климат: максимум", None, None,
     SensorStateClass.MEASUREMENT, "mdi:thermometer-high"),
    ("klimat_min", "Климат: минимум", None, None,
     SensorStateClass.MEASUREMENT, "mdi:thermometer-low"),
    # Выносной датчик (строковый формат params)
    ("dt_use", "Выносной датчик: используется", None, None, None, "mdi:thermometer-check"),
    ("temp_dt", "Выносной датчик: температура", SensorDeviceClass.TEMPERATURE,
     UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT, None),
]

# Значения из data["climate"] (разбираются в coordinator.py):
# (ключ, название, тип значения, device_class, единица, state_class, иконка)
CLIMATE_VALUE_SENSORS = [
    ("enabled", "Климат: включён", "flag", None, None, None, "mdi:thermostat"),
    ("sensor_selected", "Климат: датчик выбран", "flag", None, None, None,
     "mdi:thermometer-check"),
    ("sensor_name", "Климат: датчик", "text", None, None, None,
     "mdi:thermometer-lines"),
    ("sensor_temp", "Климат: температура датчика", "number",
     SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS,
     SensorStateClass.MEASUREMENT, None),
    ("target_temp", "Климат: целевая температура", "number",
     SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS,
     SensorStateClass.MEASUREMENT, None),
    ("period_text", "Климат: период", "text", None, None, None,
     "mdi:calendar-clock"),
]


def _to_number(value):
    """Привести значение к числу (int/float) или вернуть None."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value) if isinstance(value, float) and value.is_integer() else value
    try:
        number = float(str(value).strip().replace(",", "."))
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _params_format(raw) -> str:
    """Определить формат строки params (для диагностики)."""
    if isinstance(raw, list):
        return "json"
    if not isinstance(raw, str) or not raw.strip():
        return "empty"
    text = raw.strip()
    if text.startswith("[") and text.endswith("]"):
        return "json"
    if "a" in text and "I" in text:
        return "markers"
    return "unknown"


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

    # --- Диагностические сенсоры (для изучения состояний) ---
    params = dev_data.get("params", {})
    for key, name, device_class, unit, state_class, icon in DIAG_PARAM_SENSORS:
        # Создаём только те параметры, которые реально приходят от прошивки.
        # Если params пуст (например, контроллер был офлайн), создаём все.
        if params and key not in params:
            continue
        sensors.append(KotelContrSensor(
            coordinator, dev_id, key, name,
            device_class, unit, state_class, icon,
            EntityCategory.DIAGNOSTIC,
        ))

    for key, name, kind, device_class, unit, state_class, icon in CLIMATE_VALUE_SENSORS:
        sensors.append(KotelClimateValueSensor(
            coordinator, dev_id, key, name, kind,
            device_class, unit, state_class, icon,
        ))

    # Сводный сенсор: все параметры и «сырая» строка params в атрибутах
    sensors.append(KotelContrDebugSensor(coordinator, dev_id))

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


class KotelClimateValueSensor(KotelBaseSensor):
    """Значение климат-контроля из data["climate"] (флаг, число или текст)."""

    def __init__(self, coordinator, dev_id, key, name, kind,
                 device_class=None, unit=None, state_class=None, icon=None):
        super().__init__(coordinator, dev_id)
        self._key = key
        self._kind = kind
        self._attr_name = name
        self._attr_unique_id = f"kotel_{dev_id}_climate_{key}"
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if icon:
            self._attr_icon = icon

    @property
    def native_value(self):
        value = self._get_device_data().get("climate", {}).get(self._key)
        if value is None or value == "":
            return None
        if self._kind == "number":
            return _to_number(value)
        if self._kind == "flag":
            return int(bool(value))
        return str(value)


class KotelContrDebugSensor(KotelBaseSensor):
    """Сводный диагностический сенсор: все параметры контроллера в атрибутах.

    Состояние — количество разобранных параметров. Если оно 0, значит
    строка params не распознана; смотрите атрибут params_raw.
    """

    def __init__(self, coordinator, dev_id):
        super().__init__(coordinator, dev_id)
        self._attr_name = "Диагностика параметров"
        self._attr_unique_id = f"kotel_{dev_id}_debug"
        self._attr_icon = "mdi:bug-outline"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        return len(self._get_device_data().get("params", {}))

    @property
    def extra_state_attributes(self):
        data = self._get_device_data()
        raw = data.get("raw") or {}
        list_raw = data.get("list_raw") or {}

        def pick(key):
            value = raw.get(key)
            return value if value is not None else list_raw.get(key)

        params_raw = pick("params")
        return {
            "params_format": _params_format(params_raw),
            "params_raw": params_raw,
            "params": dict(data.get("params", {})),
            "climate": dict(data.get("climate", {})),
            "tstatArr": pick("tstatArr"),
            "tempUstNowTstate": pick("tempUstNowTstate"),
            "mqtt_online": data.get("mqtt_online"),
            "raw_keys": sorted(raw.keys()),
            "list_raw_keys": sorted(list_raw.keys()),
        }


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
