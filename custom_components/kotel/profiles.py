"""Профили настроек контроллера: сохранение, применение, откат, экспорт и импорт.

Профиль — именованный набор значений настроек одного контроллера. Хранится в
.storage/kotel.profiles (входит в резервные копии Home Assistant).

Области профиля (scope):
    boiler          настройки котла (11 параметров из number.py)  — по умолчанию
    climate         режим климат-контроля и целевая температура
    schedule        расписание климат-контроля (до 48 значений)
    climate_sensor  выбранный датчик климат-контроля

Старт/стоп и включение климат-контроля в профили не входят.
"""

from __future__ import annotations

import asyncio
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
    SCHEDULE_MODES,
    TSTAT_MODE_CONST,
)
from .coordinator import to_number
from .number import NUMBER_PARAMS

_LOGGER = logging.getLogger(__name__)

# --- хранилище и файлы -------------------------------------------------------
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.profiles"
EXPORT_DIR = "kotel_profiles"          # /config/kotel_profiles
EXPORT_FORMAT = "kotel-profiles"
EXPORT_VERSION = 1
MAX_IMPORT_BYTES = 1_000_000
MAX_PROFILES_PER_DEVICE = 20
MAX_NAME_LENGTH = 40

# --- события -----------------------------------------------------------------
EVENT_PROFILE_APPLIED = "kotel_profile_applied"
EVENT_PROFILE_SAVED = "kotel_profile_saved"

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
    for key, _name, min_val, max_val, step, _unit, _cat in NUMBER_PARAMS
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
# Очистка данных при импорте
# =============================================================================
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


def clean_profile(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("профиль должен быть объектом")
    values = clean_values(raw.get("values"))
    firmware = to_number(raw.get("firmware"))
    note = raw.get("note")
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    return {
        "name": clean_name(raw.get("name")),
        "note": note.strip()[:200] if isinstance(note, str) else "",
        "firmware": firmware,
        "scopes": [s for s in ALL_SCOPES if s in values],
        "values": values,
        "source": {
            "device_name": str(source.get("device_name", ""))[:60],
            "device_id": str(source.get("device_id", ""))[:32],
        },
        "created": raw.get("created") if isinstance(raw.get("created"), str) else None,
        "updated": raw.get("updated") if isinstance(raw.get("updated"), str) else None,
    }


def _write_json_file(directory: str, path: str, payload: dict) -> None:
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


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
    """Хранит профили всех контроллеров и применяет их через API."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, Any] = {"version": STORAGE_VERSION, "devices": {}}
        self._entries: dict[str, tuple[Any, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._drafts: dict[str, str] = {}
        self._listeners: list = []

    # ------------------------------------------------------------------ хранилище
    async def async_load(self) -> None:
        data = await self._store.async_load()
        if isinstance(data, dict) and isinstance(data.get("devices"), dict):
            self._data = data
            self._data.setdefault("version", STORAGE_VERSION)

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
        return self._locks.setdefault(dev_id, asyncio.Lock())

    # ------------------------------------------------------------------ чтение
    def _device(self, dev_id: str, create: bool = False) -> dict:
        devices = self._data["devices"]
        if dev_id not in devices and create:
            devices[dev_id] = {"profiles": {}, "selected": None,
                               "last_applied": None, "undo": None}
        return devices.get(dev_id, {"profiles": {}, "selected": None,
                                    "last_applied": None, "undo": None})

    def profile_names(self, dev_id: str) -> list[str]:
        return sorted(self._device(dev_id)["profiles"], key=str.casefold)

    def get_profile(self, dev_id: str, name: str) -> dict | None:
        return self._device(dev_id)["profiles"].get(name)

    def selected(self, dev_id: str) -> str | None:
        name = self._device(dev_id).get("selected")
        return name if name in self._device(dev_id)["profiles"] else None

    def last_applied(self, dev_id: str) -> dict | None:
        return self._device(dev_id).get("last_applied")

    def has_undo(self, dev_id: str) -> bool:
        return bool(self._device(dev_id).get("undo"))

    def draft_name(self, dev_id: str) -> str:
        return self._drafts.get(dev_id, "")

    @callback
    def set_draft_name(self, dev_id: str, value: str) -> None:
        self._drafts[dev_id] = value
        self._notify()

    def matching_profile(self, dev_id: str) -> str | None:
        """Имя профиля, чьи настройки котла совпадают с текущими, иначе None."""
        info = self.controllers().get(dev_id)
        if not info:
            return None
        data = info["coordinator"].data.get(dev_id, {})
        current = capture_values(data, (SCOPE_BOILER,)).get(SCOPE_BOILER, {})
        for name in self.profile_names(dev_id):
            target = self._device(dev_id)["profiles"][name]["values"].get(SCOPE_BOILER)
            if not target:
                continue
            if all(key in current and values_equal(current[key], value)
                   for key, value in target.items()):
                return name
        return None

    def profile_choices(self) -> dict[str, str]:
        """{"id устройства|имя": подпись} — для окон выбора."""
        many = len(self._data["devices"]) > 1
        choices = {}
        for dev_id, device in self._data["devices"].items():
            for name in sorted(device["profiles"], key=str.casefold):
                label = f"{self.device_name(dev_id)}: {name}" if many else name
                choices[f"{dev_id}|{name}"] = label
        return choices

    # ------------------------------------------------------------------ сохранение
    def _snapshot(self, dev_id: str, scopes) -> dict[str, dict]:
        coordinator, _ = self._runtime(dev_id)
        data = coordinator.data.get(dev_id)
        if not data:
            raise HomeAssistantError("Нет данных контроллера")
        return capture_values(data, scopes)

    async def async_save_profile(self, dev_id: str, name: str, scopes=None,
                                 note: str = "", overwrite: bool = False) -> dict:
        try:
            name = clean_name(name)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        scopes = [s for s in ALL_SCOPES if s in (scopes or DEFAULT_SCOPES)]
        device = self._device(dev_id, create=True)
        exists = name in device["profiles"]
        if exists and not overwrite:
            raise ServiceValidationError(
                f"Профиль «{name}» уже существует (используйте «Обновить профиль» или overwrite)")
        if not exists and len(device["profiles"]) >= MAX_PROFILES_PER_DEVICE:
            raise ServiceValidationError(
                f"Достигнут предел: {MAX_PROFILES_PER_DEVICE} профилей на контроллер")

        values = self._snapshot(dev_id, scopes)
        empty = [SCOPE_LABELS[s] for s in scopes if not values.get(s)]
        if empty:
            raise ServiceValidationError(
                "Нет данных для области: " + ", ".join(empty)
                + ". Контроллер не отдаёт эти параметры.")

        coordinator, _ = self._runtime(dev_id)
        params = coordinator.data[dev_id].get("params", {})
        now = dt_util.utcnow().isoformat(timespec="seconds")
        old = device["profiles"].get(name, {})
        device["profiles"][name] = {
            "created": old.get("created", now),
            "updated": now,
            "note": note.strip()[:200] if note else old.get("note", ""),
            "firmware": to_number(params.get("vers")),
            "scopes": scopes,
            "values": values,
            "source": {"device_id": dev_id, "device_name": self.device_name(dev_id)},
        }
        device["selected"] = name
        await self._async_save()
        self.hass.bus.async_fire(EVENT_PROFILE_SAVED, {
            "device_id": dev_id, "profile": name, "scopes": scopes, "overwritten": exists})
        return {"profile": name, "scopes": scopes,
                "values": sum(len(v) for v in values.values()), "overwritten": exists}

    async def async_update_profile(self, dev_id: str, name: str) -> dict:
        profile = self.get_profile(dev_id, name)
        if profile is None:
            raise ServiceValidationError(f"Профиль «{name}» не найден")
        return await self.async_save_profile(
            dev_id, name, scopes=profile["scopes"], overwrite=True)

    async def async_delete_profile(self, dev_id: str, name: str) -> None:
        device = self._device(dev_id)
        if name not in device["profiles"]:
            raise ServiceValidationError(f"Профиль «{name}» не найден")
        del device["profiles"][name]
        if device.get("selected") == name:
            device["selected"] = None
        await self._async_save()

    async def async_rename_profile(self, dev_id: str, name: str, new_name: str) -> None:
        device = self._device(dev_id)
        if name not in device["profiles"]:
            raise ServiceValidationError(f"Профиль «{name}» не найден")
        try:
            new_name = clean_name(new_name)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        if new_name != name and new_name.casefold() in (
                n.casefold() for n in device["profiles"]):
            raise ServiceValidationError(f"Профиль «{new_name}» уже существует")
        device["profiles"][new_name] = device["profiles"].pop(name)
        if device.get("selected") == name:
            device["selected"] = new_name
        await self._async_save()

    async def async_set_selected(self, dev_id: str, name: str | None) -> None:
        device = self._device(dev_id, create=True)
        if name is not None and name not in device["profiles"]:
            raise ServiceValidationError(f"Профиль «{name}» не найден")
        device["selected"] = name
        await self._async_save()

    # ------------------------------------------------------------------ применение
    async def async_apply(self, dev_id: str, name: str, scopes=None, *,
                          dry_run: bool = False, force: bool = False,
                          verify: bool = True, backup: bool = True) -> dict:
        profile = self.get_profile(dev_id, name)
        if profile is None:
            raise ServiceValidationError(f"Профиль «{name}» не найден")
        use_scopes = [s for s in APPLY_ORDER
                      if s in (scopes or profile["scopes"]) and s in profile["values"]]
        if not use_scopes:
            raise ServiceValidationError("В профиле нет выбранных областей")
        report = await self._apply_values(
            dev_id, profile["values"], use_scopes, label=name,
            dry_run=dry_run, force=force, verify=verify, backup=backup)
        if not dry_run:
            device = self._device(dev_id, create=True)
            device["last_applied"] = {
                "name": name, "at": dt_util.utcnow().isoformat(timespec="seconds"),
                "ok": report["ok"]}
            await self._async_save()
            self.hass.bus.async_fire(EVENT_PROFILE_APPLIED, {
                "device_id": dev_id, "profile": name, "ok": report["ok"],
                "applied": report["applied"], "failed": len(report["failed"]),
                "unverified": len(report["unverified"])})
        return report

    async def async_undo(self, dev_id: str, *, force: bool = False,
                         verify: bool = True) -> dict:
        undo = self._device(dev_id).get("undo")
        if not undo:
            raise ServiceValidationError("Нет сохранённого состояния для отката")
        report = await self._apply_values(
            dev_id, undo["values"], [s for s in APPLY_ORDER if s in undo["values"]],
            label=f"откат «{undo.get('profile', '')}»",
            dry_run=False, force=force, verify=verify, backup=False)
        if report["ok"]:
            self._device(dev_id)["undo"] = None
        await self._async_save()
        self.hass.bus.async_fire(EVENT_PROFILE_APPLIED, {
            "device_id": dev_id, "profile": None, "undo": True, "ok": report["ok"],
            "applied": report["applied"], "failed": len(report["failed"]),
            "unverified": len(report["unverified"])})
        return report

    async def _apply_values(self, dev_id: str, target: dict, scopes: list[str], *,
                            label: str, dry_run: bool, force: bool, verify: bool,
                            backup: bool) -> dict:
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
                self._device(dev_id, create=True)["undo"] = {
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
        """Записать выбранные профили в JSON-файл. keys — "id|имя" (None = все)."""
        items = []
        for dev_id, device in self._data["devices"].items():
            for name in sorted(device["profiles"], key=str.casefold):
                if keys is not None and f"{dev_id}|{name}" not in keys:
                    continue
                profile = device["profiles"][name]
                items.append({
                    "name": name,
                    "note": profile.get("note", ""),
                    "firmware": profile.get("firmware"),
                    "scopes": profile["scopes"],
                    "values": profile["values"],
                    "created": profile.get("created"),
                    "updated": profile.get("updated"),
                    "source": {"device_id": dev_id, "device_name": self.device_name(dev_id)},
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
        path = os.path.join(directory, filename)
        await self.hass.async_add_executor_job(_write_json_file, directory, path, payload)
        return {"path": path, "filename": filename, "count": len(items),
                "url": f"/local/{EXPORT_DIR}/{filename}" if to_www else None}

    # ------------------------------------------------------------------ импорт
    async def async_list_export_files(self) -> list[str]:
        return await self.hass.async_add_executor_job(
            _list_json_files, self._export_dir(False))

    async def async_read_export_file(self, filename: str) -> list[dict]:
        """Прочитать и проверить файл экспорта; вернуть очищенные профили."""
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
        if not isinstance(version, int) or version > EXPORT_VERSION:
            raise ServiceValidationError("Неподдерживаемая версия файла профилей")
        if len(raw["profiles"]) > 200:
            raise ServiceValidationError("Слишком много профилей в файле")

        profiles, seen = [], set()
        for index, item in enumerate(raw["profiles"], start=1):
            try:
                profile = clean_profile(item)
            except ValueError as err:
                raise ServiceValidationError(f"Профиль №{index}: {err}") from err
            if profile["name"].casefold() in seen:
                raise ServiceValidationError(f"Профиль «{profile['name']}» повторяется в файле")
            seen.add(profile["name"].casefold())
            profiles.append(profile)
        return profiles

    async def async_import(self, filename: str, *, names: list[str] | None = None,
                           dev_id: str, on_conflict: str = "rename") -> dict:
        """Импортировать профили из файла в контроллер dev_id.

        on_conflict: overwrite — заменить, skip — пропустить, rename — добавить «(2)».
        """
        if on_conflict not in ("overwrite", "skip", "rename"):
            raise ServiceValidationError("on_conflict: overwrite, skip или rename")
        profiles = await self.async_read_export_file(filename)
        if names is not None:
            profiles = [p for p in profiles if p["name"] in names]
        if not profiles:
            raise ServiceValidationError("Нет профилей для импорта")

        device = self._device(dev_id, create=True)
        result = {"imported": [], "skipped": [], "renamed": {}}
        now = dt_util.utcnow().isoformat(timespec="seconds")
        for profile in profiles:
            name = profile["name"]
            existing = {n.casefold(): n for n in device["profiles"]}
            if name.casefold() in existing:
                if on_conflict == "skip":
                    result["skipped"].append({"name": name, "reason": "уже существует"})
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
                    result["renamed"][profile["name"]] = candidate
                    name = candidate
            elif len(device["profiles"]) >= MAX_PROFILES_PER_DEVICE:
                result["skipped"].append({
                    "name": name, "reason": f"предел {MAX_PROFILES_PER_DEVICE} профилей"})
                continue
            device["profiles"][name] = {
                "created": profile["created"] or now,
                "updated": now,
                "note": profile["note"],
                "firmware": profile["firmware"],
                "scopes": profile["scopes"],
                "values": profile["values"],
                "source": profile["source"],
                "imported_from": filename,
            }
            result["imported"].append(name)
        if result["imported"]:
            await self._async_save()
        return result

    # ------------------------------------------------------------------ удаление пачкой
    async def async_delete_many(self, keys: list[str]) -> int:
        count = 0
        for key in keys:
            dev_id, _, name = key.partition("|")
            device = self._data["devices"].get(dev_id)
            if device and name in device["profiles"]:
                del device["profiles"][name]
                if device.get("selected") == name:
                    device["selected"] = None
                count += 1
        if count:
            await self._async_save()
        return count
