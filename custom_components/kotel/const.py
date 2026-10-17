"""Константы интеграции Kotel."""
DOMAIN = "kotel"
DEFAULT_SCAN_INTERVAL = 30
DEFAULT_URL = "https://app.reglerpro.ru"
API_PATH = "/php/taptop.php"

CONF_LOGIN = "login"
CONF_PASSWORD = "password"
CONF_HOME_ID = "home_id"

# Типы устройств
DEVICE_TYPE_CONTR = "contr"
DEVICE_TYPE_DT = "dt"

# Действия API
ACTION_GET_ALL_DEV = "get_all_dev"
ACTION_GET_BIG_CONTR = "get_big_contr"
ACTION_GET_BIG_DT = "get_big_dt"

# Состояния работы контроллера
SOST_RAB_STOPPED = 0
SOST_RAB_RUNNING = 1
SOST_RAB_IGNITION = 2

# ---------------------------------------------------------------------------
# Климат-контроль (термостат)
# ---------------------------------------------------------------------------
# Режимы tstat_mode из tstatArr
TSTAT_MODE_CONST = 0        # постоянная температура
TSTAT_MODE_7_7 = 3          # расписание 7/7 (ПН–ВС)
TSTAT_MODE_5_2 = 4          # расписание 5/2 (ПН–ПТ, СБ–ВС)
TSTAT_MODE_4_1_2 = 5        # расписание 4/1/2 (ПН–ЧТ, ПТ, СБ–ВС)
SCHEDULE_MODES = (TSTAT_MODE_7_7, TSTAT_MODE_5_2, TSTAT_MODE_4_1_2)

# Названия пресетов (режимов расписания) в Home Assistant
PRESET_BY_MODE = {
    TSTAT_MODE_7_7: "Расписание 7/7",
    TSTAT_MODE_5_2: "Расписание 5/2",
    TSTAT_MODE_4_1_2: "Расписание 4/1/2",
}
MODE_BY_PRESET = {name: mode for mode, name in PRESET_BY_MODE.items()}

# Пределы температуры климат-контроля (как params_for_range("temp_dt") в JS)
CLIMATE_TEMP_MIN = 5.0
CLIMATE_TEMP_MAX = 40.0
CLIMATE_TEMP_STEP = 0.5

# Расписание: шаблон ключа в tstatArr -> подпись группы.
# {n} — номер периода 1..4, {kind} — "time" (час начала) или "temp" (температура).
SCHEDULE_PERIODS = (1, 2, 3, 4)
SCHEDULE_GROUPS = (
    ("m3_P{n}_{kind}", "7/7 ПН–ВС"),
    ("m4_P{n}_d5_{kind}", "5/2 ПН–ПТ"),
    ("m4_P{n}_d2_{kind}", "5/2 СБ–ВС"),
    ("m5_P{n}_d4_{kind}", "4/1/2 ПН–ЧТ"),
    ("m5_P{n}_d1_{kind}", "4/1/2 ПТ"),
    ("m5_P{n}_d2_{kind}", "4/1/2 СБ–ВС"),
)

# Действия API климат-контроля
ACTION_CLIMATE_ON_OFF = "climat_on_off"
ACTION_CLIMATE_MODE = "mode_klimat"
ACTION_CLIMATE_RANGE = "range_climat"
ACTION_CLIMATE_GET_SENSORS = "get_climat_sensors"
ACTION_CLIMATE_SELECT_SENSOR = "select_climat_sensors"
