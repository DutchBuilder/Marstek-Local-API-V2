"""Marstek Local API V2 – custom services."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, MAX_SCHEDULE_SLOTS, WEEKDAY_ALL
from .coordinator import MarstekDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_REQUEST_SYNC = "request_sync"
SERVICE_SET_MANUAL_SCHEDULE = "set_manual_schedule"
SERVICE_SET_MANUAL_SCHEDULES = "set_manual_schedules"
SERVICE_CLEAR_MANUAL_SCHEDULES = "clear_manual_schedules"
SERVICE_SET_PASSIVE_MODE = "set_passive_mode"


def _get_all_device_coordinators(hass: HomeAssistant) -> dict[str, MarstekDataUpdateCoordinator]:
    """Return all device coordinators from all config entries."""
    coords: dict[str, MarstekDataUpdateCoordinator] = {}
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict) and "device_coordinators" in entry_data:
            coords.update(entry_data["device_coordinators"])
    return coords


def _get_coordinator_by_device_id(
    hass: HomeAssistant, device_id: str | None, entry_id: str | None
) -> MarstekDataUpdateCoordinator | None:
    """Look up a coordinator by device_id (ble_mac) or entry_id."""
    all_coords = _get_all_device_coordinators(hass)

    if device_id:
        # Try direct match on ble_mac
        if device_id in all_coords:
            return all_coords[device_id]
        # Try suffix match
        for mac, coord in all_coords.items():
            if mac.endswith(device_id.lower()) or device_id.lower() in mac.lower():
                return coord

    if entry_id:
        entry_data = hass.data[DOMAIN].get(entry_id, {})
        devs = entry_data.get("device_coordinators", {})
        if devs:
            return next(iter(devs.values()))

    return None


SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("time_num"): vol.All(vol.Coerce(int), vol.Range(min=0, max=9)),
        vol.Required("start_time"): cv.time,
        vol.Required("end_time"): cv.time,
        vol.Optional("days", default=WEEKDAY_ALL): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=127)
        ),
        vol.Optional("power", default=0): vol.Coerce(int),
        vol.Optional("enabled", default=True): cv.boolean,
    }
)


def async_setup_services(hass: HomeAssistant) -> None:
    """Register all Marstek services."""

    async def _service_request_sync(call: ServiceCall) -> None:
        """Trigger an immediate data refresh."""
        device_id = call.data.get("device_id")
        entry_id = call.data.get("entry_id")

        if device_id or entry_id:
            coord = _get_coordinator_by_device_id(hass, device_id, entry_id)
            if coord:
                await coord.async_request_refresh()
            else:
                _LOGGER.warning("request_sync: device '%s' not found", device_id or entry_id)
        else:
            # Refresh all
            all_coords = _get_all_device_coordinators(hass)
            await asyncio.gather(*(c.async_request_refresh() for c in all_coords.values()))

    async def _service_set_manual_schedule(call: ServiceCall) -> None:
        device_id = call.data["device_id"]
        coord = _get_coordinator_by_device_id(hass, device_id, None)
        if coord is None:
            _LOGGER.error("set_manual_schedule: device '%s' not found", device_id)
            return

        time_num = call.data["time_num"]
        start = call.data["start_time"].strftime("%H:%M")
        end = call.data["end_time"].strftime("%H:%M")
        days = call.data.get("days", WEEKDAY_ALL)
        power = call.data.get("power", 0)
        enabled = 1 if call.data.get("enabled", True) else 0

        try:
            await coord.client.set_mode_manual(
                time_num=time_num,
                start_time=start,
                end_time=end,
                power=power,
                week_set=days,
                enable=enabled,
            )
            await asyncio.sleep(0.5)
            await coord.async_request_refresh()
        except Exception as err:
            _LOGGER.error("set_manual_schedule failed: %s", err)
            raise

    async def _service_set_manual_schedules(call: ServiceCall) -> None:
        device_id = call.data["device_id"]
        coord = _get_coordinator_by_device_id(hass, device_id, None)
        if coord is None:
            _LOGGER.error("set_manual_schedules: device '%s' not found", device_id)
            return

        schedules = call.data["schedules"]
        failed = []
        for sched in schedules:
            try:
                start = sched["start_time"].strftime("%H:%M")
                end = sched["end_time"].strftime("%H:%M")
                await coord.client.set_mode_manual(
                    time_num=sched["time_num"],
                    start_time=start,
                    end_time=end,
                    power=sched.get("power", 0),
                    week_set=sched.get("days", WEEKDAY_ALL),
                    enable=1 if sched.get("enabled", True) else 0,
                )
                await asyncio.sleep(0.5)
            except Exception as err:
                _LOGGER.warning("Schedule slot %d failed: %s", sched.get("time_num"), err)
                failed.append(sched.get("time_num"))

        if failed:
            _LOGGER.warning("Failed schedule slots: %s", failed)
        await coord.async_request_refresh()

    async def _service_clear_manual_schedules(call: ServiceCall) -> None:
        device_id = call.data["device_id"]
        coord = _get_coordinator_by_device_id(hass, device_id, None)
        if coord is None:
            _LOGGER.error("clear_manual_schedules: device '%s' not found", device_id)
            return

        for slot in range(MAX_SCHEDULE_SLOTS):
            try:
                await coord.client.set_mode_manual(
                    time_num=slot,
                    start_time="00:00",
                    end_time="00:00",
                    power=0,
                    week_set=WEEKDAY_ALL,
                    enable=0,
                )
                await asyncio.sleep(0.3)
            except Exception as err:
                _LOGGER.warning("Clear slot %d failed: %s", slot, err)

        await coord.async_request_refresh()

    async def _service_set_passive_mode(call: ServiceCall) -> None:
        device_id = call.data["device_id"]
        coord = _get_coordinator_by_device_id(hass, device_id, None)
        if coord is None:
            _LOGGER.error("set_passive_mode: device '%s' not found", device_id)
            return

        power = call.data["power"]
        duration = call.data["duration"]

        try:
            await coord.client.set_mode_passive(power=power, cd_time=duration)
            await asyncio.sleep(0.5)
            await coord.async_request_refresh()
        except Exception as err:
            _LOGGER.error("set_passive_mode failed: %s", err)
            raise

    hass.services.async_register(
        DOMAIN,
        SERVICE_REQUEST_SYNC,
        _service_request_sync,
        schema=vol.Schema(
            {
                vol.Optional("device_id"): str,
                vol.Optional("entry_id"): str,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_MANUAL_SCHEDULE,
        _service_set_manual_schedule,
        schema=vol.Schema(
            {
                vol.Required("device_id"): str,
                vol.Required("time_num"): vol.All(vol.Coerce(int), vol.Range(min=0, max=9)),
                vol.Required("start_time"): cv.time,
                vol.Required("end_time"): cv.time,
                vol.Optional("days", default=WEEKDAY_ALL): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=127)
                ),
                vol.Optional("power", default=0): vol.Coerce(int),
                vol.Optional("enabled", default=True): cv.boolean,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_MANUAL_SCHEDULES,
        _service_set_manual_schedules,
        schema=vol.Schema(
            {
                vol.Required("device_id"): str,
                vol.Required("schedules"): [SCHEDULE_SCHEMA],
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_MANUAL_SCHEDULES,
        _service_clear_manual_schedules,
        schema=vol.Schema({vol.Required("device_id"): str}),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_PASSIVE_MODE,
        _service_set_passive_mode,
        schema=vol.Schema(
            {
                vol.Required("device_id"): str,
                vol.Required("power"): vol.All(
                    vol.Coerce(int), vol.Range(min=-10000, max=10000)
                ),
                vol.Required("duration"): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=86400)
                ),
            }
        ),
    )

    _LOGGER.debug("Marstek Local API V2 services registered")


def async_unload_services(hass: HomeAssistant) -> None:
    """Remove registered services."""
    for service in [
        SERVICE_REQUEST_SYNC,
        SERVICE_SET_MANUAL_SCHEDULE,
        SERVICE_SET_MANUAL_SCHEDULES,
        SERVICE_CLEAR_MANUAL_SCHEDULES,
        SERVICE_SET_PASSIVE_MODE,
    ]:
        hass.services.async_remove(DOMAIN, service)
