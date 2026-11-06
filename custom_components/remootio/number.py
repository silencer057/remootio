"""Remootio Number Platform: the duration used by the Hold open button."""
from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MAX_HOLD_MINUTES
from .coordinator import RemootioCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Remootio hold-open duration number."""
    coordinator: RemootioCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RemootioHoldDurationNumber(coordinator)])


class RemootioHoldDurationNumber(CoordinatorEntity[RemootioCoordinator], RestoreNumber):
    """How long the Hold open button holds the gate, in minutes.

    A local setting (nothing is sent to the device when it changes); the
    value lives on the coordinator so the button can read it, and is
    restored across Home Assistant restarts.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "hold_open_duration"
    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_max_value = MAX_HOLD_MINUTES
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator: RemootioCoordinator) -> None:
        """Initialize the number."""
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"remootio_{coordinator.host.replace('.', '_')}_hold_open_duration_ch1"
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for device registry."""
        return self.coordinator.device_info

    @property
    def native_value(self) -> float:
        """Return the current hold duration in minutes."""
        return self.coordinator.hold_open_minutes

    async def async_added_to_hass(self) -> None:
        """Restore the last duration the user set."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            value = int(last.native_value)
            if 1 <= value <= MAX_HOLD_MINUTES:
                self.coordinator.hold_open_minutes = value

    async def async_set_native_value(self, value: float) -> None:
        """Set a new hold duration."""
        self.coordinator.hold_open_minutes = int(value)
        self.async_write_ha_state()
