"""Marstek Local API V2 – config flow."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .api import MarstekUDPClient, discover_devices, validate_connection
from .const import (
    CONF_BLE_MAC,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_DOD,
    CONF_ELECTRICITY_PRICE_ENTITY,
    CONF_ENERGY_TAX_ENTITY,
    CONF_FIRMWARE,
    CONF_GRID_POWER_ENTITY,
    CONF_MARKET_PRICE_ENTITY,
    CONF_MIN_SPREAD_ENTITY,
    CONF_PLAN_HOURS_ENTITY,
    CONF_PORT,
    CONF_PROCUREMENT_FEE_ENTITY,
    CONF_WIFI_MAC,
    DEFAULT_DOD,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

CONF_DEVICES = "devices"  # list of ble_macs selected
CONF_USE_ALL = "use_all_devices"


class MarstekConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle config flow for Marstek Local API V2."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: list[dict[str, Any]] = []
        self._selected_devices: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Entry point → go to discovery."""
        return await self.async_step_discovery()

    async def async_step_dhcp(self, discovery_info: Any) -> FlowResult:
        """DHCP discovery entry point."""
        return await self.async_step_discovery()

    async def async_step_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Broadcast-discover devices and let user select."""
        errors: dict[str, str] = {}

        if user_input is None:
            # Perform discovery
            try:
                self._discovered = await discover_devices()
            except Exception as err:
                _LOGGER.warning("Discovery failed: %s", err)
                self._discovered = []

            if not self._discovered:
                # No devices found → fall back to manual entry
                return await self.async_step_manual()

            # Build options for selector
            device_options = [
                selector.SelectOptionDict(
                    value=d["ble_mac"],
                    label=f"{d.get('device','Unknown')} – {d.get('ip','?')} (MAC: {d['ble_mac']})",
                )
                for d in self._discovered
            ]

            return self.async_show_form(
                step_id="discovery",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_DEVICES): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=device_options,
                                multiple=True,
                            )
                        )
                    }
                ),
                description_placeholders={
                    "count": str(len(self._discovered))
                },
            )

        # User selected devices
        selected_macs: list[str] = user_input[CONF_DEVICES]
        self._selected_devices = [
            d for d in self._discovered if d["ble_mac"] in selected_macs
        ]

        if not self._selected_devices:
            errors["base"] = "no_devices_selected"
            return self.async_show_form(step_id="discovery", errors=errors)

        return await self.async_step_name_devices()

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manual IP entry fallback."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input.get(CONF_PORT, DEFAULT_PORT)
            try:
                info = await validate_connection(host, port)
                self._selected_devices = [
                    {
                        "ble_mac": info.get("ble_mac", "unknown"),
                        "wifi_mac": info.get("wifi_mac", ""),
                        "device": info.get("device", "Unknown"),
                        "ver": info.get("ver", 0),
                        "ip": host,
                        "port": port,
                    }
                ]
                return await self.async_step_name_devices()
            except Exception as err:
                _LOGGER.error("Manual connection failed: %s", err)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
                }
            ),
            errors=errors,
        )

    async def async_step_name_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let user set friendly names for each device."""
        if user_input is not None:
            for dev in self._selected_devices:
                mac = dev["ble_mac"]
                dev["device_name"] = user_input.get(f"name_{mac}", "")
            return await self.async_step_options_initial()

        schema_fields: dict[Any, Any] = {}
        for dev in self._selected_devices:
            mac = dev["ble_mac"]
            default_name = f"Marstek {mac[-4:].upper()}"
            schema_fields[
                vol.Optional(f"name_{mac}", default=default_name)
            ] = str

        return self.async_show_form(
            step_id="name_devices",
            data_schema=vol.Schema(schema_fields),
        )

    async def async_step_options_initial(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Optional entity configuration (price, grid power, etc.)."""
        if user_input is not None:
            # Build config entry data
            devices_data = []
            for dev in self._selected_devices:
                devices_data.append(
                    {
                        CONF_BLE_MAC: dev["ble_mac"],
                        CONF_WIFI_MAC: dev.get("wifi_mac", ""),
                        CONF_DEVICE_MODEL: dev.get("device", "Unknown"),
                        CONF_FIRMWARE: dev.get("ver", 0),
                        CONF_DEVICE_NAME: dev.get("device_name", ""),
                        CONF_HOST: dev.get("ip", ""),
                        CONF_PORT: dev.get("port", DEFAULT_PORT),
                        CONF_DOD: DEFAULT_DOD,
                    }
                )

            # Unique ID: sorted BLE MACs space-joined
            unique_id = " ".join(
                sorted(d[CONF_BLE_MAC] for d in devices_data)
            )
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            title = (
                self._selected_devices[0].get("device_name")
                or f"Marstek {self._selected_devices[0]['ble_mac'][-4:].upper()}"
            )
            if len(self._selected_devices) > 1:
                title = f"Marstek Fleet ({len(self._selected_devices)} devices)"

            return self.async_create_entry(
                title=title,
                data={CONF_DEVICES: devices_data},
                options={
                    CONF_SCAN_INTERVAL: user_input.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                    CONF_ELECTRICITY_PRICE_ENTITY: user_input.get(
                        CONF_ELECTRICITY_PRICE_ENTITY, ""
                    ),
                    CONF_GRID_POWER_ENTITY: user_input.get(CONF_GRID_POWER_ENTITY, ""),
                    CONF_MARKET_PRICE_ENTITY: user_input.get(
                        CONF_MARKET_PRICE_ENTITY, ""
                    ),
                    CONF_ENERGY_TAX_ENTITY: user_input.get(CONF_ENERGY_TAX_ENTITY, ""),
                    CONF_PROCUREMENT_FEE_ENTITY: user_input.get(
                        CONF_PROCUREMENT_FEE_ENTITY, ""
                    ),
                    CONF_PLAN_HOURS_ENTITY: user_input.get(CONF_PLAN_HOURS_ENTITY, ""),
                    CONF_MIN_SPREAD_ENTITY: user_input.get(CONF_MIN_SPREAD_ENTITY, ""),
                },
            )

        return self.async_show_form(
            step_id="options_initial",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                    ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
                    vol.Optional(CONF_ELECTRICITY_PRICE_ENTITY, default=""): str,
                    vol.Optional(CONF_GRID_POWER_ENTITY, default=""): str,
                    vol.Optional(CONF_MARKET_PRICE_ENTITY, default=""): str,
                    vol.Optional(CONF_ENERGY_TAX_ENTITY, default=""): str,
                    vol.Optional(CONF_PROCUREMENT_FEE_ENTITY, default=""): str,
                    vol.Optional(CONF_PLAN_HOURS_ENTITY, default=""): str,
                    vol.Optional(CONF_MIN_SPREAD_ENTITY, default=""): str,
                }
            ),
            description_placeholders={
                "scan_interval_default": str(DEFAULT_SCAN_INTERVAL),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "MarstekOptionsFlow":
        return MarstekOptionsFlow(config_entry)


class MarstekOptionsFlow(OptionsFlow):
    """Handle options flow (reconfigure scan interval, entity references)."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        options = self.config_entry.options

        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_SCAN_INTERVAL: user_input.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                    CONF_ELECTRICITY_PRICE_ENTITY: user_input.get(
                        CONF_ELECTRICITY_PRICE_ENTITY, ""
                    ),
                    CONF_GRID_POWER_ENTITY: user_input.get(CONF_GRID_POWER_ENTITY, ""),
                    CONF_MARKET_PRICE_ENTITY: user_input.get(
                        CONF_MARKET_PRICE_ENTITY, ""
                    ),
                    CONF_ENERGY_TAX_ENTITY: user_input.get(CONF_ENERGY_TAX_ENTITY, ""),
                    CONF_PROCUREMENT_FEE_ENTITY: user_input.get(
                        CONF_PROCUREMENT_FEE_ENTITY, ""
                    ),
                    CONF_PLAN_HOURS_ENTITY: user_input.get(CONF_PLAN_HOURS_ENTITY, ""),
                    CONF_MIN_SPREAD_ENTITY: user_input.get(CONF_MIN_SPREAD_ENTITY, ""),
                },
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
                    vol.Optional(
                        CONF_ELECTRICITY_PRICE_ENTITY,
                        default=options.get(CONF_ELECTRICITY_PRICE_ENTITY, ""),
                    ): str,
                    vol.Optional(
                        CONF_GRID_POWER_ENTITY,
                        default=options.get(CONF_GRID_POWER_ENTITY, ""),
                    ): str,
                    vol.Optional(
                        CONF_MARKET_PRICE_ENTITY,
                        default=options.get(CONF_MARKET_PRICE_ENTITY, ""),
                    ): str,
                    vol.Optional(
                        CONF_ENERGY_TAX_ENTITY,
                        default=options.get(CONF_ENERGY_TAX_ENTITY, ""),
                    ): str,
                    vol.Optional(
                        CONF_PROCUREMENT_FEE_ENTITY,
                        default=options.get(CONF_PROCUREMENT_FEE_ENTITY, ""),
                    ): str,
                    vol.Optional(
                        CONF_PLAN_HOURS_ENTITY,
                        default=options.get(CONF_PLAN_HOURS_ENTITY, ""),
                    ): str,
                    vol.Optional(
                        CONF_MIN_SPREAD_ENTITY,
                        default=options.get(CONF_MIN_SPREAD_ENTITY, ""),
                    ): str,
                }
            ),
        )
