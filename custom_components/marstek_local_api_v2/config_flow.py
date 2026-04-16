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

CONF_DEVICES = "devices"


# ─────────────────────────────────────────────────────────────────────────────
# Selector helpers
# ─────────────────────────────────────────────────────────────────────────────

def _entity_selector(domain: str | None = None) -> selector.EntitySelector:
    """Return an EntitySelector, optionally filtered to a domain."""
    if domain:
        return selector.EntitySelector(
            selector.EntitySelectorConfig(domain=domain)
        )
    return selector.EntitySelector(selector.EntitySelectorConfig())


def _options_schema(current: dict[str, Any]) -> vol.Schema:
    """
    Build the options schema with EntitySelectors and suggested default values.

    Using suggested_value (description placeholder) instead of hard default so
    users who don't have these entities aren't forced to clear them.
    """
    def _sv(key: str, fallback: str = "") -> dict:
        """Return a description dict with suggested_value from current options or fallback."""
        val = current.get(key) or fallback
        return {"suggested_value": val} if val else {}

    return vol.Schema(
        {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),

            # All-in electricity price (e.g. sensor.stroomprijs_totaal)
            vol.Optional(
                CONF_ELECTRICITY_PRICE_ENTITY,
                description=_sv(CONF_ELECTRICITY_PRICE_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig()),

            # P1 meter active power sensor (W) – positive = importing from grid
            vol.Optional(
                CONF_GRID_POWER_ENTITY,
                description=_sv(CONF_GRID_POWER_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig()),

            # Market price sensor with 'prices' attribute (Nordpool / EPEX)
            vol.Optional(
                CONF_MARKET_PRICE_ENTITY,
                description=_sv(CONF_MARKET_PRICE_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig()),

            # Energiebelasting excl. BTW (€/kWh) – default 0.0
            vol.Optional(
                CONF_ENERGY_TAX_ENTITY,
                description=_sv(CONF_ENERGY_TAX_ENTITY, "input_number.energiebelasting_kwh"),
            ): selector.EntitySelector(selector.EntitySelectorConfig()),

            # Inkoopvergoeding (€/kWh) – default ~0.0166
            vol.Optional(
                CONF_PROCUREMENT_FEE_ENTITY,
                description=_sv(CONF_PROCUREMENT_FEE_ENTITY, "input_number.inkoopvergoeding_kwh"),
            ): selector.EntitySelector(selector.EntitySelectorConfig()),

            # Number of charge/discharge hours for plan – default 6
            vol.Optional(
                CONF_PLAN_HOURS_ENTITY,
                description=_sv(
                    CONF_PLAN_HOURS_ENTITY,
                    "input_number.input_number_marstek_plan_hours",
                ),
            ): selector.EntitySelector(selector.EntitySelectorConfig()),

            # Minimum price spread (€/kWh) – default 0.062
            vol.Optional(
                CONF_MIN_SPREAD_ENTITY,
                description=_sv(
                    CONF_MIN_SPREAD_ENTITY,
                    "input_number.input_number_marstek_min_spread",
                ),
            ): selector.EntitySelector(selector.EntitySelectorConfig()),
        }
    )


def _clean_options(user_input: dict[str, Any], scan_interval: int) -> dict[str, Any]:
    """Normalise option values – replace None with empty string."""
    return {
        CONF_SCAN_INTERVAL: user_input.get(CONF_SCAN_INTERVAL, scan_interval),
        CONF_ELECTRICITY_PRICE_ENTITY: user_input.get(CONF_ELECTRICITY_PRICE_ENTITY) or "",
        CONF_GRID_POWER_ENTITY: user_input.get(CONF_GRID_POWER_ENTITY) or "",
        CONF_MARKET_PRICE_ENTITY: user_input.get(CONF_MARKET_PRICE_ENTITY) or "",
        CONF_ENERGY_TAX_ENTITY: user_input.get(CONF_ENERGY_TAX_ENTITY) or "",
        CONF_PROCUREMENT_FEE_ENTITY: user_input.get(CONF_PROCUREMENT_FEE_ENTITY) or "",
        CONF_PLAN_HOURS_ENTITY: user_input.get(CONF_PLAN_HOURS_ENTITY) or "",
        CONF_MIN_SPREAD_ENTITY: user_input.get(CONF_MIN_SPREAD_ENTITY) or "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Config flow
# ─────────────────────────────────────────────────────────────────────────────

class MarstekConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle config flow for Marstek Local API V2."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: list[dict[str, Any]] = []
        self._selected_devices: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self.async_step_discovery()

    async def async_step_dhcp(self, discovery_info: Any) -> FlowResult:
        return await self.async_step_discovery()

    async def async_step_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Broadcast-discover devices and let user select."""
        errors: dict[str, str] = {}

        if user_input is None:
            try:
                self._discovered = await discover_devices()
            except Exception as err:
                _LOGGER.warning("Discovery failed: %s", err)
                self._discovered = []

            if not self._discovered:
                return await self.async_step_manual()

            device_options = [
                selector.SelectOptionDict(
                    value=d["ble_mac"],
                    label=f"{d.get('device', 'Unknown')} – {d.get('ip', '?')} (MAC: {d['ble_mac']})",
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
                description_placeholders={"count": str(len(self._discovered))},
            )

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
            schema_fields[vol.Optional(f"name_{mac}", default=default_name)] = str

        return self.async_show_form(
            step_id="name_devices",
            data_schema=vol.Schema(schema_fields),
        )

    async def async_step_options_initial(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Optional entity configuration."""
        if user_input is not None:
            devices_data = [
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
                for dev in self._selected_devices
            ]

            unique_id = " ".join(sorted(d[CONF_BLE_MAC] for d in devices_data))
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
                options=_clean_options(user_input, DEFAULT_SCAN_INTERVAL),
            )

        return self.async_show_form(
            step_id="options_initial",
            data_schema=_options_schema({}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "MarstekOptionsFlow":
        return MarstekOptionsFlow(config_entry)


# ─────────────────────────────────────────────────────────────────────────────
# Options flow
# ─────────────────────────────────────────────────────────────────────────────

class MarstekOptionsFlow(OptionsFlow):
    """Handle options flow."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        options = self.config_entry.options

        if user_input is not None:
            return self.async_create_entry(
                title="",
                data=_clean_options(
                    user_input,
                    options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ),
            )

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(options),
        )
