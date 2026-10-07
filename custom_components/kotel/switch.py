"""Переключатели для управления котлом (старт/стоп)."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .coordinator import KotelCoordinator
from .params_parser import get_params

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator: KotelCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = []

    for dev_id, dev_data in coordinator.data.items():
        if dev_data["type"] == "contr":
            entities.append(KotelStartStopSwitch(coordinator, dev_id, entry.entry_id))

    async_add_entities(entities)


class KotelStartStopSwitch(SwitchEntity):
    """Переключатель старт/стоп котла."""

    def __init__(self, coordinator, dev_id, entry_id):
        self._coordinator = coordinator
        self._dev_id = dev_id
        self._entry_id = entry_id
        self._attr_name = "Старт/Стоп"
        self._attr_unique_id = f"kotel_{dev_id}_start_stop"
        self._attr_icon = "mdi:power"
        self._attr_should_poll = False

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        data = self._coordinator.data.get(self._dev_id, {})
        params = data.get("params", {})
        return params.get("sost_rab", 0) != 0

    @property
    def device_info(self):
        data = self._coordinator.data.get(self._dev_id, {})
        return {
            "identifiers": {(DOMAIN, self._dev_id)},
            "name": data.get("name", self._dev_id),
            "manufacturer": "Kotel",
            "model": "Контроллер",
        }

    async def async_added_to_hass(self):
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )

    async def async_turn_on(self):
        await self._send_command("start__")

    async def async_turn_off(self):
        await self._send_command("stop___")

    async def _send_command(self, action):
        api = self._coordinator.hass.data[DOMAIN][self._entry_id]["api"]
        await api.send_command(action, self._dev_id)
        await self._coordinator.async_request_refresh()
