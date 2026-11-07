"""Constants for the Remootio integration."""

DOMAIN = "remootio"

CONF_API_SECRET_KEY = "api_secret_key"
CONF_API_AUTH_KEY = "api_auth_key"

DEFAULT_NAME = "Remootio Garage Door"
DEFAULT_PORT = 8080

MANUFACTURER = "Remootio"
MODEL = "Garage Door Controller"

SIGNAL_REMOOTIO_STATE_CHANGED = "remootio_state_changed"

SERVICE_HOLD_OPEN = "hold_open"
ATTR_DURATION = "duration"
ATTR_USE_TRIGGER = "use_trigger"
# The API spec gives no maximum for ``duration``; cap it at 24 hours so a
# typo can't latch the gate open for days with no way to release it early.
MAX_HOLD_MINUTES = 1440
# Starting value for the "Hold open duration" number entity.
DEFAULT_HOLD_MINUTES = 15

# Home Assistant bus events fired from device events (API logging required).
EVENT_RELAY_TRIGGER = "remootio_relay_trigger"
EVENT_KEY_MANAGEMENT = "remootio_key_management"

# Display names for the keyType / via fields of RelayTrigger events.
KEY_TYPE_NAMES = {
    "master key": "Master key",
    "unique key": "Unique key",
    "guest key": "Guest key",
    "api key": "API key",
    "smart home": "Smart home",
    "automation": "Automation",
}
# Key types that are numbered per key, so "Guest key 5" is meaningful.
NUMBERED_KEY_TYPES = ("unique key", "guest key")
VIA_NAMES = {
    "bluetooth": "Bluetooth",
    "wifi": "Wi-Fi",
    "internet": "internet",
    "autoopen": "auto-open",
}
REMOOTIO_MODELS = {
    "remootio-1": "Remootio 1",
    "remootio-2": "Remootio 2",
    "remootio-3": "Remootio 3",
}
# API v3 (firmware 2.24+) is the first to accept the ``duration`` field.
MIN_API_VERSION_HOLD = 3


def format_operator(key_type: str | None, key_nr: int | None, via: str | None) -> str:
    """Render RelayTrigger key info as e.g. "Guest key 5 via internet"."""
    if not key_type:
        return "Unknown"
    text = KEY_TYPE_NAMES.get(key_type, str(key_type).capitalize())
    if key_type in NUMBERED_KEY_TYPES and key_nr is not None:
        text = f"{text} {key_nr}"
    if via and via not in ("none", "unknown"):
        text = f"{text} via {VIA_NAMES.get(via, via)}"
    return text
