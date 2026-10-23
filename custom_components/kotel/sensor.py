"""Сенсоры для интеграции Kotel."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory

from .const import DEVICE_TYPE_CONTR, DEVICE_TYPE_DT, DOMAIN
from .coordinator import KotelCoordinator
from .entity import KotelEntity
from .params_parser import is_kotel_auto, sost_rab_text

_LOGGER = logging.getLogger(__name__)

# Сенсоры, которые интеграция больше не создаёт (нет данных от прошивки).
# Ключи совпадают с unique_id вида kotel_<id устройства>_<ключ>.
REMOVED_SENSOR_KEYS = (
    "vent_max",
    "vent_min",
    "nasos_t",
    "produv_time",
    "produv_interval",
    "time_ugas",
    "korect_",
    "servo_cooler",
)

# Общие сенсоры контроллера: (ключ params, translation_key, device_class,
# единица, state_class, иконка).
PARAM_SENSORS = (
    ("temp_w", "water_temperature", SensorDeviceClass.TEMPERATURE,
     UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT, None),
    ("temp_w_ust", "target_temperature_max", SensorDeviceClass.TEMPERATURE,
     UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT, None),
    ("temp_w_min", "target_temperature_min", SensorDeviceClass.TEMPERATURE,
     UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT, None),
    ("shnek_temp", "auger_temperature", SensorDeviceClass.TEMPERATURE,
     UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT, None),
)

# Диагностические сенсоры: (ключ params, translation_key, device_class,
# единица, state_class, иконка).
# Для флагов/кодов state_class не задаётся: в истории HA они отображаются
# как временная шкала состояний, что удобно для изучения.
DIAG_PARAM_SENSORS = (
    ("vers", "firmware_version", None, None, None, "mdi:chip"),
    ("vent_on", "fan_on", None, None, None, "mdi:fan"),
    # Аварии и ошибки
    ("err_temp_w", "error_water_sensor", None, None, None, "mdi:alert-circle-outline"),
    ("err_temp_shnek", "error_auger_sensor", None, None, None, "mdi:alert-circle-outline"),
    ("shnek_avaria", "auger_failure", None, None, None, "mdi:alert"),
    ("alarm_w", "water_alarm", None, None, None, "mdi:alarm-light"),
    ("simistor_err", "triac_error", None, None, None, "mdi:flash-alert"),
    # Шнек (авто-котлы)
    ("shn_roz", "auger_interval_heating", SensorDeviceClass.DURATION,
     UnitOfTime.SECONDS, SensorStateClass.MEASUREMENT, "mdi:timer-outline"),
    ("shn_uga", "auger_interval_maintenance", SensorDeviceClass.DURATION,
     UnitOfTime.SECONDS, SensorStateClass.MEASUREMENT, "mdi:timer-outline"),
    ("shn_roz_vkl", "auger_run_heating", SensorDeviceClass.DURATION,
     UnitOfTime.SECONDS, SensorStateClass.MEASUREMENT, "mdi:timer-play-outline"),
    ("shn_uga_vkl", "auger_run_maintenance", SensorDeviceClass.DURATION,
     UnitOfTime.SECONDS, SensorStateClass.MEASUREMENT, "mdi:timer-play-outline"),
    ("shn_rev_auto", "auger_auto_reverse", None, None, None, "mdi:swap-horizontal"),
    ("shn_rev_auto_i", "auger_reverse_interval", None, None,
     SensorStateClass.MEASUREMENT, "mdi:swap-horizontal"),
    ("shn_rev_now", "auger_reversing", None, None, None, "mdi:swap-horizontal"),
    # Климат-контроль
    ("klimat_max", "climate_maximum", None, None,
     SensorStateClass.MEASUREMENT, "mdi:thermometer-high"),
    ("klimat_min", "climate_minimum", None, None,
     SensorStateClass.MEASUREMENT, "mdi:thermometer-low"),
    # Выносной датчик (строковый формат params)
    ("dt_use", "external_sensor_used", None, None, None, "mdi:thermometer-check"),
    ("temp_dt", "external_sensor_temperature", SensorDeviceClass.TEMPERATURE,
     UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT, None),
)

# Значения из data["climate"] (разбираются в coordinator.py):
# (ключ, translation_key, тип значения, device_class, единица, state_class, иконка).
CLIMATE_VALUE_SENSORS = (
    ("enabled", "climate_enabled", "flag", None, None, None, "mdi:thermostat"),
    ("sensor_selected", "climate_sensor_selected", "flag", None, None, None,
     "mdi:thermometer-check"),
    ("sensor_name", "climate_sensor_name", "text", None, None, None,
     "mdi:thermometer-lines"),
    ("sensor_temp", "climate_sensor_temperature", "number",
     SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS,
     SensorStateClass.MEASUREMENT, None),
    ("target_temp", "climate_target_temperature", "number",
     SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS,
     SensorStateClass.MEASUREMENT, None),
    ("period_text", "climate_period", "text", None, None, None,
     "mdi:calendar-clock"),
)

# Бинарные параметры отображаются как «нет» / «да» (исходное число — в атрибуте value).
# Значения оставлены прежними для совместимости автоматизаций; перевод состояний
# выполняется в translations/en.json.
YES_NO_OPTIONS = ["нет", "да"]
FLAG_PARAM_KEYS = frozenset({
    "vent_on",          # вентилятор включён
    "err_temp_w",       # ошибка датчика t воды
    "err_temp_shnek",   # ошибка датчика t шнека
    "shnek_avaria",     # авария шнека
    "alarm_w",          # тревога по воде
    "simistor_err",     # ошибка симистора
    "shn_rev_auto",     # автореверс шнека
    "shn_rev_now",      # реверс сейчас
    "dt_use",           # выносной датчик используется
})

STATE_OPTIONS = ["Остановлен", "Работает", "Розжиг/Угасание", "Неизвестно"]
CLIMATE_MODE_OPTIONS = ["Выключен", "Нагрев", "Охлаждение"]


def _cleanup_removed_sensors(hass, coordinator) -> None:
    """Удалить из реестра сущностей старые сенсоры из REMOVED_SENSOR_KEYS.

    Удаляются только явно перечисленные unique_id. Безопасно вызывать при
    каждом запуске: если записей уже нет, ничего не происходит.
    """
    registry = er.async_get(hass)
    for dev_id, dev_data in coordinator.data.items():
        if dev_data.get("type") != DEVICE_TYPE_CONTR:
            continue
        for key in REMOVED_SENSOR_KEYS:
            entity_id = registry.async_get_entity_id(
                "sensor", DOMAIN, f"kotel_{dev_id}_{key}"
            )
            if entity_id:
                _LOGGER.info("Удаляю устаревший сенсор %s", entity_id)
                registry.async_remove(entity_id)


def _yes_no(value):
    """0 -> «нет», любое ненулевое число -> «да», нет данных -> None."""
    number = _to_number(value)
    if number is None:
        return None
    return "да" if number else "нет"


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
            entities.append(KotelDtSensor(coordinator, dev_id))

    _cleanup_removed_sensors(hass, coordinator)
    async_add_entities(entities)


def _create_contr_sensors(coordinator, dev_id, dev_data):
    """Создать набор сенсоров для контроллера котла."""
    sensors = []

    for param_key, translation_key, device_class, unit, state_class, icon in PARAM_SENSORS:
        sensors.append(KotelParamSensor(
            coordinator, dev_id,
            param_key=param_key,
            translation_key=translation_key,
            device_class=device_class,
            unit=unit,
            state_class=state_class,
            icon=icon,
        ))

    # Режим климат-контроля (нагрев / охлаждение)
    sensors.append(KotelClimateModeSensor(coordinator, dev_id))
    # Состояние работы (текстовый enum)
    sensors.append(KotelContrStateSensor(coordinator, dev_id))
    # Статус онлайн
    sensors.append(KotelContrOnlineSensor(coordinator, dev_id))
    # Активные профили
    sensors.append(KotelActiveProfileSensor(coordinator, dev_id, "boiler"))
    sensors.append(KotelActiveProfileSensor(coordinator, dev_id, "climate"))

    # --- Диагностические сенсоры ---
    # Создаём только те параметры, которые реально приходят от прошивки.
    # Если params пуст (например, контроллер был офлайн), создаём все.
    params = dev_data.get("params", {})
    for key, translation_key, device_class, unit, state_class, icon in DIAG_PARAM_SENSORS:
        if params and key not in params:
            continue
        if key in FLAG_PARAM_KEYS:
            sensors.append(KotelFlagSensor(
                coordinator, dev_id, key, translation_key, icon,
                EntityCategory.DIAGNOSTIC,
            ))
            continue
        sensors.append(KotelParamSensor(
            coordinator, dev_id,
            param_key=key,
            translation_key=translation_key,
            device_class=device_class,
            unit=unit,
            state_class=state_class,
            icon=icon,
            entity_category=EntityCategory.DIAGNOSTIC,
        ))

    for key, translation_key, kind, device_class, unit, state_class, icon in CLIMATE_VALUE_SENSORS:
        sensors.append(KotelClimateValueSensor(
            coordinator, dev_id, key, translation_key, kind,
            device_class, unit, state_class, icon,
        ))

    # Сводный сенсор: все параметры и «сырая» строка params в атрибутах
    sensors.append(KotelContrDebugSensor(coordinator, dev_id))

    return sensors


class KotelParamSensor(KotelEntity, SensorEntity):
    """Сенсор числового параметра контроллера (значение из params)."""

    def __init__(self, coordinator, dev_id, *, param_key, translation_key,
                 device_class=None, unit=None, state_class=None, icon=None,
                 entity_category=None):
        super().__init__(
            coordinator, dev_id,
            unique_suffix=param_key,
            translation_key=translation_key,
        )
        self._param_key = param_key
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class
        if icon:
            self._attr_icon = icon
        if entity_category:
            self._attr_entity_category = entity_category

    @property
    def native_value(self):
        data = self.coordinator.data.get(self._dev_id, {})
        return (data.get("params") or {}).get(self._param_key)


class KotelFlagSensor(KotelParamSensor):
    """Бинарный параметр контроллера: «нет» / «да» (0 -> нет, иное -> да)."""

    def __init__(self, coordinator, dev_id, param_key, translation_key,
                 icon=None, entity_category=None):
        super().__init__(
            coordinator, dev_id,
            param_key=param_key,
            translation_key=translation_key,
            device_class=SensorDeviceClass.ENUM,
            icon=icon,
            entity_category=entity_category,
        )
        self._attr_options = list(YES_NO_OPTIONS)

    @property
    def native_value(self):
        return _yes_no(super().native_value)

    @property
    def extra_state_attributes(self):
        return {"value": super().native_value}


class KotelContrStateSensor(KotelEntity, SensorEntity):
    """Сенсор состояния работы контроллера (sost_rab)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATE_OPTIONS
    _attr_icon = "mdi:state-machine"

    def __init__(self, coordinator, dev_id):
        super().__init__(
            coordinator, dev_id,
            unique_suffix="state",
            translation_key="operating_state",
        )

    @property
    def native_value(self) -> str | None:
        params = self.coordinator.data.get(self._dev_id, {}).get("params") or {}
        if "sost_rab" not in params:
            return None
        return sost_rab_text(params.get("sost_rab"))

    @property
    def extra_state_attributes(self):
        params = self.coordinator.data.get(self._dev_id, {}).get("params") or {}
        attrs = {"sost_rab_raw": params.get("sost_rab")}
        if "vent_on" in params:
            attrs["vent_on"] = params["vent_on"]
        if "vers" in params:
            attrs["version"] = params["vers"]
            attrs["auto"] = is_kotel_auto(params.get("vers", 0))
        return attrs


class KotelClimateModeSensor(KotelEntity, SensorEntity):
    """Режим климат-контроля: Нагрев / Охлаждение / Выключен (tstatNow)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = CLIMATE_MODE_OPTIONS

    _ICONS = {
        "Нагрев": "mdi:fire",
        "Охлаждение": "mdi:snowflake",
        "Выключен": "mdi:thermostat-off",
    }

    def __init__(self, coordinator, dev_id):
        super().__init__(
            coordinator, dev_id,
            unique_suffix="tstat_now",
            translation_key="climate_control",
        )

    @property
    def native_value(self):
        data = self.coordinator.data.get(self._dev_id, {})
        if not (data.get("climate") or {}).get("active"):
            return "Выключен"
        now = (data.get("params") or {}).get("tstatNow")
        if now is None:
            return None          # данных нет -> unknown, а не ложный «нагрев»
        return "Охлаждение" if now else "Нагрев"

    @property
    def icon(self):
        return self._ICONS.get(self.native_value, "mdi:thermostat")

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data.get(self._dev_id, {})
        climate = data.get("climate") or {}
        params = data.get("params") or {}
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


class KotelClimateValueSensor(KotelEntity, SensorEntity):
    """Значение климат-контроля из data["climate"] (флаг, число или текст)."""

    def __init__(self, coordinator, dev_id, key, translation_key, kind,
                 device_class=None, unit=None, state_class=None, icon=None):
        super().__init__(
            coordinator, dev_id,
            unique_suffix=f"climate_{key}",
            translation_key=translation_key,
        )
        self._key = key
        self._kind = kind
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if kind == "flag":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(YES_NO_OPTIONS)
        if icon:
            self._attr_icon = icon

    @property
    def native_value(self):
        value = (self.coordinator.data.get(self._dev_id, {})
                 .get("climate", {}).get(self._key))
        if value is None or value == "":
            return None
        if self._kind == "number":
            return _to_number(value)
        if self._kind == "flag":
            return "да" if value else "нет"
        return str(value)


class KotelContrDebugSensor(KotelEntity, SensorEntity):
    """Сводный диагностический сенсор: все параметры контроллера в атрибутах.

    Состояние — количество разобранных параметров. Если оно 0, значит
    строка params не распознана; смотрите атрибут params_raw.
    """

    def __init__(self, coordinator, dev_id):
        super().__init__(
            coordinator, dev_id,
            unique_suffix="debug",
            translation_key="diagnostics",
        )
        self._attr_icon = "mdi:bug-outline"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        return len((self.coordinator.data.get(self._dev_id, {})
                    .get("params") or {}))

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data.get(self._dev_id, {})
        raw = data.get("raw") or {}
        list_raw = data.get("list_raw") or {}

        def pick(key):
            value = raw.get(key)
            return value if value is not None else list_raw.get(key)

        params_raw = pick("params")
        return {
            "params_format": _params_format(params_raw),
            "params_raw": params_raw,
            "params": dict(data.get("params") or {}),
            "climate": dict(data.get("climate") or {}),
            "tstatArr": pick("tstatArr"),
            "tempUstNowTstate": pick("tempUstNowTstate"),
            "mqtt_online": data.get("mqtt_online"),
            "raw_keys": sorted(raw.keys()),
            "list_raw_keys": sorted(list_raw.keys()),
        }


class KotelActiveProfileSensor(KotelEntity, SensorEntity):
    """Какой сохранённый профиль котла или климата соответствует текущим значениям."""

    # (translation_key, суффикс unique_id, иконка)
    _NAMES = {
        "boiler": ("active_boiler_profile", "active_profile",
                   "mdi:content-save-check"),
        "climate": ("active_climate_profile", "active_climate_profile",
                    "mdi:content-save-check-outline"),
    }

    def __init__(self, coordinator, dev_id, kind="boiler"):
        translation_key, suffix, icon = self._NAMES[kind]
        super().__init__(
            coordinator, dev_id,
            unique_suffix=suffix,
            translation_key=translation_key,
        )
        self._kind = kind
        self._attr_icon = icon

    @property
    def _manager(self):
        return self.coordinator.hass.data[DOMAIN]["_profiles"]

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self._manager.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self):
        manager = self._manager
        if not manager.profile_names(self._dev_id, self._kind):
            return "нет профилей"
        return manager.matching_profile(self._dev_id, self._kind) or "изменён"

    @property
    def extra_state_attributes(self):
        manager = self._manager
        last = manager.last_applied(self._dev_id, self._kind) or {}
        return {
            "kind": self._kind,
            "profiles": manager.profile_names(self._dev_id, self._kind),
            "selected": manager.selected(self._dev_id, self._kind),
            "last_applied": last.get("name"),
            "last_applied_at": last.get("at"),
            "last_applied_ok": last.get("ok"),
            "has_undo": manager.has_undo(self._dev_id, self._kind),
        }


class KotelContrOnlineSensor(KotelEntity, SensorEntity):
    """Сенсор статуса онлайн/офлайн контроллера."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["online", "offline"]

    def __init__(self, coordinator, dev_id):
        super().__init__(
            coordinator, dev_id,
            unique_suffix="online",
            translation_key="connection_status",
        )

    @property
    def native_value(self) -> str:
        data = self.coordinator.data.get(self._dev_id, {})
        return "online" if str(data.get("mqtt_online")) == "1" else "offline"

    @property
    def icon(self) -> str:
        return "mdi:wifi" if self.native_value == "online" else "mdi:wifi-off"


class KotelDtSensor(KotelEntity, SensorEntity):
    """Сенсор температуры датчика (dt)."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, dev_id):
        super().__init__(
            coordinator, dev_id,
            unique_suffix="temp",
            translation_key="temperature",
        )

    @property
    def native_value(self):
        return self.coordinator.data.get(self._dev_id, {}).get("temp")

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data.get(self._dev_id, {})
        last_update = data.get("time") or 0
        return {
            "last_update_seconds": last_update,
            "online": last_update < 300,
        }
