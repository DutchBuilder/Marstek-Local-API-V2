"""Marstek Local API V2 – button entities (operating mode control)."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MarstekDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


class MarstekModeButton(ButtonEntity):
    """Button that triggers an operating mode change on a Marstek device."""

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: DeviceInfo,
        unique_id: str,
        name: str,
        icon: str,
        press_fn,
    ) -> None:
        self._coordinator = coordinator
        self._attr_device_info = device_info
        self._attr_unique_id = unique_id
        self._attr_name = name
        self._attr_icon = icon
        self._press_fn = press_fn

    async def async_press(self) -> None:
        """Execute the mode change."""
        try:
            await self._press_fn()
            await self._coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to execute button %s: %s", self._attr_name, err)
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

        # Auto mode
        entities.append(
            MarstekModeButton(
                coord, device_info,
                f"{uid_prefix}_btn_auto_mode",
                f"Auto Mode ({suffix})",
                "mdi:auto-mode",
                client.set_mode_auto,
            )
        )

        # AI mode
        entities.append(
            MarstekModeButton(
                coord, device_info,
                f"{uid_prefix}_btn_ai_mode",
                f"AI Mode ({suffix})",
                "mdi:brain",
                client.set_mode_ai,
            )
        )

        # UPS mode (Rev 2.0 new)
        entities.append(
            MarstekModeButton(
                coord, device_info,
                f"{uid_prefix}_btn_ups_mode",
                f"UPS Mode ({suffix})",
                "mdi:power-plug-battery",
                client.set_mode_ups,
            )
        )

    async_add_entities(entities)
