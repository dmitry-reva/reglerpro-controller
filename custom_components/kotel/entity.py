"""Общая база сущностей интеграции Kotel.

Все платформы наследуют :class:`KotelEntity`. Это даёт единые:

* устройство (``device_info``) и модель;
* доступность (``available``);
* подписку на обновления координатора;
* ``unique_id`` (совместимый с прежними версиями);
* перевод названия через ``translation_key``.

``has_entity_name = True`` — стандарт Home Assistant для интеграций-хабов:
пользователь видит «Устройство · Сущность», а ``unique_id`` и существующие
``entity_id`` не меняются.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN

if TYPE_CHECKING:  # pragma: no cover - только для типов
    from .coordinator import KotelCoordinator


class KotelEntity(Entity):
    """Базовая сущность контроллера или датчика ReglerPro."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: "KotelCoordinator",
        dev_id: str,
        *,
        unique_suffix: str,
        translation_key: str,
    ) -> None:
        """Создать сущность.

        ``unique_suffix`` сохраняет прежний формат unique_id:
        ``kotel_<id устройства>_<суффикс>``.
        """
        super().__init__()
        self.coordinator = coordinator
        self._dev_id = dev_id
        self._attr_unique_id = f"kotel_{dev_id}_{unique_suffix}"
        self._attr_translation_key = translation_key

    @property
    def available(self) -> bool:
        """Сущность доступна, пока координатор успешно обновляется."""
        return self.coordinator.last_update_success

    @property
    def device_info(self) -> DeviceInfo:
        """Единое описание устройства для всех платформ."""
        data = self.coordinator.data.get(self._dev_id, {})
        is_controller = data.get("type") == "contr"
        info = DeviceInfo(
            identifiers={(DOMAIN, self._dev_id)},
            name=data.get("name", self._dev_id),
            manufacturer="ReglerPro",
            model="ReglerPro Controller" if is_controller else "ReglerPro Sensor",
            configuration_url="https://app.reglerpro.ru",
        )
        if is_controller:
            version = (data.get("params") or {}).get("vers")
            if version is not None:
                info["sw_version"] = str(version)
        return info

    async def async_added_to_hass(self) -> None:
        """Подписаться на обновления данных координатора."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )
