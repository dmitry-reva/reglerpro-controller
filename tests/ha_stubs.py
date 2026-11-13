"""Минимальные заглушки Home Assistant, aiohttp и voluptuous для юнит-тестов.

Нужны, чтобы проверять логику интеграции без установленного Home Assistant.
Если настоящий Home Assistant установлен, заглушки не включаются.
Реальный __init__.py интеграции не выполняется: модули загружаются в
синтетический пакет (см. load_integration).
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
import types
from pathlib import Path


class _Any:
    """Универсальное значение: допускает вызов, атрибуты и операцию «|»."""

    def __init__(self, name: str = "any") -> None:
        self._name = name

    def __getattr__(self, item):
        if item.startswith("__"):
            raise AttributeError(item)
        return _Any(f"{self._name}.{item}")

    def __call__(self, *args, **kwargs):
        return _Any(f"{self._name}()")

    def __or__(self, other):
        return _Any(f"{self._name}|{getattr(other, '_name', other)}")

    def __eq__(self, other):
        return isinstance(other, _Any) and other._name == self._name

    def __hash__(self):
        return hash(self._name)

    def __repr__(self):
        return f"<{self._name}>"


class _Meta(type):
    def __getattr__(cls, item):
        if item.startswith("__"):
            raise AttributeError(item)
        return _Any(f"{cls.__name__}.{item}")


def _dummy_class(name: str):
    return _Meta(name, (), {
        "__init__": lambda self, *a, **k: None,
        "__init_subclass__": classmethod(lambda cls, **kw: None),
    })


class HomeAssistantError(Exception):
    pass


class ServiceValidationError(HomeAssistantError):
    pass


class UpdateFailed(Exception):
    pass


class Entity:
    """Заглушка Entity: запоминает записи состояния и отписки."""

    hass = None

    def __init__(self, *args, **kwargs) -> None:
        self.writes = 0
        self._on_remove: list = []

    def async_write_ha_state(self) -> None:
        self.writes += 1

    def async_on_remove(self, func) -> None:
        self._on_remove.append(func)

    async def async_added_to_hass(self) -> None:
        pass

    async def async_will_remove_from_hass(self) -> None:
        pass


class DataUpdateCoordinator:
    """Заглушка координатора: результат обновления задаётся тестом."""

    def __init__(self, hass, logger, name=None, update_interval=None, **kwargs) -> None:
        self.hass = hass
        self.update_interval = update_interval
        self.data = {}
        self.last_update_success = True
        self.refreshes = 0

    async def async_refresh(self) -> None:
        self.refreshes += 1

    async def async_request_refresh(self) -> None:
        self.refreshes += 1

    def async_add_listener(self, update_callback):
        return lambda: None


class _Module(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        if name.startswith("async_"):                    # функция-заглушка
            value = lambda *a, **k: _Any(name)           # noqa: E731
        elif name[0].islower():                          # подмодуль (cv, config_entries ...)
            value = importlib.import_module(f"{self.__name__}.{name}")
        else:                                            # класс или константа
            value = _dummy_class(name)
        setattr(self, name, value)
        return value


_SPECIAL = {
    "homeassistant.exceptions": {
        "HomeAssistantError": HomeAssistantError,
        "ServiceValidationError": ServiceValidationError,
    },
    "homeassistant.core": {"callback": lambda func: func},
    "homeassistant.helpers.entity": {"Entity": Entity},
    "homeassistant.helpers.device_registry": {"DeviceInfo": dict},
    "homeassistant.helpers.update_coordinator": {
        "DataUpdateCoordinator": DataUpdateCoordinator,
        "UpdateFailed": UpdateFailed,
    },
    "homeassistant.helpers.event": {"async_call_later": lambda hass, delay, action: (lambda: None)},
}
_ROOTS = ("homeassistant", "voluptuous", "aiohttp")


class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path, target=None):
        if fullname.split(".")[0] in _ROOTS:
            return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        module = _Module(spec.name)
        module.__path__ = []
        for key, value in _SPECIAL.get(spec.name, {}).items():
            setattr(module, key, value)
        return module

    def exec_module(self, module):
        pass


def install() -> bool:
    """Включить заглушки, если настоящего Home Assistant нет. True — включены."""
    try:
        import homeassistant  # noqa: F401
        return False
    except ModuleNotFoundError:
        pass
    sys.meta_path.insert(0, _Finder())
    return True


def load_integration(component_dir: Path, package: str = "kotel_under_test"):
    """Загрузить модули интеграции в синтетический пакет без запуска __init__.py."""
    install()
    package_module = types.ModuleType(package)
    package_module.__path__ = [str(component_dir)]
    sys.modules[package] = package_module
    return package_module
