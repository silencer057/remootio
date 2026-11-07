"""Remootio Binary Sensor Platform: whether a timed hold is active."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RemootioCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Remootio hold-active binary sensor."""
    coordinator: RemootioCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RemootioHoldActiveBinarySensor(coordinator)])


class RemootioHoldActiveBinarySensor(
    CoordinatorEntity[RemootioCoordinator], BinarySensorEntity
):
    """On while the device is holding its main output active.

    Driven by the device's Output1Activated / Output1Deactivated (and
    OutputHeldActive) events, so it reflects what the device is actually
    doing — whether the hold came from Home Assistant, the Remootio app, or
    a key. Needs the Remootio API enabled with logging.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "hold_active"

    def __init__(self, coordinator: RemootioCoordinator) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"remootio_{coordinator.host.replace('.', '_')}_hold_active_ch1"
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for device registry."""
        return self.coordinator.device_info

    @property
    def is_on(self) -> bool:
        """Return True while a hold is active."""
        return self.coordinator.hold_active

    @property
    def icon(self) -> str:
        """Return an icon that reflects the hold state."""
        return "mdi:timer-lock-open" if self.coordinator.hold_active else "mdi:timer-lock-outline"
