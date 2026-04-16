"""Marstek Local API V2 – switch entities (LED and BLE advertising)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MarstekDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


class MarstekSwitch(SwitchEntity):
    """Generic switch entity for Marstek device toggles."""

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: DeviceInfo,
        unique_id: str,
        name: str,
        icon_on: str,
        icon_off: str,
        turn_on_fn,
        turn_off_fn,
        state_fn=None,
    ) -> None:
        self._coordinator = coordinator
        self._attr_device_info = device_info
        self._attr_unique_id = unique_id
        self._attr_name = name
        self._icon_on = icon_on
        self._icon_off = icon_off
        self._turn_on_fn = turn_on_fn
        self._turn_off_fn = turn_off_fn
        self._state_fn = state_fn
        self._optimistic_state: bool | None = None

    @property
    def icon(self) -> str:
        return self._icon_on if self.is_on else self._icon_off

    @property
    def is_on(self) -> bool | None:
        if self._optimistic_state is not None:
            return self._optimistic_state
        if self._state_fn is not None:
            try:
                return self._state_fn(self._coordinator.data or {})
            except Exception:
                return None
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            await self._turn_on_fn()
            self._optimistic_state = True
            await self._coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to turn on %s: %s", self._attr_name, err)
            raise

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self._turn_off_fn()
            self._optimistic_state = False
            await self._coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to turn off %s: %s", self._attr_name, err)
            raise


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
        uid_prefix = f"{entry.entry_id}_{ble_mac}"
        client = coord.client

        # LED switch
        entities.append(
            MarstekSwitch(
                coord, device_info,
                f"{uid_prefix}_led",
                f"LED ({suffix})",
                icon_on="mdi:led-on",
                icon_off="mdi:led-off",
                turn_on_fn=lambda: client.set_led(True),
                turn_off_fn=lambda: client.set_led(False),
            )
        )

        # BLE advertising switch
        entities.append(
            MarstekSwitch(
                coord, device_info,
                f"{uid_prefix}_ble_advertising",
                f"BLE Advertising ({suffix})",
                icon_on="mdi:bluetooth",
                icon_off="mdi:bluetooth-off",
                turn_on_fn=lambda: client.set_ble_advertising(True),
                turn_off_fn=lambda: client.set_ble_advertising(False),
            )
        )

    async_add_entities(entities)
