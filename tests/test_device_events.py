"""Tests for device events beyond StateChange: who operated the door, hold
active, key management, and SERVER_HELLO device info / firmware check."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.remootio.api import RemootioAPI, RemootioEventListener, encrypt_frame
from custom_components.remootio.binary_sensor import RemootioHoldActiveBinarySensor
from custom_components.remootio.const import (
    EVENT_KEY_MANAGEMENT,
    EVENT_RELAY_TRIGGER,
    format_operator,
)
from custom_components.remootio.sensor import RemootioLastOperatedSensor
from tests.conftest import TEST_AUTH_KEY, TEST_HOST, TEST_SECRET_KEY
from tests.test_api import TestRemootioEventListener as _L


def _event_frame(event: dict) -> str:
    enc = encrypt_frame({"event": event}, TEST_SECRET_KEY, TEST_AUTH_KEY)
    return json.dumps({
        "type": "ENCRYPTED",
        "data": {"iv": enc["iv"], "payload": enc["payload"]},
        "mac": enc["mac"],
    })


def _relay_trigger(cnt, key_type="guest key", key_nr=5, via="internet", type_="RelayTrigger"):
    return {
        "cnt": cnt, "type": type_, "state": "closed", "t100ms": 1000,
        "data": {"keyType": key_type, "keyNr": key_nr, "via": via},
    }


# ── format_operator ────────────────────────────────────────────────────────

class TestFormatOperator:

    @pytest.mark.parametrize("args,expected", [
        (("guest key", 5, "internet"), "Guest key 5 via internet"),
        (("master key", 0, "bluetooth"), "Master key via Bluetooth"),
        (("api key", 0, "wifi"), "API key via Wi-Fi"),
        (("unique key", 3, "none"), "Unique key 3"),
        (("automation", None, "autoopen"), "Automation via auto-open"),
        (("future type", 1, "unknown"), "Future type"),
        ((None, None, None), "Unknown"),
    ])
    def test_formats(self, args, expected):
        assert format_operator(*args) == expected


# ── listener routing ───────────────────────────────────────────────────────

class TestListenerRouting:

    def _listener(self, on_event=None, on_hello=None):
        api = RemootioAPI(TEST_HOST, TEST_SECRET_KEY, TEST_AUTH_KEY)
        on_state = AsyncMock()
        return RemootioEventListener(api, on_state, on_event=on_event, on_hello=on_hello), on_state

    async def _run(self, listener, frames, recv=None):
        helper = _L()
        ws_mock = helper._make_ws_mock(frames)
        if recv is not None:
            ws_mock.recv = recv
        with patch(
            "custom_components.remootio.api.websockets.connect",
            return_value=helper._make_connect_ctx(ws_mock),
        ):
            await listener._connect_and_listen()
        return ws_mock

    @pytest.mark.asyncio
    async def test_first_connection_flags_events_replayed(self):
        on_event = AsyncMock()
        listener, _ = self._listener(on_event=on_event)
        await self._run(listener, [_event_frame(_relay_trigger(7))])
        on_event.assert_awaited_once()
        event, replayed = on_event.await_args.args
        assert event["type"] == "RelayTrigger"
        assert replayed is True
        assert listener._last_event_cnt == 7

    @pytest.mark.asyncio
    async def test_live_events_not_flagged_once_counter_known(self):
        on_event = AsyncMock()
        listener, _ = self._listener(on_event=on_event)
        listener._last_event_cnt = 6
        await self._run(listener, [_event_frame(_relay_trigger(7))])
        assert on_event.await_args.args[1] is False

    @pytest.mark.asyncio
    async def test_dedups_events_by_counter(self):
        on_event = AsyncMock()
        listener, _ = self._listener(on_event=on_event)
        listener._last_event_cnt = 10
        await self._run(listener, [
            _event_frame(_relay_trigger(9)),
            _event_frame(_relay_trigger(11)),
        ])
        assert on_event.await_count == 1
        assert on_event.await_args.args[0]["cnt"] == 11

    @pytest.mark.asyncio
    async def test_state_change_still_goes_to_state_callback(self):
        on_event = AsyncMock()
        listener, on_state = self._listener(on_event=on_event)
        frame = _event_frame({"cnt": 1, "type": "StateChange", "state": "open", "t100ms": 1})
        await self._run(listener, [frame])
        on_state.assert_awaited_once_with("open")
        on_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handler_error_does_not_kill_listener(self):
        on_event = AsyncMock(side_effect=[RuntimeError("boom"), None])
        listener, _ = self._listener(on_event=on_event)
        listener._last_event_cnt = 0
        await self._run(listener, [
            _event_frame(_relay_trigger(1)),
            _event_frame(_relay_trigger(2)),
        ])
        assert on_event.await_count == 2

    @pytest.mark.asyncio
    async def test_hello_sent_once_and_passed_to_callback(self):
        on_hello = AsyncMock()
        listener, _ = self._listener(on_hello=on_hello)
        helper = _L()
        server_hello = json.dumps({
            "type": "SERVER_HELLO", "apiVersion": 3,
            "serialNumber": "abc123", "remootioVersion": "remootio-2",
        })
        recv = AsyncMock(side_effect=[server_hello, helper._make_auth_response()])
        ws_mock = await self._run(listener, [], recv=recv)

        assert json.loads(ws_mock.send.await_args_list[0].args[0]) == {"type": "HELLO"}
        on_hello.assert_awaited_once()
        assert on_hello.await_args.args[0]["apiVersion"] == 3

        # second connection: no HELLO
        recv2 = AsyncMock(return_value=helper._make_auth_response())
        ws_mock2 = await self._run(listener, [], recv=recv2)
        sent = [c.args[0] for c in ws_mock2.send.await_args_list]
        assert json.dumps({"type": "HELLO"}) not in sent
        assert on_hello.await_count == 1


# ── coordinator handling ───────────────────────────────────────────────────

class TestCoordinatorDeviceEvents:

    @pytest.mark.asyncio
    async def test_relay_trigger_sets_last_operated_and_fires_event(self, mock_coordinator):
        hass = mock_coordinator.hass
        await mock_coordinator._handle_device_event(_relay_trigger(1), replayed=False)

        info = mock_coordinator.last_operated
        assert (info["key_type"], info["key_nr"], info["via"]) == ("guest key", 5, "internet")
        hass.bus.async_fire.assert_called_once()
        name, data = hass.bus.async_fire.call_args.args
        assert name == EVENT_RELAY_TRIGGER
        assert data["output"] == 1 and data["key_type"] == "guest key"
        assert data["host"] == TEST_HOST

    @pytest.mark.asyncio
    async def test_secondary_trigger_fires_but_keeps_last_operated(self, mock_coordinator):
        await mock_coordinator._handle_device_event(
            _relay_trigger(1, type_="SecondaryRelayTrigger"), replayed=False
        )
        assert mock_coordinator.last_operated is None
        assert mock_coordinator.hass.bus.async_fire.call_args.args[1]["output"] == 2

    @pytest.mark.asyncio
    async def test_replayed_one_shot_events_are_not_fired(self, mock_coordinator):
        await mock_coordinator._handle_device_event(_relay_trigger(1), replayed=True)
        mock_coordinator.hass.bus.async_fire.assert_not_called()
        assert mock_coordinator.last_operated is None

    @pytest.mark.parametrize("replayed", [True, False])
    @pytest.mark.asyncio
    async def test_hold_active_follows_output_events(self, mock_coordinator, replayed):
        with patch.object(mock_coordinator, "async_update_listeners") as upd:
            await mock_coordinator._handle_device_event(
                {"cnt": 1, "type": "Output1Activated"}, replayed=replayed
            )
            assert mock_coordinator.hold_active is True
            await mock_coordinator._handle_device_event(
                {"cnt": 2, "type": "Output1Deactivated"}, replayed=replayed
            )
            assert mock_coordinator.hold_active is False
        assert upd.call_count == 2

    @pytest.mark.asyncio
    async def test_key_management_fires_event(self, mock_coordinator):
        event = {"cnt": 1, "type": "KeyManagement", "data": {
            "keyType": "guest key", "keyNr": 4, "bluetooth": False, "wifi": False,
            "internet": False, "notification": False, "isRemoved": True,
        }}
        await mock_coordinator._handle_device_event(event, replayed=False)
        name, data = mock_coordinator.hass.bus.async_fire.call_args.args
        assert name == EVENT_KEY_MANAGEMENT
        assert data["removed"] is True and data["key_nr"] == 4

    @pytest.mark.asyncio
    async def test_server_hello_updates_device_registry(self, mock_coordinator):
        from homeassistant.helpers import device_registry as dr
        registry = dr.async_get(mock_coordinator.hass)
        registry.reset_mock()
        registry.async_get_device.return_value = SimpleNamespace(id="dev1")
        try:
            await mock_coordinator._handle_server_hello({
                "type": "SERVER_HELLO", "apiVersion": 3,
                "serialNumber": "SN1", "remootioVersion": "remootio-2",
            })
        finally:
            registry.async_get_device.return_value = None

        assert mock_coordinator.api_version == 3
        assert mock_coordinator.device_info["model"] == "Remootio 2"
        registry.async_update_device.assert_called_once_with(
            "dev1", model="Remootio 2", sw_version="API v3", serial_number="SN1"
        )

    @pytest.mark.asyncio
    async def test_hold_open_refused_on_old_firmware(self, mock_coordinator):
        mock_coordinator.api.async_send_command = AsyncMock()
        mock_coordinator.api_version = 2
        with pytest.raises(ValueError, match="2.24"):
            await mock_coordinator.async_hold_open(1, 5)
        mock_coordinator.api.async_send_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_hold_open_allowed_when_version_unknown(self, mock_coordinator):
        mock_coordinator.api.async_send_command = AsyncMock(
            return_value={"response": {"success": True, "relayTriggered": True}}
        )
        listener = MagicMock(async_start=AsyncMock(), async_stop=AsyncMock())
        mock_coordinator._listener = listener
        assert mock_coordinator.api_version is None
        await mock_coordinator.async_hold_open(1, 5)
        mock_coordinator.api.async_send_command.assert_awaited_once()


# ── entities ───────────────────────────────────────────────────────────────

def _coord():
    c = MagicMock()
    c.host = TEST_HOST
    c.device_info = {"identifiers": {("remootio", TEST_HOST)}}
    c.last_operated = None
    c.hold_active = False
    return c


class TestLastOperatedSensor:

    def test_formats_coordinator_value(self):
        import datetime as dt
        c = _coord()
        c.last_operated = {"key_type": "master key", "key_nr": 0, "via": "bluetooth",
                           "time": dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)}
        s = RemootioLastOperatedSensor(c)
        assert s.native_value == "Master key via Bluetooth"
        assert s.extra_state_attributes["key_type"] == "master key"
        assert s._attr_unique_id.endswith("_last_operated_by_ch1")

    @pytest.mark.asyncio
    async def test_restores_previous_value(self):
        s = RemootioLastOperatedSensor(_coord())
        s.async_get_last_state = AsyncMock(return_value=SimpleNamespace(
            state="Guest key 2 via internet",
            attributes={"key_type": "guest key", "friendly_name": "x"},
        ))
        await s.async_added_to_hass()
        assert s.native_value == "Guest key 2 via internet"
        assert s.extra_state_attributes == {"key_type": "guest key"}

    def test_none_before_any_event(self):
        assert RemootioLastOperatedSensor(_coord()).native_value is None


class TestHoldActiveBinarySensor:

    def test_reflects_coordinator(self):
        c = _coord()
        b = RemootioHoldActiveBinarySensor(c)
        assert b.is_on is False
        c.hold_active = True
        assert b.is_on is True
        assert b.icon == "mdi:timer-lock-open"
        assert b._attr_unique_id.endswith("_hold_active_ch1")
