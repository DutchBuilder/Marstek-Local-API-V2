"""Marstek Local API V2 – number entities (DOD configuration)."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DOD, DEFAULT_DOD, DOMAIN
from .coordinator import MarstekDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


class MarstekDODNumber(NumberEntity):
    """
    DOD (Depth of Discharge) number entity.

    Sending DOD.SET to the device. Range: 30–88 (API spec).
    The stored value is used by BeschikbareKwh sensor.
    """

    _attr_native_min_value = 30.0
    _attr_native_max_value = 88.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = NumberDeviceClass.BATTERY
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:battery-sync"

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: DeviceInfo,
        unique_id: str,
        suffix: str,
    ) -> None:
        self._coordinator = coordinator
        self._attr_device_info = device_info
        self._attr_unique_id = unique_id
        self._attr_name = f"DOD ({suffix})"

    @property
    def native_value(self) -> float:
        return float((self._coordinator.data or {}).get("dod", DEFAULT_DOD))

    async def async_set_native_value(self, value: float) -> None:
        """Set DOD via API and store in coordinator."""
        int_val = int(value)
        try:
            result = await self._coordinator.client.set_dod(int_val)
            if result.get("set_result") is False:
                _LOGGER.warning("DOD.SET returned failure for value %d", int_val)
                return
        except Exception as err:
            _LOGGER.error("Failed to set DOD to %d: %s", int_val, err)
            raise

        self._coordinator.dod = int_val
        await self._coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    domain_data = hass.data[DOMAIN][entry.entry_id]
    device_coordinators: dict[str, MarstekDataUpdateCoordinator] = domain_data[
        "device_coordinators"
    ]
    devices_info: dict[str, dict] = domain_data["devices_info"]

    entities = []

    for ble_mac, coord in device_coordinators.items():
        info = devices_info.get(ble_mac, {})
        model = info.get("device_model", "Unknown")
        device_name = info.get("device_name") or f"Marstek {ble_mac[-4:].upper()}"
        suffix = ble_mac[-4:].upper()

        device_info = DeviceInfo(
            identifiers={(DOMAIN, ble_mac)},
            name=device_name,
            manufacturer="Marstek",
            model=model,
        )

        entities.append(
            MarstekDODNumber(
                coord,
                device_info,
                f"{entry.entry_id}_{ble_mac}_dod",
                suffix,
            )
        )

    async_add_entities(entities)
