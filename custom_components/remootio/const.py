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
