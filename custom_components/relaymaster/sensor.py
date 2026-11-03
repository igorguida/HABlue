"""
Support for RelayMaster analog inputs.
For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/sensor.relaymaster/
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import timedelta

import requests
import voluptuous as vol

from homeassistant.const import CONF_URL, CONF_USERNAME, CONF_PASSWORD
from homeassistant.components.sensor import SensorEntity, PLATFORM_SCHEMA
from homeassistant.util import Throttle
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=3)
MIN_TIME_BETWEEN_UPDATES = timedelta(milliseconds=200)

CONF_BASE_NUMBER = 'base_number'
CONF_IGNORE_UNUSED = 'ignore_unused'
DEVICE_CONFIG_ENDPOINT = '/ioconf.xml'
DEVICE_STATE_ENDPOINT = '/ajax.xml'
UNUSED_INPUT_REGEX = '^(ANINP|INPUT)_[0-9]+_'
INPUT_NODE_REGEX = '^i([0-9]+)$'
ANALOG_INPUT_NODE = 'a{}'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_URL): cv.url,
    vol.Required(CONF_USERNAME): cv.string,
    vol.Required(CONF_PASSWORD): cv.string,
    vol.Optional(CONF_BASE_NUMBER, default=12): cv.positive_int,
    vol.Optional(CONF_IGNORE_UNUSED, default=True): cv.boolean
})


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the relay board analog inputs."""
    base = config.get(CONF_BASE_NUMBER)
    ignore = config.get(CONF_IGNORE_UNUSED)
    base_url = config.get(CONF_URL).rstrip('/')
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)

    session = requests.Session()
    session.auth = requests.auth.HTTPBasicAuth(username, password)
    board = RelayMasterBoard(session, base_url)

    try:
        response = await hass.async_add_executor_job(board.get, DEVICE_CONFIG_ENDPOINT)
    except requests.exceptions.MissingSchema:
        _LOGGER.error("Missing resource or schema in configuration. "
                      "Add http:// to your URL")
        return

    if response is None:
        return

    inputs = []
    for child in ET.fromstring(response.text):
        match = re.search(INPUT_NODE_REGEX, child.tag)
        if not match:
            continue
        number = int(match.group(1))
        if number <= base:
            continue
        if ignore and child.text and re.match(UNUSED_INPUT_REGEX, child.text):
            continue
        inputs.append(RelayMasterAnalogInput(board, number, base, child.text or ''))

    async_add_entities(inputs)


class RelayMasterBoard:
    """Representation of board state."""

    def __init__(self, session, base_url):
        """Initialize the board object."""
        self._session = session
        self._base_url = base_url
        self.data = None

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        """Update the board data."""
        response = self.get(DEVICE_STATE_ENDPOINT)
        if response is None:
            return
        self.data = ET.fromstring(response.text)

    def get(self, endpoint, params=None):
        """Send request to board."""
        url = self._base_url + endpoint
        try:
            response = self._session.get(url, timeout=2, params=params)
        except requests.exceptions.ConnectionError:
            _LOGGER.warning("No route to device %s", self._base_url)
            return None

        if response.status_code != 200:
            _LOGGER.warning("Request error for %s: %s", response.url, response.text)
            return None

        return response


class RelayMasterAnalogInput(SensorEntity):
    """Representation of a RelayMaster analog input."""

    def __init__(self, board, number, base, name):
        """Initialize the input."""
        self._board = board
        self._number = number
        self._base = base
        self._name = name
        self._attr_native_value = None

    @property
    def name(self):
        """Return the name of the input."""
        return self._name.replace(".", " ").title()

    async def async_update(self):
        """Update the state of the input."""
        await self.hass.async_add_executor_job(self._board.update)

        if self._board.data is None:
            return

        analog_tag = ANALOG_INPUT_NODE.format(self._number - self._base)
        for child in self._board.data:
            if child.tag == analog_tag:
                try:
                    self._attr_native_value = int(child.text)
                except (ValueError, TypeError):
                    pass
                break
