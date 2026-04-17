"""Marstek Local API V2 – config flow."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .api import discover_devices, validate_connection
from .const import (
    CONF_BLE_MAC,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_DOD,
    CONF_ELECTRICITY_PRICE_ENTITY,
    CONF_ENERGY_TAX,
    CONF_FIRMWARE,
    CONF_GRID_POWER_ENTITY,
    CONF_MARKET_PRICE_ENTITY,
    CONF_MAX_CHARGE_WATTS,
    CONF_MAX_DISCHARGE_WATTS,
    CONF_MIN_SPREAD,
    CONF_PLAN_HOURS,
    CONF_PORT,
    CONF_PROCUREMENT_FEE,
    CONF_WIFI_MAC,
    DEFAULT_DOD,
    DEFAULT_ENERGY_TAX,
    DEFAULT_MAX_CHARGE_WATTS,
    DEFAULT_MAX_DISCHARGE_WATTS,
    DEFAULT_MIN_SPREAD,
    DEFAULT_PLAN_HOURS,
    DEFAULT_PORT,
    DEFAULT_PROCUREMENT_FEE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

CONF_DEVICES = "devices"


# ─────────────────────────────────────────────────────────────────────────────
# Schema builder
# ─────────────────────────────────────────────────────────────────────────────

def _options_schema(current: dict[str, Any]) -> vol.Schema:
    """Build the options schema with selectors and current/default values."""

    def _sv(key: str, fallback: Any = None) -> dict:
        val = current.get(key, fallback)
        return {"suggested_value": val} if val is not None else {}

    return vol.Schema(
        {
            # ── Polling ─────────────────────────────────────────────────────
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),

            # ── Dynamic entity references ────────────────────────────────────
            vol.Optional(
                CONF_ELECTRICITY_PRICE_ENTITY,
                description=_sv(CONF_ELECTRICITY_PRICE_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig()),

            vol.Optional(
                CONF_GRID_POWER_ENTITY,
                description=_sv(CONF_GRID_POWER_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig()),

            vol.Optional(
                CONF_MARKET_PRICE_ENTITY,
                description=_sv(CONF_MARKET_PRICE_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig()),

            # ── Direct numeric plan values ───────────────────────────────────
            vol.Optional(
                CONF_ENERGY_TAX,
                default=current.get(CONF_ENERGY_TAX, DEFAULT_ENERGY_TAX),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=0.5, step=0.001,
                    unit_of_measurement="€/kWh", mode="box",
                )
            ),

            vol.Optional(
                CONF_PROCUREMENT_FEE,
                default=current.get(CONF_PROCUREMENT_FEE, DEFAULT_PROCUREMENT_FEE),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=0.10, step=0.001,
                    unit_of_measurement="€/kWh", mode="box",
                )
            ),

            vol.Optional(
                CONF_PLAN_HOURS,
                default=current.get(CONF_PLAN_HOURS, DEFAULT_PLAN_HOURS),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=12, step=1,
                    unit_of_measurement="uur", mode="slider",
                )
            ),

            vol.Optional(
                CONF_MIN_SPREAD,
                default=current.get(CONF_MIN_SPREAD, DEFAULT_MIN_SPREAD),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=0.5, step=0.001,
                    unit_of_measurement="€/kWh", mode="box",
                )
            ),

            # ── Battery power limits ─────────────────────────────────────────
            vol.Optional(
                CONF_MAX_CHARGE_WATTS,
                default=current.get(CONF_MAX_CHARGE_WATTS, DEFAULT_MAX_CHARGE_WATTS),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=100, max=2500, step=50,
                    unit_of_measurement="W per batterij", mode="slider",
                )
            ),

            vol.Optional(
                CONF_MAX_DISCHARGE_WATTS,
                default=current.get(CONF_MAX_DISCHARGE_WATTS, DEFAULT_MAX_DISCHARGE_WATTS),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=100, max=2500, step=50,
                    unit_of_measurement="W per batterij", mode="slider",
                )
            ),
        }
    )


def _clean_options(user_input: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Normalise and merge options – coerce numeric types."""
    return {
        CONF_SCAN_INTERVAL: int(user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
        CONF_ELECTRICITY_PRICE_ENTITY: user_input.get(CONF_ELECTRICITY_PRICE_ENTITY) or "",
        CONF_GRID_POWER_ENTITY: user_input.get(CONF_GRID_POWER_ENTITY) or "",
        CONF_MARKET_PRICE_ENTITY: user_input.get(CONF_MARKET_PRICE_ENTITY) or "",
        CONF_ENERGY_TAX: float(user_input.get(CONF_ENERGY_TAX, DEFAULT_ENERGY_TAX)),
        CONF_PROCUREMENT_FEE: float(user_input.get(CONF_PROCUREMENT_FEE, DEFAULT_PROCUREMENT_FEE)),
        CONF_PLAN_HOURS: int(user_input.get(CONF_PLAN_HOURS, DEFAULT_PLAN_HOURS)),
        CONF_MIN_SPREAD: float(user_input.get(CONF_MIN_SPREAD, DEFAULT_MIN_SPREAD)),
        CONF_MAX_CHARGE_WATTS: int(user_input.get(CONF_MAX_CHARGE_WATTS, DEFAULT_MAX_CHARGE_WATTS)),
        CONF_MAX_DISCHARGE_WATTS: int(user_input.get(CONF_MAX_DISCHARGE_WATTS, DEFAULT_MAX_DISCHARGE_WATTS)),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Config flow
# ─────────────────────────────────────────────────────────────────────────────

class MarstekConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: list[dict[str, Any]] = []
        self._selected_devices: list[dict[str, Any]] = []

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        return await self.async_step_discovery()

    async def async_step_dhcp(self, discovery_info: Any) -> FlowResult:
        return await self.async_step_discovery()

    async def async_step_discovery(self, user_input: dict | None = None) -> FlowResult:
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
                            selector.SelectSelectorConfig(options=device_options, multiple=True)
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

    async def async_step_manual(self, user_input: dict | None = None) -> FlowResult:
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

    async def async_step_name_devices(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            try:
                for dev in self._selected_devices:
                    mac_key = dev["ble_mac"].replace(":", "_")
                    dev["device_name"] = user_input.get(f"name_{mac_key}", "")
                return await self.async_step_options_initial()
            except Exception:
                _LOGGER.exception("Error in async_step_name_devices (submit)")
                raise

        try:
            schema_fields: dict[Any, Any] = {}
            for dev in self._selected_devices:
                mac = dev["ble_mac"]
                mac_key = mac.replace(":", "_")
                schema_fields[vol.Optional(f"name_{mac_key}", default=f"Marstek {mac.replace(':', '')[-4:].upper()}")] = str

            return self.async_show_form(
                step_id="name_devices",
                data_schema=vol.Schema(schema_fields),
            )
        except Exception:
            _LOGGER.exception("Error in async_step_name_devices (show)")
            raise

    def _existing_options(self) -> dict:
        """Return options from the first existing config entry, if any."""
        existing = self.hass.config_entries.async_entries(DOMAIN)
        if existing:
            return dict(existing[0].options)
        return {}

    async def async_step_options_initial(self, user_input: dict | None = None) -> FlowResult:
        # Pre-fill options from an existing entry so the user doesn't have to
        # retype everything when adding a second or third battery.
        prefill = self._existing_options()
        is_first_battery = not bool(prefill)

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
                options=_clean_options(user_input, prefill),
            )

        if not is_first_battery:
            # Subsequent battery: show the form pre-filled so user just confirms
            description = "Instellingen zijn overgenomen van uw bestaande Marstek configuratie. Controleer en klik Verzenden om te bevestigen."
        else:
            description = None

        return self.async_show_form(
            step_id="options_initial",
            data_schema=_options_schema(prefill),
            description_placeholders={"prefill_note": description or ""},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "MarstekOptionsFlow":
        return MarstekOptionsFlow(config_entry)


# ─────────────────────────────────────────────────────────────────────────────
# Options flow
# ─────────────────────────────────────────────────────────────────────────────

class MarstekOptionsFlow(OptionsFlow):
    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        options = self.config_entry.options
        if user_input is not None:
            return self.async_create_entry(title="", data=_clean_options(user_input, options))
        return self.async_show_form(step_id="init", data_schema=_options_schema(options))
