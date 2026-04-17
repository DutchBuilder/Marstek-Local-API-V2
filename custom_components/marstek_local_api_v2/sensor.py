"""Marstek Local API V2 – sensor entities.

Sensor hierarchy:
  A) Per-device API sensors      (direct from Rev2 API data)
  B) Per-device computed sensors (derived, cost/power/available-kWh)
  C) Global aggregate sensors    (fleet-wide totals / shares / plan)
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CATEGORY_FAST,
    CATEGORY_MEDIUM,
    CATEGORY_SLOW,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_DOD,
    CONF_ELECTRICITY_PRICE_ENTITY,
    CONF_ENERGY_TAX,
    CONF_GRID_POWER_ENTITY,
    CONF_MARKET_PRICE_ENTITY,
    CONF_MAX_CHARGE_WATTS,
    CONF_MAX_DISCHARGE_WATTS,
    CONF_MIN_SPREAD,
    CONF_PLAN_HOURS,
    CONF_PROCUREMENT_FEE,
    DEFAULT_DOD,
    DEFAULT_ENERGY_TAX,
    DEFAULT_MAX_CHARGE_WATTS,
    DEFAULT_MAX_DISCHARGE_WATTS,
    DEFAULT_MIN_SPREAD,
    DEFAULT_PLAN_HOURS,
    DEFAULT_PROCUREMENT_FEE,
    DOMAIN,
    MODELS_WITH_PV,
    PLAN_SENSORS_ENTRY_KEY,
)
from .coordinator import MarstekDataUpdateCoordinator, MarstekMultiDeviceCoordinator
from .plan_utils import compute_plan, is_current_hour_in_slots

_LOGGER = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Entity description dataclass
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class MarstekSensorDescription(SensorEntityDescription):
    """Extended entity description with value/availability extractors."""

    value_fn: Callable[[dict], Any] | None = None
    available_fn: Callable[[dict, "MarstekDataUpdateCoordinator"], bool] | None = None
    category: str = CATEGORY_FAST


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────


def _wh_to_kwh(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v) / 1000, 3)
    except (TypeError, ValueError):
        return None


def _float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Per-device API sensor descriptions
# ─────────────────────────────────────────────────────────────────────────────

BATTERY_SENSORS: list[MarstekSensorDescription] = [
    # ── Bat.GetStatus ───────────────────────────────────────────────────────
    MarstekSensorDescription(
        key="bat_soc",
        name="Battery SOC",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _float_or_none(d.get("bat", {}).get("soc")),
        category=CATEGORY_FAST,
    ),
    MarstekSensorDescription(
        key="bat_temp",
        name="Battery Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _float_or_none(d.get("bat", {}).get("bat_temp")),
        category=CATEGORY_FAST,
    ),
    MarstekSensorDescription(
        key="bat_remaining_capacity",
        name="Remaining Capacity",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: _wh_to_kwh(d.get("bat", {}).get("bat_capacity")),
        category=CATEGORY_FAST,
    ),
    MarstekSensorDescription(
        key="bat_rated_capacity",
        name="Rated Capacity",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: _wh_to_kwh(d.get("bat", {}).get("rated_capacity")),
        category=CATEGORY_FAST,
    ),
    # ── ES.GetStatus ─────────────────────────────────────────────────────────
    MarstekSensorDescription(
        key="pv_power",
        name="Solar Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _float_or_none(d.get("es", {}).get("pv_power")),
        category=CATEGORY_FAST,
    ),
    MarstekSensorDescription(
        key="ongrid_power",
        name="Grid Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _float_or_none(d.get("es", {}).get("ongrid_power")),
        category=CATEGORY_FAST,
    ),
    MarstekSensorDescription(
        key="offgrid_power",
        name="Off-Grid Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _float_or_none(d.get("es", {}).get("offgrid_power")),
        category=CATEGORY_FAST,
    ),
    MarstekSensorDescription(
        key="bat_power",
        name="Battery Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _float_or_none(d.get("es", {}).get("bat_power")),
        category=CATEGORY_FAST,
    ),
    MarstekSensorDescription(
        key="total_pv_energy",
        name="Total PV Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda d: _wh_to_kwh(d.get("es", {}).get("total_pv_energy")),
        category=CATEGORY_FAST,
    ),
    MarstekSensorDescription(
        key="total_grid_output_energy",
        name="Total Grid Export Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda d: _wh_to_kwh(d.get("es", {}).get("total_grid_output_energy")),
        category=CATEGORY_FAST,
    ),
    MarstekSensorDescription(
        key="total_grid_input_energy",
        name="Total Grid Import Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda d: _wh_to_kwh(d.get("es", {}).get("total_grid_input_energy")),
        category=CATEGORY_FAST,
    ),
    MarstekSensorDescription(
        key="total_load_energy",
        name="Total Load Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda d: _wh_to_kwh(d.get("es", {}).get("total_load_energy")),
        category=CATEGORY_FAST,
    ),
    # ── ES.GetMode ───────────────────────────────────────────────────────────
    MarstekSensorDescription(
        key="es_mode",
        name="Operating Mode",
        icon="mdi:cog",
        value_fn=lambda d: d.get("mode", {}).get("mode"),
        category=CATEGORY_FAST,
    ),
    MarstekSensorDescription(
        key="ct_a_power",
        name="CT Phase A Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _float_or_none(d.get("mode", {}).get("a_power")),
        category=CATEGORY_FAST,
    ),
    MarstekSensorDescription(
        key="ct_b_power",
        name="CT Phase B Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _float_or_none(d.get("mode", {}).get("b_power")),
        category=CATEGORY_FAST,
    ),
    MarstekSensorDescription(
        key="ct_c_power",
        name="CT Phase C Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _float_or_none(d.get("mode", {}).get("c_power")),
        category=CATEGORY_FAST,
    ),
    MarstekSensorDescription(
        key="ct_total_power",
        name="CT Total Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _float_or_none(d.get("mode", {}).get("total_power")),
        category=CATEGORY_FAST,
    ),
    MarstekSensorDescription(
        key="ct_input_energy",
        name="CT Cumulative Input Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        # API returns Wh * 0.1, so raw / 10 → Wh, then /1000 → kWh
        value_fn=lambda d: round(float(d.get("mode", {}).get("input_energy", 0) or 0) / 10 / 1000, 3)
            if d.get("mode", {}).get("input_energy") is not None else None,
        category=CATEGORY_FAST,
    ),
    MarstekSensorDescription(
        key="ct_output_energy",
        name="CT Cumulative Output Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda d: round(float(d.get("mode", {}).get("output_energy", 0) or 0) / 10 / 1000, 3)
            if d.get("mode", {}).get("output_energy") is not None else None,
        category=CATEGORY_FAST,
    ),
    # ── EM.GetStatus ─────────────────────────────────────────────────────────
    MarstekSensorDescription(
        key="em_a_power",
        name="EM Phase A Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=0,
        value_fn=lambda d: _float_or_none(d.get("em", {}).get("a_power")),
        category=CATEGORY_MEDIUM,
    ),
    MarstekSensorDescription(
        key="em_b_power",
        name="EM Phase B Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=0,
        value_fn=lambda d: _float_or_none(d.get("em", {}).get("b_power")),
        category=CATEGORY_MEDIUM,
    ),
    MarstekSensorDescription(
        key="em_c_power",
        name="EM Phase C Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=0,
        value_fn=lambda d: _float_or_none(d.get("em", {}).get("c_power")),
        category=CATEGORY_MEDIUM,
    ),
    MarstekSensorDescription(
        key="em_total_power",
        name="EM Total Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=0,
        value_fn=lambda d: _float_or_none(d.get("em", {}).get("total_power")),
        category=CATEGORY_MEDIUM,
    ),
    MarstekSensorDescription(
        key="em_input_energy",
        name="EM Cumulative Input Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
        suggested_display_precision=3,
        value_fn=lambda d: round(float(d.get("em", {}).get("input_energy", 0) or 0) / 10 / 1000, 3)
            if d.get("em", {}).get("input_energy") is not None else None,
        category=CATEGORY_MEDIUM,
    ),
    MarstekSensorDescription(
        key="em_output_energy",
        name="EM Cumulative Output Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
        suggested_display_precision=3,
        value_fn=lambda d: round(float(d.get("em", {}).get("output_energy", 0) or 0) / 10 / 1000, 3)
            if d.get("em", {}).get("output_energy") is not None else None,
        category=CATEGORY_MEDIUM,
    ),
    # ── WiFi (diagnostic) ────────────────────────────────────────────────────
    MarstekSensorDescription(
        key="wifi_rssi",
        name="WiFi Signal Strength",
        native_unit_of_measurement="dBm",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        entity_category="diagnostic",
        suggested_display_precision=0,
        value_fn=lambda d: _float_or_none(d.get("wifi", {}).get("rssi")),
        category=CATEGORY_SLOW,
    ),
    MarstekSensorDescription(
        key="wifi_ip",
        name="WiFi IP Address",
        icon="mdi:ip-network",
        entity_registry_enabled_default=False,
        entity_category="diagnostic",
        value_fn=lambda d: d.get("wifi", {}).get("sta_ip"),
        category=CATEGORY_SLOW,
    ),
    MarstekSensorDescription(
        key="firmware_version",
        name="Firmware Version",
        icon="mdi:chip",
        entity_registry_enabled_default=False,
        entity_category="diagnostic",
        value_fn=lambda d: d.get("device", {}).get("ver"),
        category=CATEGORY_SLOW,
    ),
]

# PV sensors for Venus D/A models
PV_SENSORS: list[MarstekSensorDescription] = []
for _pv_idx in range(1, 5):
    PV_SENSORS.extend(
        [
            MarstekSensorDescription(
                key=f"pv{_pv_idx}_power",
                name=f"PV{_pv_idx} Power",
                native_unit_of_measurement=UnitOfPower.WATT,
                device_class=SensorDeviceClass.POWER,
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=0,
                value_fn=lambda d, i=_pv_idx: _float_or_none(
                    d.get("pv", {}).get(f"pv{i}_power")
                ),
                category=CATEGORY_MEDIUM,
            ),
            MarstekSensorDescription(
                key=f"pv{_pv_idx}_voltage",
                name=f"PV{_pv_idx} Voltage",
                native_unit_of_measurement="V",
                device_class=SensorDeviceClass.VOLTAGE,
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=1,
                entity_registry_enabled_default=False,
                value_fn=lambda d, i=_pv_idx: _float_or_none(
                    d.get("pv", {}).get(f"pv{i}_voltage")
                ),
                category=CATEGORY_MEDIUM,
            ),
            MarstekSensorDescription(
                key=f"pv{_pv_idx}_current",
                name=f"PV{_pv_idx} Current",
                native_unit_of_measurement="A",
                device_class=SensorDeviceClass.CURRENT,
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=2,
                entity_registry_enabled_default=False,
                value_fn=lambda d, i=_pv_idx: _float_or_none(
                    d.get("pv", {}).get(f"pv{i}_current")
                ),
                category=CATEGORY_MEDIUM,
            ),
        ]
    )


# ─────────────────────────────────────────────────────────────────────────────
# Base sensor class (per device)
# ─────────────────────────────────────────────────────────────────────────────


class MarstekSensor(CoordinatorEntity[MarstekDataUpdateCoordinator], SensorEntity):
    """Sensor that reads from a single-device coordinator."""

    _attr_has_entity_name = True
    entity_description: MarstekSensorDescription

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        description: MarstekSensorDescription,
        device_info: DeviceInfo,
        unique_id_prefix: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_device_info = device_info
        self._attr_unique_id = f"{unique_id_prefix}_{description.key}"

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        if self.coordinator.is_stale(self.entity_description.category):
            return None
        try:
            return self.entity_description.value_fn(self.coordinator.data)
        except Exception:
            return None

    @property
    def available(self) -> bool:
        if not self.coordinator.last_update_success:
            return False
        if self.coordinator.is_stale(self.entity_description.category):
            return False
        if self.entity_description.available_fn is not None:
            try:
                return self.entity_description.available_fn(
                    self.coordinator.data, self.coordinator
                )
            except Exception:
                return False
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Computed / derived sensors (per device)
# ─────────────────────────────────────────────────────────────────────────────


class BatterijverbruikGridPowerSensor(
    CoordinatorEntity[MarstekDataUpdateCoordinator], SensorEntity
):
    """
    Batterijverbruik Grid Power = -1 * ongrid_power.
    Positive when battery is charging FROM the grid.
    Negative when battery is exporting TO the grid.
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:battery-charging-100"

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: DeviceInfo,
        unique_id_prefix: str,
        suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{unique_id_prefix}_batterijverbruik_grid_power"
        self._attr_name = "Batterijverbruik Grid Power"

    @property
    def native_value(self) -> float | None:
        es = (self.coordinator.data or {}).get("es", {})
        v = es.get("ongrid_power")
        if v is None:
            return None
        return round(float(v) * -1, 0)


class BeschikbareKwhSensor(
    CoordinatorEntity[MarstekDataUpdateCoordinator], SensorEntity
):
    """
    Beschikbare kWh = max(0, bat_capacity_kWh - reserve_kWh).
    reserve = rated_capacity_kWh * (1 - DOD/100).
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:battery-arrow-up"

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: DeviceInfo,
        unique_id_prefix: str,
        suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{unique_id_prefix}_beschikbare_kwh"
        self._attr_name = "Beschikbare kWh"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        bat = data.get("bat", {})
        remaining_wh = bat.get("bat_capacity")
        rated_wh = bat.get("rated_capacity")
        if remaining_wh is None:
            return None
        dod = data.get("dod", DEFAULT_DOD)
        remaining_kwh = float(remaining_wh) / 1000
        rated_kwh = float(rated_wh) / 1000 if rated_wh else 0.0
        reserve_kwh = rated_kwh * (1 - dod / 100)
        return round(max(0.0, remaining_kwh - reserve_kwh), 3)


class BatteryChargePowerSensor(
    CoordinatorEntity[MarstekDataUpdateCoordinator], SensorEntity
):
    """HA Battery Charge Power = max(0, -ongrid_power)."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:battery-plus"

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: DeviceInfo,
        unique_id_prefix: str,
        suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{unique_id_prefix}_ha_battery_charge_power"
        self._attr_name = "HA Battery Charge Power"

    @property
    def native_value(self) -> float | None:
        es = (self.coordinator.data or {}).get("es", {})
        v = es.get("ongrid_power")
        if v is None:
            return None
        return round(max(0.0, -float(v)), 0)


class BatteryDischargePowerSensor(
    CoordinatorEntity[MarstekDataUpdateCoordinator], SensorEntity
):
    """HA Battery Discharge Power = max(0, ongrid_power)."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:battery-minus"

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: DeviceInfo,
        unique_id_prefix: str,
        suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{unique_id_prefix}_ha_battery_discharge_power"
        self._attr_name = "HA Battery Discharge Power"

    @property
    def native_value(self) -> float | None:
        es = (self.coordinator.data or {}).get("es", {})
        v = es.get("ongrid_power")
        if v is None:
            return None
        return round(max(0.0, float(v)), 0)


class KostenrateSensor(
    CoordinatorEntity[MarstekDataUpdateCoordinator], SensorEntity
):
    """
    Batterijverbruik Kostenrate (€/h).
    = (batterijverbruik_grid_power_W / 1000) * stroomprijs_eur_per_kwh  if > 0 else 0
    Only positive when charging FROM grid (cost).
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "EUR/h"
    _attr_icon = "mdi:currency-eur"
    _attr_suggested_display_precision = 4

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: DeviceInfo,
        unique_id_prefix: str,
        suffix: str,
        hass: HomeAssistant,
        price_entity: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{unique_id_prefix}_batterijverbruik_kostenrate"
        self._attr_name = "Batterijverbruik Kostenrate"
        self._hass = hass
        self._price_entity = price_entity

    @property
    def available(self) -> bool:
        return bool(self._price_entity) and super().available

    @property
    def native_value(self) -> float | None:
        if not self._price_entity:
            return None
        es = (self.coordinator.data or {}).get("es", {})
        ongrid = es.get("ongrid_power")
        if ongrid is None:
            return None
        grid_power_inverted = -float(ongrid)  # positive = charging
        if grid_power_inverted <= 0:
            return 0.0
        price_state = self._hass.states.get(self._price_entity)
        if price_state is None or price_state.state in ("unknown", "unavailable"):
            return None
        try:
            price = float(price_state.state)
        except (TypeError, ValueError):
            return None
        return round((grid_power_inverted / 1000) * price, 4)


class KostenrateNetSensor(
    CoordinatorEntity[MarstekDataUpdateCoordinator], SensorEntity
):
    """
    Batterijverbruik Kostenrate Net (€/h) – only the grid-sourced portion.
    = kostenrate * (grid_share_% / 100)
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "EUR/h"
    _attr_icon = "mdi:currency-eur"
    _attr_suggested_display_precision = 4

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: DeviceInfo,
        unique_id_prefix: str,
        suffix: str,
        hass: HomeAssistant,
        price_entity: str | None,
        grid_share_sensor_uid: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{unique_id_prefix}_batterijverbruik_kostenrate_net"
        self._attr_name = "Batterijverbruik Kostenrate Net"
        self._hass = hass
        self._price_entity = price_entity
        self._grid_share_uid = grid_share_sensor_uid

    @property
    def available(self) -> bool:
        return bool(self._price_entity) and super().available

    @property
    def native_value(self) -> float | None:
        if not self._price_entity:
            return None
        es = (self.coordinator.data or {}).get("es", {})
        ongrid = es.get("ongrid_power")
        if ongrid is None:
            return None
        grid_power_inverted = -float(ongrid)
        if grid_power_inverted <= 0:
            return 0.0
        price_state = self._hass.states.get(self._price_entity)
        if price_state is None or price_state.state in ("unknown", "unavailable"):
            return None
        try:
            price = float(price_state.state)
        except (TypeError, ValueError):
            return None
        rate = (grid_power_inverted / 1000) * price
        # Try to get grid share from HA state registry
        grid_share_state = self._hass.states.get(self._grid_share_uid)
        share = 100.0
        if grid_share_state and grid_share_state.state not in ("unknown", "unavailable"):
            try:
                share = max(0.0, min(100.0, float(grid_share_state.state)))
            except (TypeError, ValueError):
                pass
        return round(rate * share / 100, 4)


class OpbrengstrateSensor(
    CoordinatorEntity[MarstekDataUpdateCoordinator], SensorEntity
):
    """
    Batterijverbruik Opbrengstrate (€/h).
    = (ongrid_power_W / 1000) * stroomprijs  if ongrid_power > 0 else 0
    Only positive when discharging TO grid (revenue).
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "EUR/h"
    _attr_icon = "mdi:currency-eur"
    _attr_suggested_display_precision = 4

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: DeviceInfo,
        unique_id_prefix: str,
        suffix: str,
        hass: HomeAssistant,
        price_entity: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{unique_id_prefix}_batterijverbruik_opbrengstrate"
        self._attr_name = "Batterijverbruik Opbrengstrate"
        self._hass = hass
        self._price_entity = price_entity

    @property
    def available(self) -> bool:
        return bool(self._price_entity) and super().available

    @property
    def native_value(self) -> float | None:
        if not self._price_entity:
            return None
        es = (self.coordinator.data or {}).get("es", {})
        ongrid = es.get("ongrid_power")
        if ongrid is None:
            return None
        if float(ongrid) <= 0:
            return 0.0
        price_state = self._hass.states.get(self._price_entity)
        if price_state is None or price_state.state in ("unknown", "unavailable"):
            return None
        try:
            price = float(price_state.state)
        except (TypeError, ValueError):
            return None
        return round((float(ongrid) / 1000) * price, 4)


class AccumulatedCostSensor(
    CoordinatorEntity[MarstekDataUpdateCoordinator], RestoreSensor
):
    """
    Batterijverbruik Kosten – running total (Riemann sum of kostenrate).
    Persists across HA restarts.
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "EUR"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:cash-minus"
    _attr_suggested_display_precision = 4

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: DeviceInfo,
        unique_id_prefix: str,
        suffix: str,
        hass: HomeAssistant,
        price_entity: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{unique_id_prefix}_batterijverbruik_kosten"
        self._attr_name = "Batterijverbruik Kosten"
        self._hass = hass
        self._price_entity = price_entity
        self._total: float = 0.0
        self._last_ts: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Restore previous total
        last_state = await self.async_get_last_sensor_data()
        if last_state and last_state.native_value is not None:
            try:
                self._total = float(last_state.native_value)
            except (TypeError, ValueError):
                pass
        self._last_ts = time.monotonic()

    @callback
    def _handle_coordinator_update(self) -> None:
        now = time.monotonic()
        if self._last_ts is not None and self._price_entity:
            elapsed_h = (now - self._last_ts) / 3600
            es = (self.coordinator.data or {}).get("es", {})
            ongrid = es.get("ongrid_power")
            if ongrid is not None:
                grid_power_inverted = -float(ongrid)
                if grid_power_inverted > 0:
                    price_state = self._hass.states.get(self._price_entity)
                    if price_state and price_state.state not in ("unknown", "unavailable"):
                        try:
                            price = float(price_state.state)
                            rate = (grid_power_inverted / 1000) * price
                            self._total += rate * elapsed_h
                        except (TypeError, ValueError):
                            pass
        self._last_ts = now
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float:
        return round(self._total, 4)

    @property
    def available(self) -> bool:
        return bool(self._price_entity) and super().available


class AccumulatedRevenueSensor(
    CoordinatorEntity[MarstekDataUpdateCoordinator], RestoreSensor
):
    """
    Batterijverbruik Opbrengst – running total (Riemann sum of opbrengstrate).
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "EUR"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:cash-plus"
    _attr_suggested_display_precision = 4

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: DeviceInfo,
        unique_id_prefix: str,
        suffix: str,
        hass: HomeAssistant,
        price_entity: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{unique_id_prefix}_batterijverbruik_opbrengst"
        self._attr_name = "Batterijverbruik Opbrengst"
        self._hass = hass
        self._price_entity = price_entity
        self._total: float = 0.0
        self._last_ts: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_sensor_data()
        if last_state and last_state.native_value is not None:
            try:
                self._total = float(last_state.native_value)
            except (TypeError, ValueError):
                pass
        self._last_ts = time.monotonic()

    @callback
    def _handle_coordinator_update(self) -> None:
        now = time.monotonic()
        if self._last_ts is not None and self._price_entity:
            elapsed_h = (now - self._last_ts) / 3600
            es = (self.coordinator.data or {}).get("es", {})
            ongrid = es.get("ongrid_power")
            if ongrid is not None and float(ongrid) > 0:
                price_state = self._hass.states.get(self._price_entity)
                if price_state and price_state.state not in ("unknown", "unavailable"):
                    try:
                        price = float(price_state.state)
                        rate = (float(ongrid) / 1000) * price
                        self._total += rate * elapsed_h
                    except (TypeError, ValueError):
                        pass
        self._last_ts = now
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float:
        return round(self._total, 4)

    @property
    def available(self) -> bool:
        return bool(self._price_entity) and super().available


# ─────────────────────────────────────────────────────────────────────────────
# Global / fleet-wide sensors (use MultiDeviceCoordinator)
# ─────────────────────────────────────────────────────────────────────────────


class MarstekFleetSensor(
    CoordinatorEntity[MarstekMultiDeviceCoordinator], SensorEntity
):
    """Base class for fleet-wide aggregate sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MarstekMultiDeviceCoordinator,
        key: str,
        name: str,
        entry_id: str,
        device_info: DeviceInfo,
        **kwargs: Any,
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry_id}_fleet_{key}"
        self._attr_device_info = device_info
        for k, v in kwargs.items():
            setattr(self, f"_attr_{k}", v)

    @property
    def native_value(self) -> Any:
        return self.coordinator.get_aggregates().get(self._key)


class TotalRatedCapacitySensor(MarstekFleetSensor):
    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3

    @property
    def native_value(self) -> float | None:
        v = self.coordinator.get_aggregates().get("total_rated_capacity_wh")
        return _wh_to_kwh(v)


class TotalRemainingCapacitySensor(MarstekFleetSensor):
    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3

    @property
    def native_value(self) -> float | None:
        v = self.coordinator.get_aggregates().get("total_remaining_capacity_wh")
        return _wh_to_kwh(v)


class HaBatteryChargePowerTotalSensor(MarstekFleetSensor):
    """Total fleet battery charge power (W) – sum of max(0, -ongrid) per device."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:battery-plus"

    @property
    def native_value(self) -> float | None:
        v = self.coordinator.get_aggregates().get("total_charge_power")
        return round(float(v), 0) if v is not None else None


class HaBatteryDischargePowerTotalSensor(MarstekFleetSensor):
    """Total fleet battery discharge power (W) – sum of max(0, ongrid) per device."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:battery-minus"

    @property
    def native_value(self) -> float | None:
        v = self.coordinator.get_aggregates().get("total_discharge_power")
        return round(float(v), 0) if v is not None else None


class HaBatteryPowerTotalSignedSensor(MarstekFleetSensor):
    """
    Total fleet battery power, signed.
    Positive = net discharging to grid, negative = net charging from grid.
    """

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:battery-charging"

    @property
    def native_value(self) -> float | None:
        agg = self.coordinator.get_aggregates()
        discharge = agg.get("total_discharge_power")
        charge = agg.get("total_charge_power")
        if discharge is None or charge is None:
            return None
        return round(float(discharge) - float(charge), 0)


class TotalBeschikbareKwhSensor(
    CoordinatorEntity[MarstekMultiDeviceCoordinator], SensorEntity
):
    """Sum of beschikbare kWh across all devices."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:battery-arrow-up"

    def __init__(
        self,
        multi_coordinator: MarstekMultiDeviceCoordinator,
        device_coordinators: dict[str, MarstekDataUpdateCoordinator],
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(multi_coordinator)
        self._device_coordinators = device_coordinators
        self._attr_unique_id = f"{entry_id}_fleet_beschikbare_kwh_totaal"
        self._attr_name = "Beschikbare kWh Totaal Marstek"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> float | None:
        total = 0.0
        for mac, coord in self._device_coordinators.items():
            data = coord.data or {}
            bat = data.get("bat", {})
            remaining_wh = bat.get("bat_capacity")
            rated_wh = bat.get("rated_capacity")
            if remaining_wh is None:
                continue
            dod = data.get("dod", DEFAULT_DOD)
            remaining_kwh = float(remaining_wh) / 1000
            rated_kwh = float(rated_wh) / 1000 if rated_wh else 0.0
            reserve_kwh = rated_kwh * (1 - dod / 100)
            total += max(0.0, remaining_kwh - reserve_kwh)
        return round(total, 3)


class HaBatteryChargeSolarShareSensor(
    CoordinatorEntity[MarstekMultiDeviceCoordinator], SensorEntity
):
    """% of battery charge power coming from solar."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:solar-power"
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: MarstekMultiDeviceCoordinator,
        entry_id: str,
        hass: HomeAssistant,
        grid_power_entity: str | None,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_ha_battery_charge_solar_share"
        self._attr_name = "HA Battery Charge Solar Share"
        self._hass = hass
        self._grid_power_entity = grid_power_entity
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return bool(self._grid_power_entity) and super().available

    @property
    def native_value(self) -> float | None:
        if not self._grid_power_entity:
            return None
        agg = self.coordinator.get_aggregates()
        charge = agg.get("total_charge_power", 0.0)
        if charge <= 0:
            return 0.0
        grid_state = self._hass.states.get(self._grid_power_entity)
        if grid_state is None or grid_state.state in ("unknown", "unavailable"):
            return None
        try:
            grid_import = max(0.0, float(grid_state.state))
        except (TypeError, ValueError):
            return None
        solar_to_charge = max(0.0, charge - grid_import)
        return round(solar_to_charge / charge * 100, 0)


class HaBatteryChargeGridShareSensor(
    CoordinatorEntity[MarstekMultiDeviceCoordinator], SensorEntity
):
    """% of battery charge power coming from the grid."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:transmission-tower"
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: MarstekMultiDeviceCoordinator,
        entry_id: str,
        hass: HomeAssistant,
        grid_power_entity: str | None,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_ha_battery_charge_grid_share"
        self._attr_name = "HA Battery Charge Grid Share"
        self._hass = hass
        self._grid_power_entity = grid_power_entity
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return bool(self._grid_power_entity) and super().available

    @property
    def native_value(self) -> float | None:
        if not self._grid_power_entity:
            return None
        agg = self.coordinator.get_aggregates()
        charge = agg.get("total_charge_power", 0.0)
        if charge <= 0:
            return 0.0
        grid_state = self._hass.states.get(self._grid_power_entity)
        if grid_state is None or grid_state.state in ("unknown", "unavailable"):
            return None
        try:
            grid_import = max(0.0, float(grid_state.state))
        except (TypeError, ValueError):
            return None
        grid_to_charge = min(grid_import, charge)
        return round(grid_to_charge / charge * 100, 0)


class BatterijverbruikKostenTotaalSensor(
    CoordinatorEntity[MarstekMultiDeviceCoordinator], RestoreSensor
):
    """Sum of accumulated costs across all devices."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "EUR"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:cash-minus"
    _attr_suggested_display_precision = 4

    def __init__(
        self,
        coordinator: MarstekMultiDeviceCoordinator,
        device_coordinators: dict[str, MarstekDataUpdateCoordinator],
        entry_id: str,
        hass: HomeAssistant,
        price_entity: str | None,
        cost_sensors: dict[str, AccumulatedCostSensor],
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_batterijverbruik_kosten_totaal"
        self._attr_name = "Batterijverbruik Kosten Totaal"
        self._hass = hass
        self._price_entity = price_entity
        self._cost_sensors = cost_sensors
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return bool(self._price_entity) and super().available

    @property
    def native_value(self) -> float | None:
        if not self._cost_sensors:
            return None
        return round(sum(s.native_value or 0.0 for s in self._cost_sensors.values()), 4)


class BatterijverbruikOpbrengstTotaalSensor(
    CoordinatorEntity[MarstekMultiDeviceCoordinator], RestoreSensor
):
    """Sum of accumulated revenue across all devices."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "EUR"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:cash-plus"
    _attr_suggested_display_precision = 4

    def __init__(
        self,
        coordinator: MarstekMultiDeviceCoordinator,
        device_coordinators: dict[str, MarstekDataUpdateCoordinator],
        entry_id: str,
        hass: HomeAssistant,
        price_entity: str | None,
        revenue_sensors: dict[str, AccumulatedRevenueSensor],
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_batterijverbruik_opbrengst_totaal"
        self._attr_name = "Batterijverbruik Opbrengst Totaal"
        self._hass = hass
        self._price_entity = price_entity
        self._revenue_sensors = revenue_sensors
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return bool(self._price_entity) and super().available

    @property
    def native_value(self) -> float | None:
        if not self._revenue_sensors:
            return None
        return round(sum(s.native_value or 0.0 for s in self._revenue_sensors.values()), 4)


class StroomPrijsTotaalSensor(
    CoordinatorEntity[MarstekMultiDeviceCoordinator], SensorEntity
):
    """
    Current electricity price (all-in: spot + energy tax + procurement fee * VAT).
    Uses the configured market price sensor (with prices attribute) to compute
    a weighted average for the current hour, exactly like the yaml template.
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "EUR/kWh"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:lightning-bolt"
    _attr_suggested_display_precision = 4

    def __init__(
        self,
        coordinator: MarstekMultiDeviceCoordinator,
        entry_id: str,
        hass: HomeAssistant,
        market_price_entity: str | None,
        energy_tax: float,
        procurement_fee: float,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_stroomprijs_totaal"
        self._attr_name = "Stroomprijs Totaal"
        self._hass = hass
        self._market_entity = market_price_entity
        self._energy_tax = energy_tax
        self._procurement_fee = procurement_fee
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return bool(self._market_entity)

    @property
    def native_value(self) -> float | None:
        if not self._market_entity:
            return None
        market_state = self._hass.states.get(self._market_entity)
        if market_state is None:
            return None
        prices = market_state.attributes.get("prices", [])
        btw = 1.21
        # Compute weighted average for current hour
        now = datetime.now(timezone.utc)
        hs = now.replace(minute=0, second=0, microsecond=0).timestamp()
        he = hs + 3600
        total_price = 0.0
        total_secs = 0.0
        for item in prices:
            try:
                st = datetime.fromisoformat(item["from"]).timestamp()
                en = datetime.fromisoformat(item["till"]).timestamp()
            except (KeyError, ValueError):
                continue
            os = max(st, hs)
            oe = min(en, he)
            if oe > os:
                dur = oe - os
                total_price += float(item.get("price", 0)) * dur
                total_secs += dur
        if total_secs > 0:
            spot = total_price / total_secs
        else:
            raw = market_state.state
            try:
                spot = float(raw)
            except (TypeError, ValueError):
                return None
        return round((spot + self._energy_tax + self._procurement_fee) * btw, 4)


class MarstekPlanSensor(
    CoordinatorEntity[MarstekMultiDeviceCoordinator], SensorEntity
):
    """
    Marstek plan sensor (vandaag / morgen).
    Computes cheapest charge hours and most expensive discharge hours.
    Replicates the YAML template logic entirely in Python.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-clock"

    def __init__(
        self,
        coordinator: MarstekMultiDeviceCoordinator,
        entry_id: str,
        hass: HomeAssistant,
        is_tomorrow: bool,
        market_price_entity: str | None,
        energy_tax: float,
        procurement_fee: float,
        plan_hours: int,
        min_spread: float,
        max_charge_watts: int,
        max_discharge_watts: int,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        day = "morgen" if is_tomorrow else "vandaag"
        self._attr_unique_id = f"{entry_id}_marstek_plan_{day}"
        self._attr_name = f"Marstek plan {day}"
        self._hass = hass
        self._is_tomorrow = is_tomorrow
        self._market_entity = market_price_entity
        self._energy_tax = energy_tax
        self._procurement_fee = procurement_fee
        self._plan_hours = plan_hours
        self._min_spread = min_spread
        self._max_charge_watts = max_charge_watts
        self._max_discharge_watts = max_discharge_watts
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return bool(self._market_entity)

    def _compute_plan(self) -> dict:
        """Core plan computation. Delegates to shared plan_utils.compute_plan()."""
        devices = self.coordinator.data.get("devices", {}) if self.coordinator.data else {}
        num_batteries = len(devices) if devices else 3
        agg = self.coordinator.get_aggregates()
        total_rated_wh = agg.get("total_rated_capacity_wh") or (num_batteries * 5120)
        capacity_kwh = total_rated_wh / 1000

        return compute_plan(
            self._hass,
            self._is_tomorrow,
            self._market_entity,
            energy_tax=self._energy_tax,
            procurement_fee=self._procurement_fee,
            plan_hours=self._plan_hours,
            min_spread=self._min_spread,
            num_batteries=num_batteries,
            max_charge_watts_per_bat=self._max_charge_watts,
            max_discharge_watts_per_bat=self._max_discharge_watts,
            capacity_kwh=capacity_kwh,
        )

    @property
    def native_value(self) -> str | None:
        plan = self._compute_plan()
        if not plan:
            return "Geen data beschikbaar"
        return f"Plan voor {plan.get('plan_hours', 0)} uur – {plan.get('strategy', '')}"

    @property
    def extra_state_attributes(self) -> dict:
        plan = self._compute_plan()
        if not plan:
            return {}

        def _slot_text(slots: list, prefix: str, w_bat: int, w_total: int) -> str:
            lines = []
            for s in slots:
                lines.append(
                    f"- {prefix} {s['start']}–{s['end']} @ {w_bat}W/bat ({w_total}W) — {s['price']} €/kWh"
                )
            return "\n".join(lines)

        charge_text = _slot_text(
            plan.get("charge_slots", []),
            "LADEN",
            plan.get("charge_watts_per_bat", 800),
            plan.get("charge_watts_per_bat", 800) * 3,
        )
        discharge_text = _slot_text(
            plan.get("discharge_slots", []),
            "ONTLADEN",
            plan.get("discharge_watts_per_bat", 800),
            plan.get("discharge_watts_per_bat", 800) * 3,
        )

        spread_label = "OK" if plan.get("spread_ok") else "NIET gehaald"
        profit_text = (
            f"Strategie: {plan.get('strategy')}\n"
            f"Min spread eis: {plan.get('min_spread')} €/kWh\n"
            f"Spread check: {spread_label} (min {plan.get('min_pair_spread')} €/kWh)\n"
            f"Laden: {plan.get('charge_hours')} uur @ {plan.get('charge_watts_per_bat')}W/bat\n"
            f"Ontladen: {plan.get('discharge_hours')} uur @ {plan.get('discharge_watts_per_bat')}W/bat\n"
            f"Energie laden: {plan.get('charge_kwh')} kWh\n"
            f"Energie ontladen: {plan.get('discharge_kwh')} kWh\n"
            f"Charge kosten: € {plan.get('buy_cost')}\n"
            f"Ontlaad opbrengst: € {plan.get('save_revenue')}\n"
            f"Totale winst/besparing: € {plan.get('profit')}"
        )

        return {
            "strategy": plan.get("strategy"),
            "plan_hours": plan.get("plan_hours"),
            "min_spread": plan.get("min_spread"),
            "profit": plan.get("profit"),
            "charge_slots": plan.get("charge_slots"),
            "discharge_slots": plan.get("discharge_slots"),
            "charge_text": charge_text,
            "discharge_text": discharge_text,
            "profit_text": profit_text,
        }


class MarstekPlanWattSensor(
    CoordinatorEntity[MarstekMultiDeviceCoordinator], SensorEntity
):
    """
    Exposes the charge or discharge wattage setpoint from today's plan.
    Useful for automations that set the battery power via a service call.
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: MarstekMultiDeviceCoordinator,
        entry_id: str,
        hass: HomeAssistant,
        is_charge: bool,
        market_price_entity: str | None,
        energy_tax: float,
        procurement_fee: float,
        plan_hours: int,
        min_spread: float,
        max_charge_watts: int,
        max_discharge_watts: int,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        kind = "laad" if is_charge else "ontlaad"
        self._attr_unique_id = f"{entry_id}_marstek_plan_{kind}vermogen"
        self._attr_name = f"Marstek plan {kind}vermogen"
        self._attr_icon = "mdi:battery-plus" if is_charge else "mdi:battery-minus"
        self._hass = hass
        self._is_charge = is_charge
        self._market_entity = market_price_entity
        self._energy_tax = energy_tax
        self._procurement_fee = procurement_fee
        self._plan_hours = plan_hours
        self._min_spread = min_spread
        self._max_charge_watts = max_charge_watts
        self._max_discharge_watts = max_discharge_watts
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return bool(self._market_entity)

    @property
    def native_value(self) -> float | None:
        if not self._market_entity:
            return None
        devices = self.coordinator.data.get("devices", {}) if self.coordinator.data else {}
        num_batteries = len(devices) if devices else 3
        agg = self.coordinator.get_aggregates()
        total_rated_wh = agg.get("total_rated_capacity_wh") or (num_batteries * 5120)

        plan = compute_plan(
            self._hass, False, self._market_entity,
            energy_tax=self._energy_tax,
            procurement_fee=self._procurement_fee,
            plan_hours=self._plan_hours,
            min_spread=self._min_spread,
            num_batteries=num_batteries,
            max_charge_watts_per_bat=self._max_charge_watts,
            max_discharge_watts_per_bat=self._max_discharge_watts,
            capacity_kwh=total_rated_wh / 1000,
        )
        if not plan:
            return None
        key = "charge_watts_total" if self._is_charge else "discharge_watts_total"
        return float(plan.get(key, 0))


# ─────────────────────────────────────────────────────────────────────────────
# Platform setup
# ─────────────────────────────────────────────────────────────────────────────


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up all sensor entities for this config entry."""
    domain_data = hass.data[DOMAIN][entry.entry_id]
    multi_coordinator: MarstekMultiDeviceCoordinator = domain_data["multi_coordinator"]
    device_coordinators: dict[str, MarstekDataUpdateCoordinator] = domain_data[
        "device_coordinators"
    ]
    devices_info: dict[str, dict] = domain_data["devices_info"]

    options = entry.options
    price_entity = options.get(CONF_ELECTRICITY_PRICE_ENTITY)
    grid_power_entity = options.get(CONF_GRID_POWER_ENTITY)
    market_entity = options.get(CONF_MARKET_PRICE_ENTITY)
    energy_tax = float(options.get(CONF_ENERGY_TAX, DEFAULT_ENERGY_TAX))
    procurement_fee = float(options.get(CONF_PROCUREMENT_FEE, DEFAULT_PROCUREMENT_FEE))
    plan_hours = int(options.get(CONF_PLAN_HOURS, DEFAULT_PLAN_HOURS))
    min_spread = float(options.get(CONF_MIN_SPREAD, DEFAULT_MIN_SPREAD))
    max_charge_watts = int(options.get(CONF_MAX_CHARGE_WATTS, DEFAULT_MAX_CHARGE_WATTS))
    max_discharge_watts = int(options.get(CONF_MAX_DISCHARGE_WATTS, DEFAULT_MAX_DISCHARGE_WATTS))

    entities: list[SensorEntity] = []
    cost_sensors: dict[str, AccumulatedCostSensor] = {}
    revenue_sensors: dict[str, AccumulatedRevenueSensor] = {}

    # ── Per-device sensors ────────────────────────────────────────────────
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

        # API sensors
        sensor_list = list(BATTERY_SENSORS)
        if model in MODELS_WITH_PV:
            sensor_list.extend(PV_SENSORS)

        for desc in sensor_list:
            entities.append(
                MarstekSensor(coord, desc, device_info, uid_prefix)
            )

        # Computed sensors
        entities.append(
            BatterijverbruikGridPowerSensor(coord, device_info, uid_prefix, suffix)
        )
        entities.append(
            BeschikbareKwhSensor(coord, device_info, uid_prefix, suffix)
        )
        entities.append(
            BatteryChargePowerSensor(coord, device_info, uid_prefix, suffix)
        )
        entities.append(
            BatteryDischargePowerSensor(coord, device_info, uid_prefix, suffix)
        )

        # Cost/revenue sensors (need electricity price entity)
        kosten_sensor = AccumulatedCostSensor(
            coord, device_info, uid_prefix, suffix, hass, price_entity
        )
        opbrengst_sensor = AccumulatedRevenueSensor(
            coord, device_info, uid_prefix, suffix, hass, price_entity
        )
        entities.append(kosten_sensor)
        entities.append(opbrengst_sensor)
        cost_sensors[ble_mac] = kosten_sensor
        revenue_sensors[ble_mac] = opbrengst_sensor

        entities.append(
            KostenrateSensor(
                coord, device_info, uid_prefix, suffix, hass, price_entity
            )
        )
        entities.append(
            OpbrengstrateSensor(
                coord, device_info, uid_prefix, suffix, hass, price_entity
            )
        )

        # Grid share entity IDs (will be created globally below)
        grid_share_entity_id = f"sensor.ha_battery_charge_grid_share"
        entities.append(
            KostenrateNetSensor(
                coord,
                device_info,
                uid_prefix,
                suffix,
                hass,
                price_entity,
                grid_share_entity_id,
            )
        )

    # ── Fleet-wide sensors ────────────────────────────────────────────────
    # Only the entry that owns plan sensors creates fleet entities.
    # This avoids duplicate "Marstek Fleet" devices when batteries are added
    # one by one as separate config entries.
    is_plan_entry = hass.data[DOMAIN].get(PLAN_SENSORS_ENTRY_KEY) == entry.entry_id

    if not is_plan_entry:
        async_add_entities(entities)
        return

    fleet_device_info = DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_fleet")},
        name="Marstek Fleet",
        manufacturer="Marstek",
        model="Multi-device",
    )

    entities.extend(
        [
            TotalRatedCapacitySensor(
                multi_coordinator, "total_rated_capacity_wh", "Totale Marstek Rated Capacity",
                entry.entry_id, fleet_device_info
            ),
            TotalRemainingCapacitySensor(
                multi_coordinator, "total_remaining_capacity_wh", "Totale Marstek Remaining Capacity",
                entry.entry_id, fleet_device_info
            ),
            HaBatteryChargePowerTotalSensor(
                multi_coordinator, "total_charge_power", "HA Battery Charge Power Total",
                entry.entry_id, fleet_device_info
            ),
            HaBatteryDischargePowerTotalSensor(
                multi_coordinator, "total_discharge_power", "HA Battery Discharge Power Total",
                entry.entry_id, fleet_device_info
            ),
            HaBatteryPowerTotalSignedSensor(
                multi_coordinator, "total_bat_power_signed", "HA Battery Power Total Signed",
                entry.entry_id, fleet_device_info
            ),
            TotalBeschikbareKwhSensor(
                multi_coordinator, multi_coordinator.device_coordinators, entry.entry_id, fleet_device_info
            ),
            HaBatteryChargeSolarShareSensor(
                multi_coordinator, entry.entry_id, hass, grid_power_entity, fleet_device_info
            ),
            HaBatteryChargeGridShareSensor(
                multi_coordinator, entry.entry_id, hass, grid_power_entity, fleet_device_info
            ),
            BatterijverbruikKostenTotaalSensor(
                multi_coordinator, multi_coordinator.device_coordinators, entry.entry_id,
                hass, price_entity, cost_sensors, fleet_device_info
            ),
            BatterijverbruikOpbrengstTotaalSensor(
                multi_coordinator, multi_coordinator.device_coordinators, entry.entry_id,
                hass, price_entity, revenue_sensors, fleet_device_info
            ),
        ]
    )

    # Stroomprijs totaal (only if market price entity configured)
    if market_entity:
        entities.append(
            StroomPrijsTotaalSensor(
                multi_coordinator, entry.entry_id, hass,
                market_entity, energy_tax, procurement_fee, fleet_device_info,
            )
        )

    if market_entity:
        _plan_kwargs = dict(
            market_price_entity=market_entity,
            energy_tax=energy_tax,
            procurement_fee=procurement_fee,
            plan_hours=plan_hours,
            min_spread=min_spread,
            max_charge_watts=max_charge_watts,
            max_discharge_watts=max_discharge_watts,
            device_info=fleet_device_info,
        )
        entities.extend(
            [
                MarstekPlanSensor(
                    multi_coordinator, entry.entry_id, hass, False, **_plan_kwargs
                ),
                MarstekPlanSensor(
                    multi_coordinator, entry.entry_id, hass, True, **_plan_kwargs
                ),
                MarstekPlanWattSensor(
                    multi_coordinator, entry.entry_id, hass, True, **_plan_kwargs
                ),
                MarstekPlanWattSensor(
                    multi_coordinator, entry.entry_id, hass, False, **_plan_kwargs
                ),
            ]
        )

    async_add_entities(entities)
