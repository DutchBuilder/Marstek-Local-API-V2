"""Marstek Local API V2 – integration setup."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api import MarstekUDPClient
from .const import (
    CONF_BLE_MAC,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_DOD,
    CONF_FIRMWARE,
    CONF_WIFI_MAC,
    DEFAULT_DOD,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLAN_SENSORS_ENTRY_KEY,
)
from .coordinator import MarstekDataUpdateCoordinator, MarstekMultiDeviceCoordinator
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SWITCH,
]

# Key in hass.data[DOMAIN] for tracking whether services are set up
_SERVICES_SETUP_KEY = "_services_set_up"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Marstek Local API V2 from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    devices_config: list[dict[str, Any]] = entry.data.get("devices", [])
    if not devices_config:
        raise ConfigEntryNotReady("No device configuration found")

    options = entry.options
    scan_interval = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    device_coordinators: dict[str, MarstekDataUpdateCoordinator] = {}
    devices_info: dict[str, dict] = {}

    for dev in devices_config:
        ble_mac = dev[CONF_BLE_MAC]
        host = dev.get(CONF_HOST, "")
        port = dev.get(CONF_PORT, DEFAULT_PORT)
        model = dev.get(CONF_DEVICE_MODEL, "Unknown")
        dod = dev.get(CONF_DOD, DEFAULT_DOD)
        device_name = dev.get(CONF_DEVICE_NAME, f"Marstek {ble_mac[-4:].upper()}")

        client = MarstekUDPClient(host=host, port=port)
        try:
            await client.connect()
        except Exception as err:
            _LOGGER.error("Failed to connect to Marstek %s at %s:%d: %s", ble_mac, host, port, err)
            raise ConfigEntryNotReady(f"Cannot connect to {host}:{port}") from err

        coord = MarstekDataUpdateCoordinator(
            hass=hass,
            client=client,
            ble_mac=ble_mac,
            device_model=model,
            scan_interval=scan_interval,
            dod=dod,
        )

        # First data fetch
        try:
            await coord.async_config_entry_first_refresh()
        except Exception as err:
            await client.disconnect()
            raise ConfigEntryNotReady(f"Initial data fetch failed for {ble_mac}") from err

        device_coordinators[ble_mac] = coord
        devices_info[ble_mac] = {
            "device_model": model,
            "device_name": device_name,
            CONF_FIRMWARE: dev.get(CONF_FIRMWARE, 0),
            CONF_WIFI_MAC: dev.get(CONF_WIFI_MAC, ""),
        }

    # Create multi-device coordinator (aggregator)
    multi_coordinator = MarstekMultiDeviceCoordinator(
        hass=hass,
        device_coordinators=device_coordinators,
        scan_interval=scan_interval,
    )
    await multi_coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "device_coordinators": device_coordinators,
        "multi_coordinator": multi_coordinator,
        "devices_info": devices_info,
        "devices_config": devices_config,
    }

    # Set up services (once per domain)
    if not hass.data[DOMAIN].get(_SERVICES_SETUP_KEY):
        async_setup_services(hass)
        hass.data[DOMAIN][_SERVICES_SETUP_KEY] = True

    # Claim plan sensors ownership for the first entry that is set up
    if not hass.data[DOMAIN].get(PLAN_SENSORS_ENTRY_KEY):
        hass.data[DOMAIN][PLAN_SENSORS_ENTRY_KEY] = entry.entry_id

    # Register update listener for options changes
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Forward to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        domain_data = hass.data[DOMAIN].pop(entry.entry_id, {})

        # Disconnect all API clients
        for coord in domain_data.get("device_coordinators", {}).values():
            try:
                await coord.client.disconnect()
            except Exception:
                pass

        # Release plan sensors ownership if this entry held it
        if hass.data[DOMAIN].get(PLAN_SENSORS_ENTRY_KEY) == entry.entry_id:
            hass.data[DOMAIN].pop(PLAN_SENSORS_ENTRY_KEY, None)

        # Unload services if no entries remain (excluding meta keys)
        remaining = [k for k in hass.data[DOMAIN] if not k.startswith("_")]
        if not remaining:
            async_unload_services(hass)
            hass.data[DOMAIN].pop(_SERVICES_SETUP_KEY, None)

    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
