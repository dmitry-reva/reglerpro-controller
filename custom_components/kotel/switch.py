"""Переключатели для управления котлом (старт/стоп)."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import HomeAssistantError

from .api import KotelApiError
from .const import DOMAIN
from .coordinator import KotelCoordinator
from .entity import KotelEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator: KotelCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        KotelStartStopSwitch(coordinator, dev_id, entry.entry_id)
        for dev_id, dev_data in coordinator.data.items()
        if dev_data["type"] == "contr"
    )


class KotelStartStopSwitch(KotelEntity, SwitchEntity):
    """Переключатель старт/стоп котла."""

    _attr_icon = "mdi:power"

    def __init__(self, coordinator, dev_id, entry_id):
        super().__init__(
            coordinator, dev_id,
            unique_suffix="start_stop",
            translation_key="start_stop",
        )
        self._entry_id = entry_id

    @property
    def is_on(self) -> bool:
        params = self.coordinator.data.get(self._dev_id, {}).get("params") or {}
        return params.get("sost_rab", 0) != 0

    async def async_turn_on(self):
        await self._send_command("start__")

    async def async_turn_off(self):
        await self._send_command("stop___")

    async def _send_command(self, action):
        api = self.coordinator.hass.data[DOMAIN][self._entry_id]["api"]
        try:
            await api.send_command(action, self._dev_id)
        except KotelApiError as err:
            raise HomeAssistantError(
                f"Не удалось переключить котёл ({action}): {err}") from err
        await self.coordinator.async_request_refresh()
