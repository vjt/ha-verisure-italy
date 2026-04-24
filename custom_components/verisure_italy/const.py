"""Constants for the Verisure Italy integration."""

DOMAIN = "verisure_italy"

CONF_INSTALLATION_NUMBER = "installation_number"
CONF_INSTALLATION_PANEL = "installation_panel"
CONF_INSTALLATION_ALIAS = "installation_alias"
# v2+: full Installation dump (model_dump(mode="json")). Supersedes the
# three scalars above, which remain for backward compatibility.
CONF_INSTALLATION = "installation"
CONF_DEVICE_ID = "device_id"
CONF_UUID = "uuid"

CONF_POLL_INTERVAL = "poll_interval"
CONF_POLL_TIMEOUT = "poll_timeout"
CONF_POLL_DELAY = "poll_delay"

DEFAULT_POLL_INTERVAL = 5
DEFAULT_POLL_TIMEOUT = 60
DEFAULT_POLL_DELAY = 2
