"""Профили настроек контроллера: сохранение, применение, откат, экспорт и импорт.

Профиль — именованный набор значений настроек одного контроллера. Хранится в
.storage/kotel.profiles (входит в резервные копии Home Assistant).

Профили бывают двух типов (kind), у каждого свой список, выбор и откат:
    boiler    «Профиль котла»    — область boiler (11 настроек из number.py)
    climate   «Профиль климата»  — области climate, schedule, climate_sensor

Области профиля (scope):
    boiler          настройки котла (11 параметров из number.py)
    climate         режим климат-контроля и целевая температура
    schedule        расписание климат-контроля (до 48 значений)
    climate_sensor  выбранный датчик климат-контроля

Старт/стоп и включение климат-контроля в профили не входят.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
import re
from typing import Any

from homeassistant.core import HomeAssistant, callback  # pyright: ignore[reportMissingImports]
from homeassistant.exceptions import (  # pyright: ignore[reportMissingImports]
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.storage import Store  # pyright: ignore[reportMissingImports]
from homeassistant.util import dt as dt_util  # pyright: ignore[reportMissingImports]

from .api import KotelApiError
from .const import (
    CLIMATE_TEMP_MAX,
    CLIMATE_TEMP_MIN,
    CLIMATE_TEMP_STEP,
    DOMAIN,
    PRESET_BY_MODE,
    SCHEDULE_MODES,
    TSTAT_MODE_CONST,
)
from .coordinator import to_number
from .number import NUMBER_PARAMS

_LOGGER = logging.getLogger(__name__)

# --- хранилище и файлы -------------------------------------------------------
STORAGE_VERSION = 1                    # версия формата файла Store (не меняем)
SCHEMA_VERSION = 2                     # версия схемы данных внутри файла (поле "schema")
STORAGE_KEY = f"{DOMAIN}.profiles"
EXPORT_DIR = "kotel_profiles"          # /config/kotel_profiles
EXPORT_FORMAT = "kotel-profiles"
EXPORT_VERSION = 2                     # 1 — без типов профилей, 2 — с полем kind
MAX_IMPORT_BYTES = 1_000_000
MAX_PROFILES_PER_DEVICE = 20           # на каждый тип профилей
MAX_NAME_LENGTH = 40

# --- события -----------------------------------------------------------------
EVENT_PROFILE_APPLIED = "kotel_profile_applied"
EVENT_PROFILE_SAVED = "kotel_profile_saved"
EVENT_PROFILE_DELETED = "kotel_profile_deleted"

# --- поведение применения (в тестах переопределяются) ------------------------
WRITE_DELAY = 0.3          # пауза между командами, с
VERIFY_FIRST_WAIT = 2.0    # пауза перед первой проверкой, с
VERIFY_INTERVAL = 4.0      # интервал повторных проверок, с
VERIFY_TIMEOUT = 20.0      # сколько ждать подтверждения от контроллера, с

# --- области -----------------------------------------------------------------
SCOPE_BOILER = "boiler"
SCOPE_CLIMATE = "climate"
SCOPE_SCHEDULE = "schedule"
SCOPE_SENSOR = "climate_sensor"
ALL_SCOPES = (SCOPE_BOILER, SCOPE_CLIMATE, SCOPE_SCHEDULE, SCOPE_SENSOR)
DEFAULT_SCOPES = (SCOPE_BOILER,)

# --- типы профилей -----------------------------------------------------------
KIND_BOILER = "boiler"
KIND_CLIMATE = "climate"
ALL_KINDS = (KIND_BOILER, KIND_CLIMATE)
KIND_LABELS = {KIND_BOILER: "Котёл", KIND_CLIMATE: "Климат"}
KIND_SCOPES = {
    KIND_BOILER: (SCOPE_BOILER,),
    KIND_CLIMATE: (SCOPE_CLIMATE, SCOPE_SCHEDULE, SCOPE_SENSOR),
}
# что сохраняется по умолчанию (датчик климата привязан к дому и включается отдельно)
KIND_DEFAULT_SCOPES = {
    KIND_BOILER: (SCOPE_BOILER,),
    KIND_CLIMATE: (SCOPE_CLIMATE, SCOPE_SCHEDULE),
}
# порядок записи: датчик -> режим/цель -> расписание -> настройки котла
APPLY_ORDER = (SCOPE_SENSOR, SCOPE_CLIMATE, SCOPE_SCHEDULE, SCOPE_BOILER)
SCOPE_LABELS = {
    SCOPE_BOILER: "Настройки котла",
    SCOPE_CLIMATE: "Климат: режим и цель",
    SCOPE_SCHEDULE: "Климат: расписание",
    SCOPE_SENSOR: "Климат: датчик",
}

# допустимые значения: ключ -> (минимум, максимум, шаг)
BOILER_LIMITS: dict[str, tuple[float, float, float]] = {
    key: (min_val, max_val, step)
    for key, _translation_key, min_val, max_val, step, _unit, _cat in NUMBER_PARAMS
}
VALID_MODES = (TSTAT_MODE_CONST, *SCHEDULE_MODES)
_TIME_KEY_RE = re.compile(r"^(m[345])_P([1-4])_(?:(d\d)_)?time$")
_SCHEDULE_KEY_RE = re.compile(r"^m[345]_P[1-4]_(?:d\d_)?(?:time|temp)$")
_CLIMATE_KEYS = ("tstat_mode", "tstatTemp")
_ISO_FILE_RE = re.compile(r"^[\w.\- ]+\.json$")


# =============================================================================
# Чистые функции: проверка значений, снимок, план изменений
# =============================================================================
def _check_range(value, low: float, high: float, step: float) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "значение не число"
    if not math.isfinite(value):
        return "значение не число"
    if value < low or value > high:
        return f"вне диапазона {low:g}…{high:g}"
    if abs(round(value / step) * step - value) > 1e-6:
        return f"не кратно шагу {step:g}"
    return None


def validate_value(scope: str, key: str, value) -> str | None:
    """None — значение допустимо, иначе причина отказа."""
    if scope == SCOPE_BOILER:
        limits = BOILER_LIMITS.get(key)
        return _check_range(value, *limits) if limits else "неизвестный параметр"
    if scope == SCOPE_CLIMATE:
        if key == "tstat_mode":
            return None if value in VALID_MODES else "недопустимый режим"
        if key == "tstatTemp":
            return _check_range(value, CLIMATE_TEMP_MIN, CLIMATE_TEMP_MAX, CLIMATE_TEMP_STEP)
        return "неизвестный параметр"
    if scope == SCOPE_SCHEDULE:
        if _TIME_KEY_RE.match(key):
            return _check_range(value, 0, 23, 1)
        if _SCHEDULE_KEY_RE.match(key):
            return _check_range(value, CLIMATE_TEMP_MIN, CLIMATE_TEMP_MAX, CLIMATE_TEMP_STEP)
        return "неизвестный параметр"
    if scope == SCOPE_SENSOR:
        ok = key == "tstatDtId" and isinstance(value, str) and bool(value)
        return None if ok else "недопустимый идентификатор датчика"
    return "неизвестная область"


def values_equal(first, second) -> bool:
    if isinstance(first, (int, float)) and isinstance(second, (int, float)):
        return abs(first - second) < 1e-9
    return first == second


def capture_values(data: dict, scopes) -> dict[str, dict]:
    """Снять текущие значения выбранных областей из данных координатора."""
    params = data.get("params", {}) or {}
    climate = data.get("climate", {}) or {}
    result: dict[str, dict] = {}

    if SCOPE_BOILER in scopes:
        boiler = {}
        for key in BOILER_LIMITS:
            value = to_number(params.get(key))
            if value is not None:
                boiler[key] = value
        result[SCOPE_BOILER] = boiler

    if SCOPE_CLIMATE in scopes:
        values = {}
        mode = to_number(climate.get("mode"))
        target = to_number(climate.get("target_const"))
        if mode is not None:
            values["tstat_mode"] = mode
        if target is not None:
            values["tstatTemp"] = target
        result[SCOPE_CLIMATE] = values

    if SCOPE_SCHEDULE in scopes:
        schedule = {}
        for key, raw in (climate.get("schedule") or {}).items():
            value = to_number(raw)
            if value is not None and _SCHEDULE_KEY_RE.match(key):
                schedule[key] = value
        result[SCOPE_SCHEDULE] = schedule

    if SCOPE_SENSOR in scopes:
        sensor_id = climate.get("sensor_id")
        result[SCOPE_SENSOR] = {"tstatDtId": str(sensor_id)} if sensor_id else {}

    return result


def build_plan(target: dict, current: dict, scopes) -> tuple[list[dict], list[dict], int]:
    """Сравнить профиль с текущими значениями.

    Возвращает (изменения, пропущенные, число совпавших).
    """
    changes: list[dict] = []
    skipped: list[dict] = []
    unchanged = 0
    for scope in scopes:
        current_scope = current.get(scope, {})
        for key, new in target.get(scope, {}).items():
            # настройки котла, которых нет в данных, прошивка не поддерживает;
            # ключи климата/расписания могут ещё не существовать на сервере
            if key not in current_scope and scope == SCOPE_BOILER:
                skipped.append({"scope": scope, "key": key,
                                "reason": "параметр не поддерживается прошивкой"})
                continue
            old = current_scope.get(key)
            if old is not None and values_equal(old, new):
                unchanged += 1
                continue
            reason = validate_value(scope, key, new)
            if reason:
                skipped.append({"scope": scope, "key": key, "reason": reason})
                continue
            changes.append({"scope": scope, "key": key, "from": old, "to": new})
    return changes, skipped, unchanged


def _time_group(key: str):
    """(режим, день) и номер периода для ключа времени: m4_P2_d5_time -> (("m4","d5"), 2)."""
    match = _TIME_KEY_RE.match(key)
    if not match:
        return (None, None), None
    return (match.group(1), match.group(3)), int(match.group(2))


def split_time_groups(schedule_changes: list[dict], current: dict, target: dict):
    """Проверить порядок времени периодов и отсортировать записи безопасно.

    Время периодов должно строго возрастать (P1 < P2 < P3 < P4). Чтобы это
    правило не нарушалось ни на одном промежуточном шаге, сначала понижаем
    значения по возрастанию периодов, затем повышаем по убыванию.
    Возвращает (упорядоченные изменения, пропущенные).
    """
    temps = [c for c in schedule_changes if not _TIME_KEY_RE.match(c["key"])]
    groups: dict[tuple, list[dict]] = {}
    for change in schedule_changes:
        if _TIME_KEY_RE.match(change["key"]):
            group, _ = _time_group(change["key"])
            groups.setdefault(group, []).append(change)

    ordered = list(temps)
    skipped: list[dict] = []
    current_schedule = current.get(SCOPE_SCHEDULE, {})
    for (mode, day), items in groups.items():
        def key_for(period: int) -> str:
            middle = f"{day}_" if day else ""
            return f"{mode}_P{period}_{middle}time"

        new_by_period = {_time_group(c["key"])[1]: c["to"] for c in items}
        final = []
        for period in (1, 2, 3, 4):
            value = new_by_period.get(period, current_schedule.get(key_for(period)))
            final.append(value)
        if all(v is not None for v in final) and any(
            final[i] >= final[i + 1] for i in range(3)
        ):
            for change in items:
                skipped.append({"scope": SCOPE_SCHEDULE, "key": change["key"],
                                "reason": "нарушен порядок времени: периоды должны идти по возрастанию"})
            continue

        def period_of(change):
            return _time_group(change["key"])[1]

        decreases = sorted(
            (c for c in items if c["from"] is not None and c["to"] < c["from"]),
            key=period_of,
        )
        increases = sorted(
            (c for c in items if c["from"] is None or c["to"] > c["from"]),
            key=period_of, reverse=True,
        )
        ordered.extend(decreases + increases)
    return ordered, skipped


def order_changes(changes: list[dict], current: dict, target: dict) -> tuple[list[dict], list[dict]]:
    """Упорядочить изменения по областям и безопасно для расписания."""
    ordered: list[dict] = []
    skipped: list[dict] = []
    for scope in APPLY_ORDER:
        items = [c for c in changes if c["scope"] == scope]
        if scope == SCOPE_CLIMATE:
            items.sort(key=lambda c: 0 if c["key"] == "tstat_mode" else 1)   # режим раньше цели
        elif scope == SCOPE_SCHEDULE:
            items, extra_skipped = split_time_groups(items, current, target)
            skipped.extend(extra_skipped)
        ordered.extend(items)
    return ordered, skipped


def _wire(value):
    """Целые числа отправляем как int (24, а не 24.0)."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


# =============================================================================
# Типы профилей, миграция, очистка данных при импорте
# =============================================================================
def infer_kind(scopes) -> str | None:
    """Тип профиля по областям: boiler / climate; None — области смешаны или пусты."""
    scopes = list(scopes or [])
    if not scopes:
        return None
    for kind in ALL_KINDS:
        if all(s in KIND_SCOPES[kind] for s in scopes):
            return kind
    return None


def split_values_by_kind(values: dict) -> dict[str, dict]:
    """{область: значения} -> {тип: {область: значения}} (только непустые)."""
    result: dict[str, dict] = {}
    for kind in ALL_KINDS:
        part = {scope: copy.deepcopy(block) for scope, block in values.items()
                if scope in KIND_SCOPES[kind] and block}
        if part:
            result[kind] = part
    return result


def split_profile(profile: dict) -> dict[str, dict]:
    """Разделить профиль версии 1.3.0 (мог содержать и котёл, и климат) по типам."""
    result: dict[str, dict] = {}
    for kind, part in split_values_by_kind(profile.get("values", {})).items():
        item = copy.deepcopy(profile)
        item["values"] = part
        item["scopes"] = [s for s in ALL_SCOPES if s in part]
        result[kind] = item
    return result


def _empty_kind() -> dict:
    return {"profiles": {}, "selected": None, "last_applied": None, "undo": None}


def _empty_device() -> dict:
    return {kind: _empty_kind() for kind in ALL_KINDS}


def migrate_data(data: dict) -> tuple[dict, bool]:
    """Привести данные хранилища к текущей схеме. Возвращает (данные, изменено)."""
    if not isinstance(data, dict) or not isinstance(data.get("devices"), dict):
        return {"version": STORAGE_VERSION, "schema": SCHEMA_VERSION, "devices": {}}, False
    if data.get("schema", 1) >= SCHEMA_VERSION:
        return data, False

    migrated: dict[str, Any] = {"version": STORAGE_VERSION, "schema": SCHEMA_VERSION,
                                "devices": {}}
    for dev_id, old in data["devices"].items():
        device = _empty_device()
        for name, profile in (old.get("profiles") or {}).items():
            for kind, part in split_profile(profile).items():
                device[kind]["profiles"][name] = part
        selected = old.get("selected")
        last = old.get("last_applied")
        undo = old.get("undo")
        for kind in ALL_KINDS:
            if selected in device[kind]["profiles"]:
                device[kind]["selected"] = selected
            if isinstance(last, dict) and last.get("name") in device[kind]["profiles"]:
                device[kind]["last_applied"] = dict(last)
        if isinstance(undo, dict) and isinstance(undo.get("values"), dict):
            for kind, part in split_values_by_kind(undo["values"]).items():
                device[kind]["undo"] = {**undo, "values": part}
        migrated["devices"][dev_id] = device
    return migrated, True


def clean_name(raw) -> str:
    if not isinstance(raw, str):
        raise ValueError("имя профиля должно быть строкой")
    name = re.sub(r"[\x00-\x1f\x7f]", " ", raw)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        raise ValueError("имя профиля не может быть пустым")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"имя профиля длиннее {MAX_NAME_LENGTH} символов")
    return name


def clean_values(raw) -> dict[str, dict]:
    """Оставить только известные области и ключи; значения — числа/строки."""
    if not isinstance(raw, dict):
        raise ValueError("values должен быть объектом")
    cleaned: dict[str, dict] = {}
    for scope in ALL_SCOPES:
        block = raw.get(scope)
        if not isinstance(block, dict):
            continue
        items = {}
        for key, value in block.items():
            if scope == SCOPE_SENSOR:
                if key == "tstatDtId" and isinstance(value, str) and value:
                    items[key] = value[:64]
                continue
            number = to_number(value) if not isinstance(value, bool) else None
            if number is None:
                continue
            known = (
                key in BOILER_LIMITS if scope == SCOPE_BOILER
                else key in _CLIMATE_KEYS if scope == SCOPE_CLIMATE
                else bool(_SCHEDULE_KEY_RE.match(key))
            )
            if known:
                items[key] = number
        if items:
            cleaned[scope] = items
    if not cleaned:
        raise ValueError("в профиле нет известных параметров")
    return cleaned


def clean_profile(raw) -> list[dict]:
    """Проверить профиль из файла. Возвращает 1 запись (или 2 для файлов версии 1,
    где котёл и климат хранились в одном профиле)."""
    if not isinstance(raw, dict):
        raise ValueError("профиль должен быть объектом")
    name = clean_name(raw.get("name"))
    values = clean_values(raw.get("values"))

    raw_kind = raw.get("kind")
    if raw_kind is None:                       # файл версии 1: определяем тип по областям
        parts = split_values_by_kind(values)
    elif raw_kind in ALL_KINDS:
        part = {s: v for s, v in values.items() if s in KIND_SCOPES[raw_kind]}
        if not part:
            raise ValueError(f"в профиле типа «{KIND_LABELS[raw_kind]}» нет подходящих параметров")
        parts = {raw_kind: part}
    else:
        raise ValueError("неизвестный тип профиля")

    firmware = to_number(raw.get("firmware"))
    note = raw.get("note")
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    mode = to_number(raw.get("mode"))
    result = []
    for kind, part in parts.items():
        result.append({
            "kind": kind,
            "name": name,
            "note": note.strip()[:200] if isinstance(note, str) else "",
            "firmware": firmware,
            "scopes": [s for s in ALL_SCOPES if s in part],
            "values": part,
            "mode": mode if kind == KIND_CLIMATE and mode in VALID_MODES else None,
            "all_schedules": raw.get("all_schedules") is True,
            "source": {
                "device_name": str(source.get("device_name", ""))[:60],
                "device_id": str(source.get("device_id", ""))[:32],
            },
            "created": raw.get("created") if isinstance(raw.get("created"), str) else None,
            "updated": raw.get("updated") if isinstance(raw.get("updated"), str) else None,
        })
    return result


def filter_schedule_by_mode(schedule: dict, mode) -> dict:
    """Оставить в расписании ключи только активного режима (m3_* / m4_* / m5_*)."""
    if mode in SCHEDULE_MODES:
        prefix = f"m{int(mode)}_"
        return {k: v for k, v in schedule.items() if k.startswith(prefix)}
    if mode == TSTAT_MODE_CONST:
        return {}                              # в режиме постоянной температуры расписание не действует
    return dict(schedule)                      # режим неизвестен — ничего не отбрасываем


def _mode_label(mode) -> str:
    return PRESET_BY_MODE.get(mode, "постоянная температура")


def _parse_key(key: str) -> tuple[str, str, str]:
    """Ключ выбора "id|тип|имя" (допускается прежний вид "id|имя" = котёл)."""
    dev_id, _, rest = key.partition("|")
    kind, _, name = rest.partition("|")
    if kind in ALL_KINDS and name:
        return dev_id, kind, name
    return dev_id, KIND_BOILER, rest


def _write_json_file(directory: str, filename: str, payload: dict) -> str:
    """Записать JSON; если файл с таким именем уже есть, добавить _2, _3 ... Возвращает путь."""
    os.makedirs(directory, exist_ok=True)
    base, ext = os.path.splitext(filename)
    path, counter = os.path.join(directory, filename), 2
    while os.path.exists(path):
        path = os.path.join(directory, f"{base}_{counter}{ext}")
        counter += 1
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return path


def _read_json_file(path: str) -> Any:
    if os.path.getsize(path) > MAX_IMPORT_BYTES:
        raise ValueError("файл слишком большой")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _list_json_files(directory: str) -> list[str]:
    if not os.path.isdir(directory):
        return []
    files = [f for f in os.listdir(directory) if _ISO_FILE_RE.match(f)]
    files.sort(key=lambda f: os.path.getmtime(os.path.join(directory, f)), reverse=True)
    return files


# =============================================================================
# Менеджер профилей
# =============================================================================
class KotelProfileManager:
    """Хранит профили всех контроллеров и применяет их через API.

    Профили котла и климата живут в отдельных списках: у каждого типа свой
    выбор, история применения и откат.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, Any] = {"version": STORAGE_VERSION,
                                      "schema": SCHEMA_VERSION, "devices": {}}
        self._entries: dict[str, tuple[Any, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._drafts: dict[tuple[str, str], str] = {}
        self._listeners: list = []

    # ------------------------------------------------------------------ хранилище
    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data is None:
            return
        self._data, changed = migrate_data(data)
        if changed:
            _LOGGER.info("Профили Kotel перенесены в схему %s: котёл и климат разделены",
                         SCHEMA_VERSION)
            await self._store.async_save(self._data)

    async def _async_save(self) -> None:
        await self._store.async_save(self._data)
        self._notify()

    @callback
    def async_add_listener(self, update_callback):
        self._listeners.append(update_callback)

        @callback
        def remove():
            if update_callback in self._listeners:
                self._listeners.remove(update_callback)

        return remove

    @callback
    def _notify(self) -> None:
        for update_callback in list(self._listeners):
            update_callback()

    # ------------------------------------------------------------------ контроллеры
    def register_entry(self, entry_id: str, coordinator, api) -> None:
        self._entries[entry_id] = (coordinator, api)

    def unregister_entry(self, entry_id: str) -> None:
        self._entries.pop(entry_id, None)

    @property
    def has_entries(self) -> bool:
        return bool(self._entries)

    def controllers(self) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for coordinator, api in self._entries.values():
            for dev_id, dev in (coordinator.data or {}).items():
                if dev.get("type") == "contr":
                    result[dev_id] = {"name": dev.get("name", dev_id),
                                      "coordinator": coordinator, "api": api}
        return result

    def _runtime(self, dev_id: str):
        info = self.controllers().get(dev_id)
        if info is None:
            raise ServiceValidationError(f"Контроллер {dev_id} не найден")
        return info["coordinator"], info["api"]

    def device_name(self, dev_id: str) -> str:
        info = self.controllers().get(dev_id)
        return info["name"] if info else dev_id

    def _lock(self, dev_id: str) -> asyncio.Lock:
        # одна блокировка на контроллер: профили котла и климата не пишут одновременно
        return self._locks.setdefault(dev_id, asyncio.Lock())

    # ------------------------------------------------------------------ чтение
    def _device(self, dev_id: str, create: bool = False) -> dict:
        devices = self._data["devices"]
        if dev_id not in devices and create:
            devices[dev_id] = _empty_device()
        return devices.get(dev_id) or _empty_device()

    def _kind(self, dev_id: str, kind: str, create: bool = False) -> dict:
        if kind not in ALL_KINDS:
            raise ServiceValidationError(f"Неизвестный тип профиля: {kind}")
        return self._device(dev_id, create=create).setdefault(kind, _empty_kind())

    def profile_names(self, dev_id: str, kind: str = KIND_BOILER) -> list[str]:
        return sorted(self._kind(dev_id, kind)["profiles"], key=str.casefold)

    def get_profile(self, dev_id: str, name: str, kind: str = KIND_BOILER) -> dict | None:
        return self._kind(dev_id, kind)["profiles"].get(name)

    def resolve_kind(self, dev_id: str, name: str, kind: str | None = None) -> str:
        """Определить тип профиля: явный, либо по имени, если оно есть только в одном списке."""
        if kind is not None:
            if kind not in ALL_KINDS:
                raise ServiceValidationError(f"Неизвестный тип профиля: {kind}")
            return kind
        found = [k for k in ALL_KINDS if name in self._kind(dev_id, k)["profiles"]]
        if len(found) > 1:
            raise ServiceValidationError(
                f"Профиль «{name}» есть и у котла, и у климата: укажите kind (boiler или climate)")
        return found[0] if found else KIND_BOILER

    def selected(self, dev_id: str, kind: str = KIND_BOILER) -> str | None:
        data = self._kind(dev_id, kind)
        return data["selected"] if data.get("selected") in data["profiles"] else None

    def last_applied(self, dev_id: str, kind: str = KIND_BOILER) -> dict | None:
        return self._kind(dev_id, kind).get("last_applied")

    def has_undo(self, dev_id: str, kind: str = KIND_BOILER) -> bool:
        return bool(self._kind(dev_id, kind).get("undo"))

    def draft_name(self, dev_id: str, kind: str = KIND_BOILER) -> str:
        return self._drafts.get((dev_id, kind), "")

    @callback
    def set_draft_name(self, dev_id: str, value: str, kind: str = KIND_BOILER) -> None:
        self._drafts[(dev_id, kind)] = value
        self._notify()

    def matching_profile(self, dev_id: str, kind: str = KIND_BOILER) -> str | None:
        """Имя профиля данного типа, чьи значения совпадают с текущими, иначе None."""
        info = self.controllers().get(dev_id)
        if not info:
            return None
        data = info["coordinator"].data.get(dev_id, {})
        for name in self.profile_names(dev_id, kind):
            profile = self._kind(dev_id, kind)["profiles"][name]
            current = capture_values(data, profile["scopes"])
            matches = True
            for scope, block in profile["values"].items():
                for key, value in block.items():
                    if key not in current.get(scope, {}) or not values_equal(
                            current[scope][key], value):
                        matches = False
                        break
                if not matches:
                    break
            if matches and profile["values"]:
                return name
        return None

    def profile_choices(self) -> dict[str, str]:
        """{"id|тип|имя": подпись} — для окон выбора."""
        many = len(self._data["devices"]) > 1
        choices = {}
        for dev_id, device in self._data["devices"].items():
            for kind in ALL_KINDS:
                profiles = (device.get(kind) or {}).get("profiles", {})
                for name in sorted(profiles, key=str.casefold):
                    label = f"{KIND_LABELS[kind]} · {name}"
                    if many:
                        label = f"{self.device_name(dev_id)}: {label}"
                    choices[f"{dev_id}|{kind}|{name}"] = label
        return choices

    # ------------------------------------------------------------------ сохранение
    def _snapshot(self, dev_id: str, scopes) -> dict[str, dict]:
        coordinator, _ = self._runtime(dev_id)
        data = coordinator.data.get(dev_id)
        if not data:
            raise HomeAssistantError("Нет данных контроллера")
        return capture_values(data, scopes)

    def _current_mode(self, dev_id: str):
        coordinator, _ = self._runtime(dev_id)
        return to_number(coordinator.data.get(dev_id, {}).get("climate", {}).get("mode"))

    async def async_save_profile(self, dev_id: str, name: str, scopes=None,
                                 note: str = "", overwrite: bool = False, *,
                                 kind: str | None = None,
                                 all_schedules: bool = False) -> dict:
        """Сохранить текущие значения как профиль.

        kind не указан — определяется по областям (по умолчанию «котёл»).
        Для профиля климата расписание сохраняется только для активного режима
        (7/7, 5/2 или 4/1/2); all_schedules=True сохраняет все наборы.
        """
        try:
            name = clean_name(name)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

        if scopes is not None:
            scopes = [s for s in ALL_SCOPES if s in scopes]
        if kind is None:
            kind = infer_kind(scopes) if scopes else KIND_BOILER
            if kind is None:
                raise ServiceValidationError(
                    "Профили котла и климата сохраняются отдельно: укажите kind "
                    "(boiler или climate) и подходящие области")
        elif kind not in ALL_KINDS:
            raise ServiceValidationError(f"Неизвестный тип профиля: {kind}")
        if scopes is None:
            scopes = list(KIND_DEFAULT_SCOPES[kind])
        wrong = [SCOPE_LABELS[s] for s in scopes if s not in KIND_SCOPES[kind]]
        if wrong or not scopes:
            raise ServiceValidationError(
                f"Области не подходят для профиля типа «{KIND_LABELS[kind]}»: "
                + (", ".join(wrong) or "не выбраны"))

        bucket = self._kind(dev_id, kind, create=True)
        exists = name in bucket["profiles"]
        if exists and not overwrite:
            raise ServiceValidationError(
                f"Профиль «{name}» уже существует (используйте «Обновить профиль» или overwrite)")
        if not exists and len(bucket["profiles"]) >= MAX_PROFILES_PER_DEVICE:
            raise ServiceValidationError(
                f"Достигнут предел: {MAX_PROFILES_PER_DEVICE} профилей типа «{KIND_LABELS[kind]}»")

        values = self._snapshot(dev_id, scopes)
        mode = self._current_mode(dev_id)
        schedule_skipped = False
        if kind == KIND_CLIMATE and SCOPE_SCHEDULE in values and not all_schedules:
            filtered = filter_schedule_by_mode(values[SCOPE_SCHEDULE], mode)
            if filtered:
                values[SCOPE_SCHEDULE] = filtered
            else:                              # расписание не действует в текущем режиме
                del values[SCOPE_SCHEDULE]
                scopes = [s for s in scopes if s != SCOPE_SCHEDULE]
                schedule_skipped = True
        if not scopes:
            raise ServiceValidationError(
                "В режиме постоянной температуры расписание не сохраняется; "
                "добавьте all_schedules, чтобы сохранить все наборы")
        empty = [SCOPE_LABELS[s] for s in scopes if not values.get(s)]
        if empty:
            raise ServiceValidationError(
                "Нет данных для области: " + ", ".join(empty)
                + ". Контроллер не отдаёт эти параметры.")

        coordinator, _ = self._runtime(dev_id)
        params = coordinator.data[dev_id].get("params", {})
        now = dt_util.utcnow().isoformat(timespec="seconds")
        old = bucket["profiles"].get(name, {})
        bucket["profiles"][name] = {
            "created": old.get("created", now),
            "updated": now,
            "note": note.strip()[:200] if note else old.get("note", ""),
            "firmware": to_number(params.get("vers")),
            "scopes": scopes,
            "values": values,
            "mode": mode if kind == KIND_CLIMATE else None,
            "all_schedules": bool(all_schedules),
            "source": {"device_id": dev_id, "device_name": self.device_name(dev_id)},
        }
        bucket["selected"] = name
        await self._async_save()
        self.hass.bus.async_fire(EVENT_PROFILE_SAVED, {
            "device_id": dev_id, "kind": kind, "profile": name,
            "scopes": scopes, "overwritten": exists})
        return {"profile": name, "kind": kind, "scopes": scopes,
                "values": sum(len(v) for v in values.values()),
                "overwritten": exists, "schedule_skipped": schedule_skipped}

    async def async_update_profile(self, dev_id: str, name: str, *,
                                   kind: str = KIND_BOILER) -> dict:
        profile = self.get_profile(dev_id, name, kind)
        if profile is None:
            raise ServiceValidationError(f"Профиль «{name}» не найден")
        if (kind == KIND_CLIMATE and SCOPE_SCHEDULE in profile["scopes"]
                and not profile.get("all_schedules")):
            saved, current = profile.get("mode"), self._current_mode(dev_id)
            if saved is not None and current is not None and saved != current:
                raise ServiceValidationError(
                    f"Профиль «{name}» сохранён для режима «{_mode_label(saved)}», "
                    f"а сейчас включён «{_mode_label(current)}». Переключите режим "
                    "или сохраните новый профиль")
        return await self.async_save_profile(
            dev_id, name, scopes=profile["scopes"], overwrite=True, kind=kind,
            all_schedules=bool(profile.get("all_schedules")))

    async def async_delete_profile(self, dev_id: str, name: str, *,
                                   kind: str | None = None) -> None:
        kind = self.resolve_kind(dev_id, name, kind)
        bucket = self._kind(dev_id, kind)
        if name not in bucket["profiles"]:
            raise ServiceValidationError(f"Профиль «{name}» не найден")
        del bucket["profiles"][name]
        if bucket.get("selected") == name:
            bucket["selected"] = None
        await self._async_save()
        self.hass.bus.async_fire(EVENT_PROFILE_DELETED, {
            "device_id": dev_id, "kind": kind, "profile": name})

    async def async_rename_profile(self, dev_id: str, name: str, new_name: str, *,
                                   kind: str | None = None) -> None:
        kind = self.resolve_kind(dev_id, name, kind)
        bucket = self._kind(dev_id, kind)
        if name not in bucket["profiles"]:
            raise ServiceValidationError(f"Профиль «{name}» не найден")
        try:
            new_name = clean_name(new_name)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        if new_name != name and new_name.casefold() in (
                n.casefold() for n in bucket["profiles"]):
            raise ServiceValidationError(f"Профиль «{new_name}» уже существует")
        bucket["profiles"][new_name] = bucket["profiles"].pop(name)
        if bucket.get("selected") == name:
            bucket["selected"] = new_name
        await self._async_save()

    async def async_set_selected(self, dev_id: str, name: str | None, *,
                                 kind: str = KIND_BOILER) -> None:
        bucket = self._kind(dev_id, kind, create=True)
        if name is not None and name not in bucket["profiles"]:
            raise ServiceValidationError(f"Профиль «{name}» не найден")
        bucket["selected"] = name
        await self._async_save()

    # ------------------------------------------------------------------ применение
    async def async_apply(self, dev_id: str, name: str, scopes=None, *,
                          kind: str | None = None, dry_run: bool = False,
                          force: bool = False, verify: bool = True,
                          backup: bool = True) -> dict:
        kind = self.resolve_kind(dev_id, name, kind)
        profile = self.get_profile(dev_id, name, kind)
        if profile is None:
            raise ServiceValidationError(f"Профиль «{name}» не найден")
        use_scopes = [s for s in APPLY_ORDER
                      if s in (scopes or profile["scopes"]) and s in profile["values"]]
        if not use_scopes:
            raise ServiceValidationError("В профиле нет выбранных областей")
        report = await self._apply_values(
            dev_id, profile["values"], use_scopes, label=name, kind=kind,
            dry_run=dry_run, force=force, verify=verify, backup=backup)
        report["kind"] = kind
        if not dry_run:
            self._kind(dev_id, kind, create=True)["last_applied"] = {
                "name": name, "at": dt_util.utcnow().isoformat(timespec="seconds"),
                "ok": report["ok"]}
            await self._async_save()
            self.hass.bus.async_fire(EVENT_PROFILE_APPLIED, {
                "device_id": dev_id, "kind": kind, "profile": name, "ok": report["ok"],
                "applied": report["applied"], "failed": len(report["failed"]),
                "unverified": len(report["unverified"])})
        return report

    async def async_undo(self, dev_id: str, *, kind: str = KIND_BOILER,
                         force: bool = False, verify: bool = True) -> dict:
        if kind not in ALL_KINDS:
            raise ServiceValidationError(f"Неизвестный тип профиля: {kind}")
        undo = self._kind(dev_id, kind).get("undo")
        if not undo:
            raise ServiceValidationError(
                f"Нет сохранённого состояния для отката ({KIND_LABELS[kind].lower()})")
        report = await self._apply_values(
            dev_id, undo["values"], [s for s in APPLY_ORDER if s in undo["values"]],
            label=f"откат «{undo.get('profile', '')}»", kind=kind,
            dry_run=False, force=force, verify=verify, backup=False)
        report["kind"] = kind
        if report["ok"]:
            self._kind(dev_id, kind)["undo"] = None
        await self._async_save()
        self.hass.bus.async_fire(EVENT_PROFILE_APPLIED, {
            "device_id": dev_id, "kind": kind, "profile": None, "undo": True,
            "ok": report["ok"], "applied": report["applied"],
            "failed": len(report["failed"]), "unverified": len(report["unverified"])})
        return report

    async def _apply_values(self, dev_id: str, target: dict, scopes: list[str], *,
                            label: str, kind: str, dry_run: bool, force: bool,
                            verify: bool, backup: bool) -> dict:
        coordinator, api = self._runtime(dev_id)
        async with self._lock(dev_id):
            await coordinator.async_refresh()
            data = coordinator.data.get(dev_id)
            if not data:
                raise HomeAssistantError("Нет данных контроллера")
            if not force and str(data.get("mqtt_online")) != "1":
                raise HomeAssistantError(
                    "Контроллер не в сети: применение невозможно (параметр force отключает проверку)")

            current = capture_values(data, scopes)
            changes, skipped, unchanged = build_plan(target, current, scopes)
            ordered, extra_skipped = order_changes(changes, current, target)
            skipped.extend(extra_skipped)
            skipped_keys = {(s["scope"], s["key"]) for s in extra_skipped}
            ordered = [c for c in ordered if (c["scope"], c["key"]) not in skipped_keys]

            report: dict[str, Any] = {
                "profile": label, "device_id": dev_id, "dry_run": dry_run,
                "changes": [{"scope": c["scope"], "key": c["key"],
                             "from": c["from"], "to": c["to"]} for c in ordered],
                "unchanged": unchanged, "skipped": skipped,
                "applied": 0, "failed": [], "unverified": [], "ok": True,
            }
            if dry_run or not ordered:
                return report

            if backup:
                undo_values: dict[str, dict] = {}
                for change in ordered:
                    if change["from"] is not None:
                        undo_values.setdefault(change["scope"], {})[change["key"]] = change["from"]
                self._kind(dev_id, kind, create=True)["undo"] = {
                    "profile": label, "values": undo_values,
                    "at": dt_util.utcnow().isoformat(timespec="seconds")}

            written: list[dict] = []
            blocked_groups: set[tuple] = set()
            for change in ordered:
                group = None
                if change["scope"] == SCOPE_SCHEDULE and _TIME_KEY_RE.match(change["key"]):
                    group = _time_group(change["key"])[0]
                    if group in blocked_groups:
                        report["skipped"].append({
                            "scope": change["scope"], "key": change["key"],
                            "reason": "пропущено после ошибки в той же группе времени"})
                        continue
                try:
                    await self._write(api, dev_id, change)
                except KotelApiError as err:
                    report["failed"].append({"scope": change["scope"], "key": change["key"],
                                             "error": str(err)})
                    if group is not None:
                        blocked_groups.add(group)
                    continue
                written.append(change)
                report["applied"] += 1
                if WRITE_DELAY:
                    await asyncio.sleep(WRITE_DELAY)

            if verify and written:
                report["unverified"] = await self._verify(coordinator, dev_id, written, scopes)

            report["ok"] = not report["failed"] and not report["unverified"]
            return report

    async def _write(self, api, dev_id: str, change: dict) -> None:
        scope, key, value = change["scope"], change["key"], _wire(change["to"])
        if scope == SCOPE_BOILER:
            await api.send_command(key, dev_id, value)
        elif scope == SCOPE_CLIMATE:
            if key == "tstat_mode":
                await api.set_climate_mode(dev_id, int(value))
            else:
                await api.set_climate_value(dev_id, "temp_dt", value)
        elif scope == SCOPE_SCHEDULE:
            await api.set_climate_value(dev_id, key, value)
        elif scope == SCOPE_SENSOR:
            await api.select_climate_sensor(dev_id, str(value))

    async def _verify(self, coordinator, dev_id: str, written: list[dict],
                      scopes: list[str]) -> list[dict]:
        """Дождаться, пока контроллер вернёт записанные значения."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + VERIFY_TIMEOUT
        pending = list(written)
        wait = VERIFY_FIRST_WAIT
        while pending:
            await asyncio.sleep(wait)
            wait = VERIFY_INTERVAL
            await coordinator.async_refresh()
            current = capture_values(coordinator.data.get(dev_id, {}), scopes)
            pending = [c for c in pending
                       if not values_equal(current.get(c["scope"], {}).get(c["key"]), c["to"])]
            if loop.time() >= deadline:
                break
        return [{"scope": c["scope"], "key": c["key"], "expected": c["to"]} for c in pending]

    # ------------------------------------------------------------------ экспорт
    def _export_dir(self, to_www: bool) -> str:
        if to_www:
            return self.hass.config.path("www", EXPORT_DIR)
        return self.hass.config.path(EXPORT_DIR)

    async def async_export(self, keys: list[str] | None = None, *,
                           to_www: bool = False) -> dict:
        """Записать выбранные профили в JSON-файл. keys — "id|тип|имя" (None = все)."""
        wanted = None if keys is None else {_parse_key(k) for k in keys}
        items = []
        for dev_id, device in self._data["devices"].items():
            for kind in ALL_KINDS:
                profiles = (device.get(kind) or {}).get("profiles", {})
                for name in sorted(profiles, key=str.casefold):
                    if wanted is not None and (dev_id, kind, name) not in wanted:
                        continue
                    profile = profiles[name]
                    items.append({
                        "kind": kind,
                        "name": name,
                        "note": profile.get("note", ""),
                        "firmware": profile.get("firmware"),
                        "scopes": profile["scopes"],
                        "values": profile["values"],
                        "mode": profile.get("mode"),
                        "all_schedules": bool(profile.get("all_schedules")),
                        "created": profile.get("created"),
                        "updated": profile.get("updated"),
                        "source": {"device_id": dev_id,
                                   "device_name": self.device_name(dev_id)},
                    })
        if not items:
            raise ServiceValidationError("Нет профилей для экспорта")

        now = dt_util.now()
        payload = {
            "format": EXPORT_FORMAT,
            "version": EXPORT_VERSION,
            "exported": now.isoformat(timespec="seconds"),
            "profiles": items,
        }
        filename = f"kotel_profiles_{now.strftime('%Y%m%d_%H%M%S')}.json"
        directory = self._export_dir(to_www)
        path = await self.hass.async_add_executor_job(
            _write_json_file, directory, filename, payload)
        filename = os.path.basename(path)
        return {"path": path, "filename": filename, "count": len(items),
                "boiler": sum(1 for i in items if i["kind"] == KIND_BOILER),
                "climate": sum(1 for i in items if i["kind"] == KIND_CLIMATE),
                "url": f"/local/{EXPORT_DIR}/{filename}" if to_www else None}

    # ------------------------------------------------------------------ импорт
    async def async_list_export_files(self) -> list[str]:
        return await self.hass.async_add_executor_job(
            _list_json_files, self._export_dir(False))

    async def async_read_export_file(self, filename: str) -> list[dict]:
        """Прочитать и проверить файл экспорта; вернуть очищенные профили (с полем kind).

        Файлы версии 1 (без типов) делятся на профили котла и климата.
        """
        if os.path.basename(filename) != filename or not _ISO_FILE_RE.match(filename):
            raise ServiceValidationError("Недопустимое имя файла (только имя в каталоге kotel_profiles)")
        path = os.path.join(self._export_dir(False), filename)
        try:
            raw = await self.hass.async_add_executor_job(_read_json_file, path)
        except FileNotFoundError as err:
            raise ServiceValidationError(f"Файл не найден: {filename}") from err
        except (ValueError, OSError) as err:      # JSONDecodeError — подкласс ValueError
            raise ServiceValidationError(f"Файл не удалось прочитать: {err}") from err

        if (not isinstance(raw, dict) or raw.get("format") != EXPORT_FORMAT
                or not isinstance(raw.get("profiles"), list)):
            raise ServiceValidationError("Это не файл профилей Kotel")
        version = raw.get("version")
        if not isinstance(version, int) or version < 1 or version > EXPORT_VERSION:
            raise ServiceValidationError("Неподдерживаемая версия файла профилей")
        if len(raw["profiles"]) > 200:
            raise ServiceValidationError("Слишком много профилей в файле")

        profiles, seen = [], set()
        for index, item in enumerate(raw["profiles"], start=1):
            try:
                parts = clean_profile(item)
            except ValueError as err:
                raise ServiceValidationError(f"Профиль №{index}: {err}") from err
            for profile in parts:
                key = (profile["kind"], profile["name"].casefold())
                if key in seen:
                    raise ServiceValidationError(
                        f"Профиль «{profile['name']}» ({KIND_LABELS[profile['kind']].lower()}) "
                        "повторяется в файле")
                seen.add(key)
                profiles.append(profile)
        return profiles

    async def async_import(self, filename: str, *, names: list[str] | None = None,
                           kind: str | None = None, keys: list[str] | None = None,
                           dev_id: str, on_conflict: str = "rename") -> dict:
        """Импортировать профили из файла в контроллер dev_id.

        Отбор: keys — "тип|имя"; либо names (имена) и/или kind (тип). По умолчанию все.
        on_conflict: overwrite — заменить, skip — пропустить, rename — добавить «(2)».
        Конфликты проверяются внутри своего типа профилей.
        """
        if on_conflict not in ("overwrite", "skip", "rename"):
            raise ServiceValidationError("on_conflict: overwrite, skip или rename")
        if kind is not None and kind not in ALL_KINDS:
            raise ServiceValidationError(f"Неизвестный тип профиля: {kind}")
        profiles = await self.async_read_export_file(filename)
        if keys is not None:
            wanted = set(keys)
            profiles = [p for p in profiles if f"{p['kind']}|{p['name']}" in wanted]
        else:
            if names is not None:
                profiles = [p for p in profiles if p["name"] in names]
            if kind is not None:
                profiles = [p for p in profiles if p["kind"] == kind]
        if not profiles:
            raise ServiceValidationError("Нет профилей для импорта")

        self._device(dev_id, create=True)
        result: dict[str, Any] = {"imported": [], "skipped": [], "renamed": []}
        now = dt_util.utcnow().isoformat(timespec="seconds")
        for profile in profiles:
            profile_kind = profile["kind"]
            bucket = self._kind(dev_id, profile_kind, create=True)
            name = profile["name"]
            existing = {n.casefold(): n for n in bucket["profiles"]}
            if name.casefold() in existing:
                if on_conflict == "skip":
                    result["skipped"].append({"kind": profile_kind, "name": name,
                                              "reason": "уже существует"})
                    continue
                if on_conflict == "overwrite":
                    name = existing[name.casefold()]
                else:
                    base, counter = profile["name"], 2
                    while True:
                        suffix = f" ({counter})"
                        candidate = base[: MAX_NAME_LENGTH - len(suffix)] + suffix
                        if candidate.casefold() not in existing:
                            break
                        counter += 1
                    result["renamed"].append(
                        {"kind": profile_kind, "from": profile["name"], "to": candidate})
                    name = candidate
            elif len(bucket["profiles"]) >= MAX_PROFILES_PER_DEVICE:
                result["skipped"].append({
                    "kind": profile_kind, "name": name,
                    "reason": f"предел {MAX_PROFILES_PER_DEVICE} профилей"})
                continue
            bucket["profiles"][name] = {
                "created": profile["created"] or now,
                "updated": now,
                "note": profile["note"],
                "firmware": profile["firmware"],
                "scopes": profile["scopes"],
                "values": profile["values"],
                "mode": profile["mode"],
                "all_schedules": profile["all_schedules"],
                "source": profile["source"],
                "imported_from": filename,
            }
            result["imported"].append({"kind": profile_kind, "name": name})
        if result["imported"]:
            await self._async_save()
        return result

    # ------------------------------------------------------------------ удаление пачкой
    async def async_delete_many(self, keys: list[str]) -> int:
        deleted: list[tuple[str, str, str]] = []
        for key in keys:
            dev_id, kind, name = _parse_key(key)
            device = self._data["devices"].get(dev_id)
            bucket = (device or {}).get(kind)
            if bucket and name in bucket["profiles"]:
                del bucket["profiles"][name]
                if bucket.get("selected") == name:
                    bucket["selected"] = None
                deleted.append((dev_id, kind, name))
        if deleted:
            await self._async_save()
            for dev_id, kind, name in deleted:
                self.hass.bus.async_fire(EVENT_PROFILE_DELETED, {
                    "device_id": dev_id, "kind": kind, "profile": name})
        return len(deleted)
