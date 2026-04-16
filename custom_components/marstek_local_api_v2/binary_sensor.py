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
    CONF_ENERGY_TAX,
    CONF_MARKET_PRICE_ENTITY,
    CONF_MAX_CHARGE_WATTS,
    CONF_MAX_DISCHARGE_WATTS,
    CONF_MIN_SPREAD,
    CONF_PLAN_HOURS,
    CONF_PROCUREMENT_FEE,
    DEFAULT_ENERGY_TAX,
    DEFAULT_MAX_CHARGE_WATTS,
    DEFAULT_MAX_DISCHARGE_WATTS,
    DEFAULT_MIN_SPREAD,
    DEFAULT_PLAN_HOURS,
    DEFAULT_PROCUREMENT_FEE,
    DOMAIN,
    PLAN_SENSORS_ENTRY_KEY,
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


class _MarstekPlanSlotSensor(
    CoordinatorEntity[MarstekMultiDeviceCoordinator], BinarySensorEntity
):
    """Base for 'moet nu laden/ontladen' binary sensors."""

    def __init__(
        self,
        coordinator: MarstekMultiDeviceCoordinator,
        entry_id: str,
        hass: HomeAssistant,
        is_charge: bool,
        market_entity: str | None,
        energy_tax: float,
        procurement_fee: float,
        plan_hours: int,
        min_spread: float,
        max_charge_watts: int,
        max_discharge_watts: int,
        num_batteries: int,
        capacity_kwh: float,
    ) -> None:
        super().__init__(coordinator)
        kind = "laden" if is_charge else "ontladen"
        self._attr_unique_id = f"{entry_id}_marstek_moet_nu_{kind}"
        self._attr_name = f"Marstek moet nu {kind}"
        self._attr_icon = "mdi:battery-charging" if is_charge else "mdi:battery-arrow-down"
        self._hass = hass
        self._is_charge = is_charge
        self._market_entity = market_entity
        self._energy_tax = energy_tax
        self._procurement_fee = procurement_fee
        self._plan_hours = plan_hours
        self._min_spread = min_spread
        self._max_charge_watts = max_charge_watts
        self._max_discharge_watts = max_discharge_watts
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
            self._hass, False, self._market_entity,
            energy_tax=self._energy_tax,
            procurement_fee=self._procurement_fee,
            plan_hours=self._plan_hours,
            min_spread=self._min_spread,
            num_batteries=self._num_batteries,
            max_charge_watts_per_bat=self._max_charge_watts,
            max_discharge_watts_per_bat=self._max_discharge_watts,
            capacity_kwh=self._capacity_kwh,
        )
        if not plan:
            return False
        slot_key = "charge_slots" if self._is_charge else "discharge_slots"
        return is_current_hour_in_slots(plan.get(slot_key, []))


class MarstekMoetNuLadenSensor(_MarstekPlanSlotSensor):
    def __init__(self, coordinator, entry_id, hass, **kwargs) -> None:
        super().__init__(coordinator, entry_id, hass, is_charge=True, **kwargs)


class MarstekMoetNuOntladenSensor(_MarstekPlanSlotSensor):
    def __init__(self, coordinator, entry_id, hass, **kwargs) -> None:
        super().__init__(coordinator, entry_id, hass, is_charge=False, **kwargs)


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
    energy_tax = float(options.get(CONF_ENERGY_TAX, DEFAULT_ENERGY_TAX))
    procurement_fee = float(options.get(CONF_PROCUREMENT_FEE, DEFAULT_PROCUREMENT_FEE))
    plan_hours = int(options.get(CONF_PLAN_HOURS, DEFAULT_PLAN_HOURS))
    min_spread = float(options.get(CONF_MIN_SPREAD, DEFAULT_MIN_SPREAD))
    max_charge_watts = int(options.get(CONF_MAX_CHARGE_WATTS, DEFAULT_MAX_CHARGE_WATTS))
    max_discharge_watts = int(options.get(CONF_MAX_DISCHARGE_WATTS, DEFAULT_MAX_DISCHARGE_WATTS))

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

    # ── Fleet-wide plan binary sensors (only when market price entity configured
    #    and this entry owns the plan sensors) ────────────────────────────────
    is_plan_entry = hass.data[DOMAIN].get(PLAN_SENSORS_ENTRY_KEY) == entry.entry_id
    if market_entity and is_plan_entry:
        agg = multi_coordinator.get_aggregates()
        num_batteries = len(device_coordinators)
        total_rated_wh = agg.get("total_rated_capacity_wh") or (num_batteries * 5120)
        capacity_kwh = total_rated_wh / 1000

        _plan_kwargs = dict(
            market_entity=market_entity,
            energy_tax=energy_tax,
            procurement_fee=procurement_fee,
            plan_hours=plan_hours,
            min_spread=min_spread,
            max_charge_watts=max_charge_watts,
            max_discharge_watts=max_discharge_watts,
            num_batteries=num_batteries,
            capacity_kwh=capacity_kwh,
        )
        entities.append(MarstekMoetNuLadenSensor(multi_coordinator, entry.entry_id, hass, **_plan_kwargs))
        entities.append(MarstekMoetNuOntladenSensor(multi_coordinator, entry.entry_id, hass, **_plan_kwargs))

    async_add_entities(entities)
