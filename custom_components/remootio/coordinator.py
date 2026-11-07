"""DataUpdateCoordinator for Remootio integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import RemootioAPI, RemootioEventListener
from .const import (
    DEFAULT_HOLD_MINUTES,
    DOMAIN,
    EVENT_KEY_MANAGEMENT,
    EVENT_RELAY_TRIGGER,
    MANUFACTURER,
    MIN_API_VERSION_HOLD,
    MODEL,
    REMOOTIO_MODELS,
    SIGNAL_REMOOTIO_STATE_CHANGED,
)

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(seconds=30)


class RemootioCoordinator(DataUpdateCoordinator[dict[int, str | None]]):
    """Coordinator that polls relay 1 and maintains a real-time event listener.

    Two independent mechanisms tolerate a flaky device, at different layers
    and for different reasons — they're intentionally not unified:

    - ``RemootioEventListener`` (api.py) uses a time-based exponential
      backoff to keep retrying its own WebSocket reconnect indefinitely.
      That's about the listener staying alive, not about entity state.
    - ``_MAX_CONSECUTIVE_FAILURES`` below is a poll-count threshold that
      decides when *entities* should stop trusting the cached state and go
      unavailable. It only applies to the polling fallback path, since a
      connected *and recently active* listener already proves liveness on
      its own (see ``_async_update_data``) — ``_STALE_LISTENER_SECONDS`` is
      what "recently active" means, because ``connected`` by itself isn't
      trustworthy indefinitely (see below).
    """

    # RemootioAPI.async_send_command swallows most communication errors
    # (timeouts, connection failures) and returns None rather than raising,
    # so a real outage shows up here as a falsy/invalid QUERY response, not
    # an exception. Tolerate a few in a row (transient blips) before raising
    # UpdateFailed — but once we hit this many consecutive misses, raise so
    # last_update_success flips False and entities go unavailable instead of
    # silently reporting a stale state indefinitely.
    #
    # This threshold only governs that soft-failure path. A hard exception
    # (CannotConnect/InvalidAuth, raised when the auth handshake itself
    # fails — e.g. wrong keys or an unexpected protocol response) is a
    # configuration-level error that won't self-heal by retrying, so it
    # deliberately raises UpdateFailed immediately rather than waiting out
    # this same tolerance window.
    _MAX_CONSECUTIVE_FAILURES = 3

    # A half-open TCP connection can leave the listener's `connected` flag
    # stuck True long after it has actually stopped receiving anything — the
    # local socket keeps accepting writes even though nothing more will ever
    # arrive from the peer. `connected` alone can't be trusted forever, so
    # once a nominally-connected listener has been silent for this long,
    # polling resumes despite it. Matches the listener's own
    # `_ACTIVITY_TIMEOUT` (api.py) — its watchdog should have already forced
    # a reconnect by this point; this is the independent backstop for the
    # case where it hasn't.
    _STALE_LISTENER_SECONDS = 90

    def __init__(
        self,
        hass: HomeAssistant,
        api: RemootioAPI,
        name: str,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"Remootio {name}",
            update_interval=UPDATE_INTERVAL,
        )
        self.api = api
        self._device_name = name
        self._previous_states: dict[int, str | None] = {1: None}
        self._listener: RemootioEventListener | None = None
        self._consecutive_failures = 0
        # Serializes TRIGGER/OPEN/CLOSE against the poller: both stop the
        # listener to get exclusive WebSocket access, and the device only
        # accepts one connection at a time. See _async_send_exclusive and
        # the poll-skip check in _async_update_data.
        self._command_lock = asyncio.Lock()
        # Duration used by the "Hold open" button. Owned here so the number
        # entity (which sets it, and restores it across restarts) and the
        # button (which reads it) share one value.
        self.hold_open_minutes: int = DEFAULT_HOLD_MINUTES
        # From device events (see _handle_device_event).
        self.hold_active: bool = False
        self.last_operated: dict | None = None
        # From SERVER_HELLO (see _handle_server_hello); None until known.
        self.api_version: int | None = None
        self.serial_number: str | None = None
        self.remootio_version: str | None = None
        self._model: str = MODEL

    @property
    def host(self) -> str:
        """Return the device host for unique ID construction."""
        return self.api.host

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info shared by all entities."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.api.host)},
            name=self._device_name,
            manufacturer=MANUFACTURER,
            model=self._model,
        )

    async def async_start_event_listener(self) -> None:
        """Start the persistent WebSocket event listener.

        The listener instance is created once and reused across restarts so
        its event-counter dedup survives the stop/start cycle around
        exclusive commands (TRIGGER/OPEN/CLOSE).
        """
        if self._listener is None:
            self._listener = RemootioEventListener(
                self.api,
                self._handle_event_state_change,
                on_event=self._handle_device_event,
                on_hello=self._handle_server_hello,
            )
        await self._listener.async_start()

    async def async_stop_event_listener(self) -> None:
        """Stop the persistent WebSocket event listener."""
        if self._listener:
            await self._listener.async_stop()

    async def _handle_event_state_change(self, state: str) -> None:
        """Handle a real-time StateChange event from the listener."""
        if state is None:
            return

        # A delivered event proves the listener has live connectivity right
        # now, regardless of whether the state actually changed — reset the
        # poll-failure streak so a stale count from an earlier blip can't
        # combine with a later, unrelated poll miss to trip the threshold.
        self._consecutive_failures = 0

        old_state = self._previous_states.get(1)
        if old_state == state:
            return

        self._previous_states[1] = state
        new_data = dict(self.data or {})
        new_data[1] = state
        self.async_set_updated_data(new_data)

        if old_state is not None:
            signal = f"{SIGNAL_REMOOTIO_STATE_CHANGED}_{self.api.host}_ch1"
            async_dispatcher_send(self.hass, signal, old_state, state)

    async def _handle_server_hello(self, frame: dict) -> None:
        """Record device identity from SERVER_HELLO and update the registry."""
        api_version = frame.get("apiVersion")
        self.api_version = api_version if isinstance(api_version, int) else None
        self.serial_number = frame.get("serialNumber") or None
        self.remootio_version = frame.get("remootioVersion") or None
        self._model = REMOOTIO_MODELS.get(self.remootio_version, MODEL)

        if self.api_version is not None and self.api_version < MIN_API_VERSION_HOLD:
            _LOGGER.warning(
                "%s reports Remootio API v%s; hold open needs API v%s "
                "(Remootio firmware 2.24 or later)",
                self.api.host,
                self.api_version,
                MIN_API_VERSION_HOLD,
            )

        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, self.api.host)})
        if device is None:
            return
        changes: dict = {"model": self._model}
        if self.api_version is not None:
            changes["sw_version"] = f"API v{self.api_version}"
        if self.serial_number:
            changes["serial_number"] = self.serial_number
        try:
            registry.async_update_device(device.id, **changes)
        except TypeError:
            # serial_number needs HA 2023.11+; keep the rest on older cores.
            changes.pop("serial_number", None)
            registry.async_update_device(device.id, **changes)

    async def _handle_device_event(self, event: dict, replayed: bool) -> None:
        """Handle a non-StateChange device event from the listener.

        State-like events (output held / released) are always applied —
        replaying them in order converges on the right state. One-shot
        events (who triggered, key changes) are skipped when *replayed*, so
        a Home Assistant restart doesn't re-fire old ones from the device's
        buffer.
        """
        event_type = event.get("type")
        data = event.get("data") or {}

        if event_type in ("Output1Activated", "OutputHeldActive"):
            self._set_hold_active(True)
        elif event_type == "Output1Deactivated":
            self._set_hold_active(False)

        if replayed:
            return

        base = {"host": self.api.host, "device_name": self._device_name}
        if event_type in ("RelayTrigger", "SecondaryRelayTrigger"):
            output = 1 if event_type == "RelayTrigger" else 2
            info = {
                "output": output,
                "key_type": data.get("keyType"),
                "key_nr": data.get("keyNr"),
                "via": data.get("via"),
            }
            self.hass.bus.async_fire(EVENT_RELAY_TRIGGER, {**base, **info})
            if output == 1:
                self.last_operated = {**info, "time": dt_util.utcnow()}
                self.async_update_listeners()
        elif event_type == "KeyManagement":
            self.hass.bus.async_fire(
                EVENT_KEY_MANAGEMENT,
                {
                    **base,
                    "key_type": data.get("keyType"),
                    "key_nr": data.get("keyNr"),
                    "removed": bool(data.get("isRemoved", False)),
                    "bluetooth": data.get("bluetooth"),
                    "wifi": data.get("wifi"),
                    "internet": data.get("internet"),
                    "notification": data.get("notification"),
                },
            )
        else:
            _LOGGER.debug("Unhandled Remootio event: %s", event_type)

    def _set_hold_active(self, active: bool) -> None:
        """Update the hold-active flag and notify entities on change."""
        if self.hold_active != active:
            self.hold_active = active
            self.async_update_listeners()

    async def _async_update_data(self) -> dict[int, str | None]:
        """Query relay 1 only.

        The Remootio API has no QUERY_SECONDARY — QUERY always returns the
        primary output state.  Relay 2 (secondary) has no queryable state.

        Skips the network call while the event listener holds a live,
        recently-active connection: the device only accepts one WebSocket
        connection at a time, so opening a second connection for polling
        would time out while the listener holds the persistent one.  When
        the listener is down, reconnecting, or reports connected but hasn't
        received anything in over ``_STALE_LISTENER_SECONDS`` (a half-open
        TCP connection can leave ``connected`` stuck True long after it's
        actually stopped delivering anything), polling resumes as a
        fallback so the state can never stay frozen indefinitely.

        A missing/invalid response is tolerated for up to
        ``_MAX_CONSECUTIVE_FAILURES`` consecutive polls (a transient blip
        keeps serving the cached state); beyond that this raises
        ``UpdateFailed``, which HA turns into ``last_update_success = False``
        so dependent entities go unavailable instead of reporting a stale
        state through an extended outage.
        """
        if self._listener is not None and self._listener.connected:
            if self._listener.seconds_idle < self._STALE_LISTENER_SECONDS:
                # A live, authenticated listener connection is itself proof
                # the device is reachable right now.
                self._consecutive_failures = 0
                return dict(self._previous_states)
            _LOGGER.warning(
                "Event listener for %s reports connected but has received "
                "nothing for %.0fs — polling despite the connected flag",
                self.api.host,
                self._listener.seconds_idle,
            )

        if self._command_lock.locked():
            # An exclusive command (TRIGGER/OPEN/CLOSE) currently has the
            # listener stopped for exclusive WebSocket access — skip this
            # cycle rather than race it with a second connection. This is
            # neither a success nor a failure, so the streak is left as-is.
            return dict(self._previous_states)

        async with self._command_lock:
            try:
                result = await self.api.async_send_command("QUERY", 1)
            except Exception as err:
                _LOGGER.warning(
                    "Error querying relay 1: %s — marking update failed", err
                )
                raise UpdateFailed("Unable to communicate with Remootio device") from err

        response = result.get("response") if result else None
        # Require an explicit state and no explicit failure flag — a present
        # but malformed response (e.g. missing "state") is just as much a
        # failure as no response at all, and must not be treated as a
        # successful poll (which would wipe out the last known door state).
        if response and response.get("state") is not None and response.get("success", True):
            state = response.get("state")
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
            _LOGGER.warning(
                "Invalid or missing response for relay 1 (%d consecutive), "
                "keeping previous state",
                self._consecutive_failures,
            )
            if self._consecutive_failures >= self._MAX_CONSECUTIVE_FAILURES:
                raise UpdateFailed(
                    f"No valid response from Remootio device for "
                    f"{self._consecutive_failures} consecutive polls"
                )
            state = self._previous_states.get(1)

        states: dict[int, str | None] = {1: state}

        old_state = self._previous_states.get(1)
        if old_state is not None and state is not None and old_state != state:
            signal = f"{SIGNAL_REMOOTIO_STATE_CHANGED}_{self.api.host}_ch1"
            async_dispatcher_send(self.hass, signal, old_state, state)

        self._previous_states = dict(states)
        return states

    async def _async_send_exclusive(
        self, command_type: str, relay_number: int, duration: int | None = None
    ) -> None:
        """Send *command_type* with exclusive WebSocket access.

        Stops the event listener first so the command gets exclusive
        WebSocket access — the device rejects a second concurrent connection.
        Holds ``_command_lock`` for the same reason against the poller, and
        to serialize overlapping command calls (e.g. a rapid double press)
        so they can't both try to stop/restart the listener at once.  No
        explicit refresh is needed: the restarted listener queries the door
        state as part of its connect handshake, which both syncs any change
        caused by the command and avoids a polling connection racing the
        listener's reconnect.
        """
        result: dict | None = None
        async with self._command_lock:
            await self.async_stop_event_listener()
            try:
                if duration is None:
                    result = await self.api.async_send_command(
                        command_type, relay_number
                    )
                else:
                    result = await self.api.async_send_command(
                        command_type, relay_number, duration=duration
                    )
            finally:
                await self.async_start_event_listener()

        # The response is otherwise unused (fire-and-forget) — the listener's
        # reconnect QUERY is what syncs door state — but a device-side
        # rejection or dropped response would otherwise fail completely
        # silently.
        if result is None:
            _LOGGER.warning(
                "%s command to relay %d got no response from the device",
                command_type,
                relay_number,
            )
        elif result.get("response", {}).get("success") is False:
            _LOGGER.warning(
                "%s command to relay %d was rejected by the device",
                command_type,
                relay_number,
            )
        elif (
            duration is not None
            and result.get("response", {}).get("relayTriggered") is False
        ):
            # Directional OPEN only fires from "closed", so a hold requested
            # while the gate is already open is silently skipped by the
            # firmware — say so, or the user is left wondering why the gate
            # closed on its own schedule anyway.
            _LOGGER.warning(
                "%s with a %d-minute hold on relay %d did not trigger the "
                "relay (the gate was probably already in the target state), "
                "so no hold was applied",
                command_type,
                duration,
                relay_number,
            )

    async def async_trigger(self, relay_number: int) -> None:
        """Send TRIGGER command.

        TRIGGER always toggles the gate regardless of its current state.
        This is what the non-directional toggle buttons want; the cover
        entity's open/close must NOT use this — see async_open/async_close.
        """
        await self._async_send_exclusive("TRIGGER", relay_number)

    async def async_open(self, relay_number: int) -> None:
        """Send OPEN command.

        Unlike TRIGGER, OPEN/CLOSE are directional and gate on the door's
        current status in firmware, so this is a no-op if the door is
        already open — matching Remootio's own cloud/SmartThings integration.

        Only relay 1 (the primary output) supports OPEN — the API has no
        directional action for the secondary relay, only TRIGGER_SECONDARY.
        """
        if relay_number != 1:
            raise ValueError(
                "OPEN is only supported on relay 1 — Remootio has no "
                "directional action for the secondary relay"
            )
        await self._async_send_exclusive("OPEN", relay_number)

    async def async_close(self, relay_number: int) -> None:
        """Send CLOSE command. See async_open for why this isn't TRIGGER.

        Only relay 1 (the primary output) supports CLOSE — see async_open.
        """
        if relay_number != 1:
            raise ValueError(
                "CLOSE is only supported on relay 1 — Remootio has no "
                "directional action for the secondary relay"
            )
        await self._async_send_exclusive("CLOSE", relay_number)

    async def async_hold_open(
        self, relay_number: int, duration: int, use_trigger: bool = False
    ) -> None:
        """Open and hold the output active for *duration* minutes (API v3).

        Sends OPEN with the v3 ``duration`` field by default, so it only acts
        when the gate is closed and can never close an open gate by mistake.
        OPEN needs a sensor (the device answers ``ERR_NO_SENSOR`` without
        one), so *use_trigger* sends TRIGGER with the same field instead for
        installs without a sensor — note TRIGGER toggles, so on those it will
        close a gate that is already open.

        The Remootio API has no way to release a held output early; the hold
        ends only when *duration* elapses. Requires Remootio firmware 2.24+.
        """
        if relay_number != 1:
            raise ValueError(
                "hold_open is only supported on relay 1 (the cover entity)"
            )
        duration = int(duration)
        if duration < 1:
            raise ValueError("duration must be at least 1 minute")
        if self.api_version is not None and self.api_version < MIN_API_VERSION_HOLD:
            raise ValueError(
                f"Hold open needs Remootio firmware 2.24 or later (API "
                f"v{MIN_API_VERSION_HOLD}); this device reports API "
                f"v{self.api_version}. Update it in the Remootio app."
            )
        await self._async_send_exclusive(
            "TRIGGER" if use_trigger else "OPEN", relay_number, duration=duration
        )
