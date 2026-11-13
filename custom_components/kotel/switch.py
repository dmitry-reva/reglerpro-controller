"""Переключатели для управления котлом (старт/стоп)."""

from __future__ import annotations

import logging
import time

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later

from .api import KotelApiError
from .const import DOMAIN
from .coordinator import KotelCoordinator
from .entity import KotelEntity

_LOGGER = logging.getLogger(__name__)

# Сколько секунд после команды показывать ожидаемое состояние, пока облако
# ещё может отдавать старое. По истечении времени показывается реальное.
PENDING_SECONDS = 15


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
        self._pending: bool | None = None      # ожидаемое состояние после команды
        self._pending_until = 0.0
        self._cancel_timer = None

    def _actual(self) -> bool | None:
        """Состояние по данным контроллера; None — параметра sost_rab нет."""
        params = self.coordinator.data.get(self._dev_id, {}).get("params") or {}
        if params.get("sost_rab") is None:
            return None
        return params["sost_rab"] != 0

    @property
    def is_on(self) -> bool | None:
        actual = self._actual()
        if self._pending is not None:
            if actual == self._pending or time.monotonic() >= self._pending_until:
                self._pending = None           # облако догнало команду или время вышло
            else:
                return self._pending           # облако ещё отдаёт старое значение
        return actual

    async def async_turn_on(self):
        await self._send_command("start__", True)

    async def async_turn_off(self):
        await self._send_command("stop___", False)

    async def _send_command(self, action, target_state: bool):
        api = self.coordinator.hass.data[DOMAIN][self._entry_id]["api"]
        try:
            await api.send_command(action, self._dev_id)
        except KotelApiError as err:
            raise HomeAssistantError(
                f"Не удалось переключить котёл ({action}): {err}") from err
        self._set_pending(target_state)
        await self.coordinator.async_request_refresh()

    def _set_pending(self, target_state: bool) -> None:
        """Показать ожидаемое состояние, чтобы переключатель не «отскакивал»."""
        self._pending = target_state
        self._pending_until = time.monotonic() + PENDING_SECONDS
        self._clear_timer()
        self._cancel_timer = async_call_later(
            self.hass, PENDING_SECONDS + 1, self._pending_expired)
        self.async_write_ha_state()

    @callback
    def _pending_expired(self, _now) -> None:
        self._cancel_timer = None
        self._pending = None
        self.async_write_ha_state()

    def _clear_timer(self) -> None:
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None

    async def async_will_remove_from_hass(self) -> None:
        self._clear_timer()
        await super().async_will_remove_from_hass()
