from datetime import timedelta
import logging
import uuid

import aiohttp

from homeassistant.components.light import ColorMode, LightEntity

from .const import BASE_API_URL, CONF_API_KEY

_LOGGER = logging.getLogger(__name__)

_API_BASE = f"{BASE_API_URL}/router/api/v1"

SCAN_INTERVAL = timedelta(seconds=300)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up Govee lights from a config entry."""
    _LOGGER.debug("Setting up Govee Lights")
    api_key = config_entry.data[CONF_API_KEY]

    headers = {"Govee-API-Key": api_key}

    # Correct endpoint for fetching devices
    api_url = _API_BASE + "/user/devices"

    async with aiohttp.ClientSession() as session:
        async with session.get(api_url, headers=headers) as response:
            if response.status != 200:
                _LOGGER.error("Failed to fetch devices: %s", await response.text())
                return

            response_data = await response.json()
            devices = response_data.get("data", {})
            _LOGGER.info("Initialised Govee OpenAPI. Found devices: %s", len(devices))
            _LOGGER.debug(
                "Devices: [%s]", ",".join([str(d["deviceName"]) for d in devices])
            )

            # Temporary filter before building a selector form
            lights = [
                GoveeLight(device, api_key)
                for device in devices
                if device.get("sku") == "H600D"
            ]
            async_add_entities(lights)


class GoveeLight(LightEntity):
    """Representation of a Govee light."""

    def __init__(self, device, api_key):
        self._device = device
        self._api_key = api_key
        self._state = False
        self._name = device["deviceName"]
        self._unique_id = device["device"]
        self._brightness = 255  # Default brightness (max)
        self._rgb_color = (255, 255, 255)  # Default white color
        self._skip_next_update = False  # Flag to skip next update

    @property
    def name(self):
        """Name of light."""
        return self._name

    @property
    def unique_id(self):
        """Unique ID of light."""
        return self._unique_id

    @property
    def is_on(self):
        """Is light on."""
        return self._state

    @property
    def supported_color_modes(self):
        """Return the supported color modes."""
        return {ColorMode.RGB}

    @property
    def color_mode(self):
        """Return the currently active color mode."""
        return ColorMode.RGB

    @property
    def brightness(self):
        """Return the brightness of the light."""
        return self._brightness

    @property
    def rgb_color(self):
        """Return the RGB color of the light."""
        return self._rgb_color

    async def async_turn_on(self, **kwargs):
        """Turns on the light."""
        self._state = True

        api_url = _API_BASE + "/device/control"
        payload = {
            "requestId": str(uuid.uuid4()),
            "payload": {
                "sku": self._device["sku"],
                "device": self._device["device"],
                "capability": self._build_capability("devices.capabilities.on_off", 1),
            },
        }
        status, json_payload = await self._send_command(api_url, payload)
        _LOGGER.debug("%s: turning on", self.name)
        if status == 200:
            self._state = True
            _LOGGER.debug(
                "%s: turned on. Current state: %s", self.name, str(self.is_on)
            )
        else:
            _LOGGER.error("%s: turned on failed", self.name)

        self.async_write_ha_state()
        self._skip_next_update = True  # Flag to skip next update

    async def async_turn_off(self, **kwargs):
        """Turns off the light."""
        self._state = False

        api_url = _API_BASE + "/device/control"
        payload = {
            "requestId": str(uuid.uuid4()),
            "payload": {
                "sku": self._device["sku"],
                "device": self._device["device"],
                "capability": self._build_capability("devices.capabilities.on_off", 0),
            },
        }
        status, json_payload = await self._send_command(api_url, payload)
        _LOGGER.debug("%s: turning off", self.name)
        if status == 200:
            self._state = False
            _LOGGER.debug(
                "%s: turned off. Current State: %s", self.name, str(self.is_on)
            )
        else:
            _LOGGER.error("%s: turned off failed", self.name)

        self.async_write_ha_state()
        self._skip_next_update = True  # Flag to skip next update

    async def async_update(self):
        """Fetch the latest state of the device from the Govee API."""
        if self._skip_next_update:
            _LOGGER.debug("Skipping update for %s", self.name)
            self._skip_next_update = False
            return

        api_url = _API_BASE + "/device/state"
        payload = {
            "requestId": str(uuid.uuid4()),
            "payload": {
                "sku": self._device["sku"],
                "device": self._device["device"],
            },
        }

        status, json_payload = await self._send_command(api_url, payload)
        if status == 200:
            on_off_json = next(
                (
                    item
                    for item in json_payload["payload"]["capabilities"]
                    if item["type"] == "devices.capabilities.on_off"
                ),
                None,
            )
            self._state = on_off_json["state"]["value"] == 1
            _LOGGER.debug("%s: updated. Current state: %s", self.name, str(self.is_on))
        else:
            _LOGGER.error("%s: update failed", self.name)

        self.async_write_ha_state()

    async def async_added_to_hass(self):
        """Fetch the initial state of the device when added to Home Assistant."""
        await self.async_update()

    async def _send_command(self, api_url, payload):
        """Send a command to the Govee API."""
        headers = {"Govee-API-Key": self._api_key}

        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=payload, headers=headers) as response:
                response_json = await response.json()
                if response.status != 200:
                    _LOGGER.error("Failed to send command: %s", response_json)
                return response.status, response_json

    def _build_capability(self, name, value):
        """Build the capability payload based on inputs."""
        # Find the capability definition by type
        capability_def = next(
            (
                item
                for item in self._device.get("capabilities", [])
                if item["type"] == name
            ),
            None,
        )

        if not capability_def:
            _LOGGER.error("Capability '%s' not found in device capabilities", name)
            return {}

        # Return the capability payload
        return {
            "type": name,
            "instance": capability_def.get("instance", "powerSwitch"),
            "value": value,
        }
