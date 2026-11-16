"""Данные работающей записи Kotel (entry.runtime_data)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - только для типов
    from homeassistant.config_entries import ConfigEntry

    from .api import KotelApiClient
    from .coordinator import KotelCoordinator


@dataclass
class KotelRuntimeData:
    """Клиент API и координатор одной записи: то, что раньше лежало в hass.data."""

    api: KotelApiClient
    coordinator: KotelCoordinator


if TYPE_CHECKING:  # pragma: no cover
    KotelConfigEntry = ConfigEntry[KotelRuntimeData]
