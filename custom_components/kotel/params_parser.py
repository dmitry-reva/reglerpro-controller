"""Парсер параметров устройств — порт функции getParams() из functions.js.

Поддерживаются два формата строки params (как в оригинальном JS):
1. Строковый с буквенными маркерами:  a45b1c...  (есть символы 'a' и 'I')
2. JSON-массив чисел:                  [45,0,0,52,1,291500,...]

Публичный API (используется в coordinator.py и sensor.py):
    get_params(data)        -> dict
    get_params_slice(...)   -> int | float | default
    is_kotel_auto(vers)     -> bool
    sost_rab_text(sost_rab) -> str
    tstat_text(tstat_now)   -> str | None
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "get_params",
    "get_params_slice",
    "is_kotel_auto",
    "sost_rab_text",
    "tstat_text",
]

Number = int | float

# ---------------------------------------------------------------------------
# Описание форматов
# ---------------------------------------------------------------------------

# Формат 1 (строковый): (ключ, маркер_начала, маркер_конца).
# Значение лежит между первым вхождением маркера начала и маркера конца.
# Если значения нет — подставляется 0 (поведение исходной версии).
_MARKER_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("vers", "f", "g"),
    ("sost_rab", "e", "f"),
    ("temp_w_ust", "a", "b"),
    ("temp_w", "d", "e"),
    ("vent_on", "G", "H"),
    ("temp_w_min", "F", "G"),
    ("dt_use", "b", "c"),
    ("temp_dt", "c", "D"),
    ("err_temp_w", "g", "h"),
    ("err_temp_shnek", "h", "i"),
    ("shnek_avaria", "i", "j"),
    ("alarm_w", "j", "k"),
    ("produv_time", "A", "B"),
    ("produv_interval", "B", "C"),
    ("vent_min", "C", "D"),
    ("vent_max", "D", "E"),
    ("nasos_t", "E", "F"),
    ("shnek_temp", "H", "I"),
    ("shn_roz", "I", "J"),
    ("shn_uga", "J", "K"),
    ("time_ugas", "L", "M"),
    ("shn_rev_auto", "M", "N"),
    ("shn_rev_auto_i", "N", "O"),
    ("shn_rev_now", "O", "P"),
)

# Поля, у которых «нет данных» не должно превращаться в 0:
#  - tstatNow: 0 означает «нагрев», а отсутствие данных — это не нагрев;
#  - korect_20: иначе korect_ получился бы -20.
_MARKER_FIELDS_OPTIONAL: tuple[tuple[str, str, str], ...] = (
    ("tstatNow", "k", "l"),
    ("korect_20", "K", "L"),
)

_ALL_MARKERS: frozenset[str] = frozenset(
    ch
    for _key, start, end in (*_MARKER_FIELDS, *_MARKER_FIELDS_OPTIONAL)
    for ch in (start, end)
)

# Формат 2 (JSON-массив): индекс -> ключ.
# Индексы 1, 2 и 20 обрабатываются отдельно / не используются.
_JSON_INDEX_MAP: dict[int, str] = {
    0: "temp_w_ust",
    3: "temp_w",
    4: "sost_rab",
    5: "vers",
    6: "err_temp_w",
    7: "err_temp_shnek",
    8: "shnek_avaria",
    9: "alarm_w",
    10: "produv_time",
    11: "produv_interval",
    12: "vent_min",
    13: "vent_max",
    14: "nasos_t",
    15: "temp_w_min",
    16: "vent_on",
    17: "shnek_temp",
    18: "shn_roz",
    19: "shn_uga",
    21: "time_ugas",
    22: "shn_rev_auto",
    23: "shn_rev_auto_i",
    24: "shn_rev_now",
    25: "tstatNow",
    26: "shn_roz_vkl",
    27: "shn_uga_vkl",
    28: "servo_cooler",
    29: "simistor_err",
    30: "klimat_max",
    31: "klimat_min",
}
_JSON_KOREKT_INDEX = 20  # хранится на сервере со сдвигом +20

_SOST_RAB_TEXT: dict[int, str] = {
    0: "Остановлен",
    1: "Работает",
    2: "Розжиг/Угасание",
}


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _num(value: Any, default: Number | None = None) -> Number | None:
    """Безопасно привести значение к int/float, иначе вернуть default.

    - bool -> 0/1; None, списки, словари -> default;
    - строки разбираются как int, затем как float ("45", "45.5");
    - целые float (45.0) приводятся к int, чтобы в HA не было "45.0";
    - NaN и бесконечности отбрасываются.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return default
        return int(value) if value.is_integer() else value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return int(text)
        except ValueError:
            try:
                number = float(text)
            except ValueError:
                return default
            if not math.isfinite(number):
                return default
            return int(number) if number.is_integer() else number
    return default


def _slice_value(
    data: str,
    positions: dict[str, int],
    start: str,
    end: str,
    default: Number | None,
) -> Number | None:
    """Взять число между маркерами, используя заранее найденные позиции."""
    pos1 = positions[start]
    pos2 = positions[end]
    if pos1 == -1 or pos2 == -1 or pos2 <= pos1:
        return default
    value = _num(data[pos1 + 1:pos2])
    return default if value is None else value


# ---------------------------------------------------------------------------
# Публичные функции разбора
# ---------------------------------------------------------------------------

def get_params_slice(data: str, ind1: str, ind2: str, default: Number | None = 0):
    """Извлечь число между двумя маркерами (как getParamsSlice в JS).

    Оставлена для совместимости; внутри get_params используется ускоренный
    вариант, который ищет каждый маркер в строке один раз.
    """
    positions = {ind1: data.find(ind1), ind2: data.find(ind2)}
    return _slice_value(data, positions, ind1, ind2, default)


def _parse_marker_format(data: str) -> dict:
    """Формат 1: строка с буквенными маркерами."""
    positions = {ch: data.find(ch) for ch in _ALL_MARKERS}
    result: dict[str, Any] = {}

    for key, start, end in _MARKER_FIELDS:
        result[key] = _slice_value(data, positions, start, end, 0)

    for key, start, end in _MARKER_FIELDS_OPTIONAL:
        value = _slice_value(data, positions, start, end, None)
        if value is not None:
            result[key] = value
        elif key == "tstatNow":
            result[key] = None  # ключ есть, но значения нет -> "unknown"

    korect_20 = result.get("korect_20")
    if korect_20 is not None:
        result["korect_"] = korect_20 - 20

    return result


def _parse_array(arr: list) -> dict:
    """Формат 2: массив значений (уже разобранный из JSON)."""
    result: dict[str, Any] = {}

    for index, key in _JSON_INDEX_MAP.items():
        if index < len(arr):
            value = _num(arr[index])
            if value is not None:
                result[key] = value

    if _JSON_KOREKT_INDEX < len(arr):
        korect_20 = _num(arr[_JSON_KOREKT_INDEX])
        if korect_20 is not None:
            result["korect_20"] = korect_20
            result["korect_"] = korect_20 - 20

    return result


def get_params(data: str | list | None) -> dict:
    """Разобрать params устройства в словарь.

    Принимает строку (оба формата) либо уже готовый список. Любые
    неожиданные данные дают пустой словарь, а не исключение.
    """
    if not data:
        return {}

    if isinstance(data, list):
        return _parse_array(data)

    if not isinstance(data, str):
        _LOGGER.debug("params: неожиданный тип %s", type(data).__name__)
        return {}

    text = data.strip()

    # JSON-массив чисел не содержит букв, поэтому определяем его по скобкам
    # в начале и конце строки. Это надёжнее поиска 'a' в первых символах.
    if text.startswith("[") and text.endswith("]"):
        try:
            arr = json.loads(text)
        except ValueError:  # JSONDecodeError — подкласс ValueError
            _LOGGER.debug("params: некорректный JSON: %.60s", text)
            return {}
        if not isinstance(arr, list):
            _LOGGER.debug("params: ожидался массив, получен %s", type(arr).__name__)
            return {}
        return _parse_array(arr)

    if "a" in text and "I" in text:
        return _parse_marker_format(text)

    return {}


# ---------------------------------------------------------------------------
# Интерпретация значений
# ---------------------------------------------------------------------------

def is_kotel_auto(vers: Any) -> bool:
    """Проверка версии авто-котла (как is_kotel_auto в functions.js)."""
    version = _num(vers)
    return version is not None and 291000 < version < 292900


def sost_rab_text(sost_rab: Any) -> str:
    """Текстовое описание состояния работы контроллера."""
    value = _num(sost_rab)
    if value is None:
        return "Неизвестно"
    return _SOST_RAB_TEXT.get(int(value), "Неизвестно")


def tstat_text(tstat_now: Any) -> str | None:
    """Режим климат-контроля: 0 — нагрев, иное ненулевое — охлаждение.

    Возвращает None, если данных нет (в JS: tstatNow ? "охлаждение" : "нагрев").
    """
    value = _num(tstat_now)
    if value is None:
        return None
    return "Охлаждение" if value else "Нагрев"
