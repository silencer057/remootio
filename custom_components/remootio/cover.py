"""Remootio Cover Platform."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_DURATION,
    ATTR_USE_TRIGGER,
    DOMAIN,
    MAX_HOLD_MINUTES,
    SERVICE_HOLD_OPEN,
)
from .coordinator import RemootioCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Remootio covers from a config entry."""
    coordinator: RemootioCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([RemootioCover(coordinator, relay_number=1)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_HOLD_OPEN,
        {
            vol.Required(ATTR_DURATION): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=MAX_HOLD_MINUTES)
            ),
            vol.Optional(ATTR_USE_TRIGGER, default=False): bool,
        },
        "async_hold_open",
    )


class RemootioCover(CoordinatorEntity[RemootioCoordinator], CoverEntity):
    """Representation of a Remootio cover."""

    _attr_has_entity_name = True
    _attr_device_class = CoverDeviceClass.GARAGE
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE

    def __init__(self, coordinator: RemootioCoordinator, relay_number: int) -> None:
        """Initialize the cover."""
        super().__init__(coordinator)
        self._relay_number = relay_number
        self._attr_unique_id = (
            f"remootio_{coordinator.host.replace('.', '_')}_ch{relay_number}"
        )
        self._attr_translation_key = f"channel_{relay_number}"
        if relay_number == 1:
            # The door/gate is the device's main feature, so per HA naming
            # guidelines it takes the device's own name ("Garage Door",
            # "Gate", ...) rather than "<device> Channel 1".
            self._attr_name = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for device registry."""
        return self.coordinator.device_info

    @property
    def is_closed(self) -> bool | None:
        """Return if the cover is closed."""
        state = self.coordinator.data.get(self._relay_number)
        if state is None:
            return None
        return state == "closed"

    @property
    def is_open(self) -> bool | None:
        """Return if the cover is open."""
        state = self.coordinator.data.get(self._relay_number)
        if state is None:
            return None
        return state == "open"

    async def async_open_cover(self, **kwargs) -> None:
        """Open the cover.

        Uses the directional OPEN command (not TRIGGER, which toggles) so
        this is a no-op when the door is already open.
        """
        await self.coordinator.async_open(self._relay_number)

    async def async_close_cover(self, **kwargs) -> None:
        """Close the cover.

        Uses the directional CLOSE command (not TRIGGER, which toggles) so
        this is a no-op when the door is already closed.
        """
        await self.coordinator.async_close(self._relay_number)

    async def async_hold_open(self, duration: int, use_trigger: bool = False) -> None:
        """Open the gate and hold the output active for *duration* minutes.

        Backs the ``remootio.hold_open`` entity service. See
        RemootioCoordinator.async_hold_open for the OPEN vs TRIGGER choice
        and the API's limits (no early release, firmware 2.24+).
        """
        await self.coordinator.async_hold_open(
            self._relay_number, duration, use_trigger=use_trigger
        )
