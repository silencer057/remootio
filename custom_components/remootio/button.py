"""Remootio Button Platform for garage door control."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RemootioCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Remootio buttons from a config entry."""
    coordinator: RemootioCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            RemootioButton(coordinator, relay_number=1),
            RemootioButton(coordinator, relay_number=2),
            RemootioHoldOpenButton(coordinator),
        ]
    )


class RemootioButton(CoordinatorEntity[RemootioCoordinator], ButtonEntity):
    """Button to trigger the Remootio garage door."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:garage"

    def __init__(self, coordinator: RemootioCoordinator, relay_number: int) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._relay_number = relay_number
        self._attr_unique_id = (
            f"remootio_{coordinator.host.replace('.', '_')}_toggle_ch{relay_number}"
        )
        self._attr_translation_key = f"toggle_channel_{relay_number}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for device registry."""
        return self.coordinator.device_info

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.debug(
            "Toggle button pressed for channel %d",
            self._relay_number,
        )
        await self.coordinator.async_trigger(self._relay_number)


class RemootioHoldOpenButton(CoordinatorEntity[RemootioCoordinator], ButtonEntity):
    """Open the gate and hold it for the "Hold open duration" setting.

    UI counterpart of the ``remootio.hold_open`` action: one tap from a
    dashboard, the device page, CarPlay or Android Auto, using the duration
    held by the number entity. Always uses directional OPEN, so it can't
    close an already-open gate.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:gate-open"
    _attr_translation_key = "hold_open"

    def __init__(self, coordinator: RemootioCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"remootio_{coordinator.host.replace('.', '_')}_hold_open_ch1"
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for device registry."""
        return self.coordinator.device_info

    async def async_press(self) -> None:
        """Open and hold for the configured number of minutes."""
        _LOGGER.debug(
            "Hold open pressed (%d min)", self.coordinator.hold_open_minutes
        )
        await self.coordinator.async_hold_open(
            1, self.coordinator.hold_open_minutes
        )
