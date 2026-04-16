"""Marstek Local API V2 – binary sensor entities."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CATEGORY_FAST,
    CONF_ENERGY_TAX_ENTITY,
    CONF_MARKET_PRICE_ENTITY,
    CONF_MIN_SPREAD_ENTITY,
    CONF_PLAN_HOURS_ENTITY,
    CONF_PROCUREMENT_FEE_ENTITY,
    DOMAIN,
)
from .coordinator import MarstekDataUpdateCoordinator, MarstekMultiDeviceCoordinator
from .plan_utils import compute_plan, is_current_hour_in_slots


class MarstekBinarySensor(
    CoordinatorEntity[MarstekDataUpdateCoordinator], BinarySensorEntity
):
    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        key: str,
        name: str,
        device_info: DeviceInfo,
        unique_id: str,
        device_class: BinarySensorDeviceClass | None = None,
        icon_on: str | None = None,
        icon_off: str | None = None,
        value_fn=None,
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._value_fn = value_fn
        self._icon_on = icon_on
        self._icon_off = icon_off
        self._attr_name = name
        self._attr_device_info = device_info
        self._attr_unique_id = unique_id
        self._attr_device_class = device_class

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        try:
            return bool(self._value_fn(self.coordinator.data))
        except Exception:
            return None

    @property
    def icon(self) -> str | None:
        if self.is_on and self._icon_on:
            return self._icon_on
        if not self.is_on and self._icon_off:
            return self._icon_off
        return None


class MarstekMoetNuLadenSensor(
    CoordinatorEntity[MarstekMultiDeviceCoordinator], BinarySensorEntity
):
    """True when the current hour is a scheduled charge slot in today's plan."""

    _attr_icon = "mdi:battery-charging"

    def __init__(
        self,
        coordinator: MarstekMultiDeviceCoordinator,
        entry_id: str,
        hass: HomeAssistant,
        market_entity: str | None,
        tax_entity: str | None,
        fee_entity: str | None,
        plan_hours_entity: str | None,
        min_spread_entity: str | None,
        num_batteries: int,
        capacity_kwh: float,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_marstek_moet_nu_laden"
        self._attr_name = "Marstek moet nu laden"
        self._hass = hass
        self._market_entity = market_entity
        self._tax_entity = tax_entity
        self._fee_entity = fee_entity
        self._plan_hours_entity = plan_hours_entity
        self._min_spread_entity = min_spread_entity
        self._num_batteries = num_batteries
        self._capacity_kwh = capacity_kwh

    @property
    def available(self) -> bool:
        return bool(self._market_entity)

    @property
    def is_on(self) -> bool | None:
        if not self._market_entity:
            return None
        plan = compute_plan(
            self._hass, False,
            self._market_entity, self._tax_entity, self._fee_entity,
            self._plan_hours_entity, self._min_spread_entity,
            num_batteries=self._num_batteries,
            watts_per_bat=800,
            capacity_kwh=self._capacity_kwh,
        )
        if not plan:
            return False
        return is_current_hour_in_slots(plan.get("charge_slots", []))


class MarstekMoetNuOntladenSensor(
    CoordinatorEntity[MarstekMultiDeviceCoordinator], BinarySensorEntity
):
    """True when the current hour is a scheduled discharge slot in today's plan."""

    _attr_icon = "mdi:battery-arrow-down"

    def __init__(
        self,
        coordinator: MarstekMultiDeviceCoordinator,
        entry_id: str,
        hass: HomeAssistant,
        market_entity: str | None,
        tax_entity: str | None,
        fee_entity: str | None,
        plan_hours_entity: str | None,
        min_spread_entity: str | None,
        num_batteries: int,
        capacity_kwh: float,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_marstek_moet_nu_ontladen"
        self._attr_name = "Marstek moet nu ontladen"
        self._hass = hass
        self._market_entity = market_entity
        self._tax_entity = tax_entity
        self._fee_entity = fee_entity
        self._plan_hours_entity = plan_hours_entity
        self._min_spread_entity = min_spread_entity
        self._num_batteries = num_batteries
        self._capacity_kwh = capacity_kwh

    @property
    def available(self) -> bool:
        return bool(self._market_entity)

    @property
    def is_on(self) -> bool | None:
        if not self._market_entity:
            return None
        plan = compute_plan(
            self._hass, False,
            self._market_entity, self._tax_entity, self._fee_entity,
            self._plan_hours_entity, self._min_spread_entity,
            num_batteries=self._num_batteries,
            watts_per_bat=800,
            capacity_kwh=self._capacity_kwh,
        )
        if not plan:
            return False
        return is_current_hour_in_slots(plan.get("discharge_slots", []))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    domain_data = hass.data[DOMAIN][entry.entry_id]
    device_coordinators: dict[str, MarstekDataUpdateCoordinator] = domain_data[
        "device_coordinators"
    ]
    multi_coordinator: MarstekMultiDeviceCoordinator = domain_data["multi_coordinator"]
    devices_info: dict[str, dict] = domain_data["devices_info"]

    options = entry.options
    market_entity = options.get(CONF_MARKET_PRICE_ENTITY)
    tax_entity = options.get(CONF_ENERGY_TAX_ENTITY)
    fee_entity = options.get(CONF_PROCUREMENT_FEE_ENTITY)
    plan_hours_entity = options.get(CONF_PLAN_HOURS_ENTITY)
    min_spread_entity = options.get(CONF_MIN_SPREAD_ENTITY)

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

        # Charging permission (from Bat.GetStatus)
        entities.append(
            MarstekBinarySensor(
                coord,
                key="charg_flag",
                name=f"Charging Allowed ({suffix})",
                device_info=device_info,
                unique_id=f"{uid_prefix}_charg_flag",
                device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
                icon_on="mdi:battery-charging",
                icon_off="mdi:battery-off",
                value_fn=lambda d: d.get("bat", {}).get("charg_flag"),
            )
        )

        # Discharge permission (from Bat.GetStatus)
        entities.append(
            MarstekBinarySensor(
                coord,
                key="dischrg_flag",
                name=f"Discharging Allowed ({suffix})",
                device_info=device_info,
                unique_id=f"{uid_prefix}_dischrg_flag",
                icon_on="mdi:battery-arrow-down",
                icon_off="mdi:battery-arrow-down-outline",
                value_fn=lambda d: d.get("bat", {}).get("dischrg_flag"),
            )
        )

        # Battery charging (ongrid_power < 0 → charging from grid)
        entities.append(
            MarstekBinarySensor(
                coord,
                key="is_charging",
                name=f"Battery Charging ({suffix})",
                device_info=device_info,
                unique_id=f"{uid_prefix}_is_charging",
                device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
                icon_on="mdi:battery-charging-100",
                icon_off="mdi:battery",
                value_fn=lambda d: (d.get("es", {}).get("ongrid_power") or 0) < 0,
            )
        )

        # Battery discharging (ongrid_power > 0 → exporting to grid)
        entities.append(
            MarstekBinarySensor(
                coord,
                key="is_discharging",
                name=f"Battery Discharging ({suffix})",
                device_info=device_info,
                unique_id=f"{uid_prefix}_is_discharging",
                icon_on="mdi:battery-arrow-up",
                icon_off="mdi:battery",
                value_fn=lambda d: (d.get("es", {}).get("ongrid_power") or 0) > 0,
            )
        )

        # CT connected (from ES.GetMode)
        entities.append(
            MarstekBinarySensor(
                coord,
                key="ct_connected",
                name=f"CT Connected ({suffix})",
                device_info=device_info,
                unique_id=f"{uid_prefix}_ct_connected",
                device_class=BinarySensorDeviceClass.CONNECTIVITY,
                icon_on="mdi:current-ac",
                icon_off="mdi:current-ac",
                value_fn=lambda d: d.get("mode", {}).get("ct_state") == 1,
            )
        )

        # BLE connected
        entities.append(
            MarstekBinarySensor(
                coord,
                key="ble_connected",
                name=f"Bluetooth Connected ({suffix})",
                device_info=device_info,
                unique_id=f"{uid_prefix}_ble_connected",
                device_class=BinarySensorDeviceClass.CONNECTIVITY,
                icon_on="mdi:bluetooth",
                icon_off="mdi:bluetooth-off",
                value_fn=lambda d: d.get("ble", {}).get("state") in ("connect", "connected"),
            )
        )

    # ── Fleet-wide plan binary sensors (only when market price entity configured) ─
    if market_entity:
        # Derive fleet size from coordinator data for plan computation
        agg = multi_coordinator.get_aggregates()
        num_batteries = len(device_coordinators)
        total_rated_wh = agg.get("total_rated_capacity_wh") or (num_batteries * 5120)
        capacity_kwh = total_rated_wh / 1000

        entities.append(
            MarstekMoetNuLadenSensor(
                multi_coordinator, entry.entry_id, hass,
                market_entity, tax_entity, fee_entity,
                plan_hours_entity, min_spread_entity,
                num_batteries=num_batteries,
                capacity_kwh=capacity_kwh,
            )
        )
        entities.append(
            MarstekMoetNuOntladenSensor(
                multi_coordinator, entry.entry_id, hass,
                market_entity, tax_entity, fee_entity,
                plan_hours_entity, min_spread_entity,
                num_batteries=num_batteries,
                capacity_kwh=capacity_kwh,
            )
        )

    async_add_entities(entities)
