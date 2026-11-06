"""Tests for custom_components.remootio.number."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.remootio.const import DEFAULT_HOLD_MINUTES, MAX_HOLD_MINUTES
from custom_components.remootio.number import RemootioHoldDurationNumber
from tests.conftest import TEST_HOST


def _make_coordinator(minutes=DEFAULT_HOLD_MINUTES):
    coordinator = MagicMock()
    coordinator.host = TEST_HOST
    coordinator.device_info = {"identifiers": {("remootio", TEST_HOST)}}
    coordinator.hold_open_minutes = minutes
    return coordinator


class TestHoldDurationNumber:

    def test_value_reads_coordinator(self):
        number = RemootioHoldDurationNumber(_make_coordinator(20))
        assert number.native_value == 20

    def test_bounds_and_metadata(self):
        number = RemootioHoldDurationNumber(_make_coordinator())
        host_safe = TEST_HOST.replace(".", "_")
        assert number._attr_native_min_value == 1
        assert number._attr_native_max_value == MAX_HOLD_MINUTES
        assert number._attr_unique_id == f"remootio_{host_safe}_hold_open_duration_ch1"
        assert number._attr_translation_key == "hold_open_duration"

    @pytest.mark.asyncio
    async def test_set_value_updates_coordinator(self):
        coordinator = _make_coordinator()
        number = RemootioHoldDurationNumber(coordinator)
        with patch.object(number, "async_write_ha_state") as write:
            await number.async_set_native_value(45.0)
        assert coordinator.hold_open_minutes == 45
        write.assert_called_once()

    @pytest.mark.asyncio
    async def test_restores_last_value(self):
        coordinator = _make_coordinator()
        number = RemootioHoldDurationNumber(coordinator)
        number.async_get_last_number_data = AsyncMock(
            return_value=SimpleNamespace(native_value=60.0)
        )
        await number.async_added_to_hass()
        assert coordinator.hold_open_minutes == 60

    @pytest.mark.parametrize("restored", [None, 0.0, MAX_HOLD_MINUTES + 1])
    @pytest.mark.asyncio
    async def test_ignores_missing_or_out_of_range_restore(self, restored):
        coordinator = _make_coordinator()
        number = RemootioHoldDurationNumber(coordinator)
        number.async_get_last_number_data = AsyncMock(
            return_value=None if restored is None else SimpleNamespace(native_value=restored)
        )
        await number.async_added_to_hass()
        assert coordinator.hold_open_minutes == DEFAULT_HOLD_MINUTES

    def test_coordinator_default(self, mock_coordinator):
        assert mock_coordinator.hold_open_minutes == DEFAULT_HOLD_MINUTES
