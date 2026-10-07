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
