"""Marstek Local API V2 – data update coordinators."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MarstekUDPClient
from .const import (
    CATEGORY_FAST,
    CATEGORY_MEDIUM,
    CATEGORY_SLOW,
    DEFAULT_DOD,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MODELS_WITH_PV,
    STALE_AFTER_MISSED,
    UPDATE_TIER_FAST,
    UPDATE_TIER_MEDIUM,
    UPDATE_TIER_SLOW,
)

_LOGGER = logging.getLogger(__name__)


class MarstekDataUpdateCoordinator(DataUpdateCoordinator):
    """
    Coordinator for a single Marstek device.

    Data structure:
    {
        "bat":    { ... Bat.GetStatus result ... },
        "es":     { ... ES.GetStatus result ... },
        "mode":   { ... ES.GetMode result ... },
        "em":     { ... EM.GetStatus result ... },
        "pv":     { ... PV.GetStatus result ... },
        "wifi":   { ... Wifi.GetStatus result ... },
        "ble":    { ... BLE.GetStatus result ... },
        "device": { ... Marstek.GetDevice result ... },
        "dod":    int,   # current DOD value (write-only to API, stored here)
    }
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: MarstekUDPClient,
        ble_mac: str,
        device_model: str,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        dod: int = DEFAULT_DOD,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{ble_mac}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.ble_mac = ble_mac
        self.device_model = device_model
        self.scan_interval = scan_interval
        self._dod = dod

        self._update_count = 0
        self._first_update = True

        # Stale tracking: missed-update counters per category
        self._missed: dict[str, int] = {
            CATEGORY_FAST: 0,
            CATEGORY_MEDIUM: 0,
            CATEGORY_SLOW: 0,
        }
        # Cache (preserves last-known good data)
        self._cache: dict[str, Any] = {
            "bat": {},
            "es": {},
            "mode": {},
            "em": {},
            "pv": {},
            "wifi": {},
            "ble": {},
            "device": {},
            "dod": dod,
        }

    @property
    def dod(self) -> int:
        return self._dod

    @dod.setter
    def dod(self, value: int) -> None:
        self._dod = value
        self._cache["dod"] = value

    def is_stale(self, category: str) -> bool:
        return self._missed.get(category, 0) >= STALE_AFTER_MISSED

    async def _async_update_data(self) -> dict[str, Any]:
        self._update_count += 1
        is_first = self._first_update
        self._first_update = False

        # First refresh: single attempt, short timeout to stay within HA's 60s setup budget.
        cmd_timeout = 8.0 if is_first else 15.0
        cmd_attempts = 1 if is_first else 3

        # ── Fast tier: every update (Bat, ES.GetStatus, ES.GetMode) ──────
        if self._update_count % UPDATE_TIER_FAST == 0:
            fast_ok = await self._update_fast(cmd_attempts, cmd_timeout)
            if fast_ok:
                self._missed[CATEGORY_FAST] = 0
            else:
                self._missed[CATEGORY_FAST] += 1

        # ── Medium tier: every 10th update (EM, PV) ──────────────────────
        if self._update_count % UPDATE_TIER_MEDIUM == 0:
            medium_ok = await self._update_medium(cmd_attempts, cmd_timeout)
            if medium_ok:
                self._missed[CATEGORY_MEDIUM] = 0
            else:
                self._missed[CATEGORY_MEDIUM] += 1

        # ── Slow tier: every 20th update (Device, WiFi, BLE) ─────────────
        # Not on first update: wifi/ble/device info is non-critical.
        if self._update_count % UPDATE_TIER_SLOW == 0:
            slow_ok = await self._update_slow(cmd_attempts, cmd_timeout)
            if slow_ok:
                self._missed[CATEGORY_SLOW] = 0
            else:
                self._missed[CATEGORY_SLOW] += 1

        # On first update, fail loudly if we got nothing
        if is_first and not self._cache.get("bat") and not self._cache.get("es"):
            raise UpdateFailed("Could not retrieve initial data from device")

        return dict(self._cache)

    async def _update_fast(self, attempts: int, timeout: float) -> bool:
        ok = True
        for coro_name, cache_key in [
            ("get_bat_status", "bat"),
            ("get_es_status", "es"),
            ("get_es_mode", "mode"),
        ]:
            try:
                result = await getattr(self.client, coro_name)(
                    timeout=timeout, max_attempts=attempts
                )
                self._cache[cache_key] = result
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.debug("Fast update failed for %s: %s", cache_key, err)
                ok = False
        return ok

    async def _update_medium(self, attempts: int, timeout: float) -> bool:
        ok = True
        try:
            self._cache["em"] = await self.client.get_em_status(
                timeout=timeout, max_attempts=attempts
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("Medium update failed for em: %s", err)
            ok = False

        if self.device_model in MODELS_WITH_PV:
            try:
                self._cache["pv"] = await self.client.get_pv_status(
                    timeout=timeout, max_attempts=attempts
                )
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.debug("Medium update failed for pv: %s", err)
                ok = False

        return ok

    async def _update_slow(self, attempts: int, timeout: float) -> bool:
        ok = True
        for coro_name, cache_key in [
            ("get_device_info", "device"),
            ("get_wifi_status", "wifi"),
            ("get_ble_status", "ble"),
        ]:
            try:
                result = await getattr(self.client, coro_name)(
                    timeout=timeout, max_attempts=attempts
                )
                self._cache[cache_key] = result
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.debug("Slow update failed for %s: %s", cache_key, err)
                ok = False
        return ok

    def get_cached(self, key: str) -> dict[str, Any]:
        return self._cache.get(key, {})


class MarstekMultiDeviceCoordinator(DataUpdateCoordinator):
    """
    Domain-wide coordinator that aggregates data from ALL device coordinators.

    One instance per HA domain (not per config entry).  Each config entry
    calls add_device() to register its batteries; the coordinator pushes
    fresh aggregated data to fleet sensors reactively on every device update.

    Data structure:
    {
        "devices": { ble_mac: <device-coordinator data dict>, ... },
        "aggregates": {
            "total_rated_capacity_wh": float,
            "total_remaining_capacity_wh": float,
            "total_pv_power": float,
            "total_ongrid_power": float,
            "total_offgrid_power": float,
            "total_bat_power": float,
            "total_charge_power": float,
            "total_discharge_power": float,
            "average_soc": float,
        }
    }
    """

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_multi",
            update_interval=None,  # purely reactive – updated via add_device listeners
        )
        self.device_coordinators: dict[str, MarstekDataUpdateCoordinator] = {}
        self._unsub_callbacks: dict[str, Any] = {}

    def add_device(
        self, ble_mac: str, coord: MarstekDataUpdateCoordinator
    ) -> None:
        """Register a device coordinator and subscribe to its updates."""
        self.device_coordinators[ble_mac] = coord

        @callback
        def _on_update() -> None:
            self.async_set_updated_data(self._build_data())

        self._unsub_callbacks[ble_mac] = coord.async_add_listener(_on_update)

    def remove_device(self, ble_mac: str) -> None:
        """Unregister a device coordinator and unsubscribe from its updates."""
        unsub = self._unsub_callbacks.pop(ble_mac, None)
        if unsub:
            unsub()
        self.device_coordinators.pop(ble_mac, None)

    def _build_data(self) -> dict[str, Any]:
        results = {
            mac: coord.data or {}
            for mac, coord in self.device_coordinators.items()
        }
        return {"devices": results, "aggregates": self._compute_aggregates(results)}

    async def _async_update_data(self) -> dict[str, Any]:
        return self._build_data()

    def _compute_aggregates(
        self, devices: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        total_rated = 0.0
        total_remaining = 0.0
        total_pv_power = 0.0
        total_ongrid = 0.0
        total_offgrid = 0.0
        total_bat_power = 0.0
        total_charge = 0.0
        total_discharge = 0.0
        weighted_soc = 0.0
        rated_sum = 0.0

        for mac, data in devices.items():
            if not data:
                continue
            bat = data.get("bat", {})
            es = data.get("es", {})

            rated_wh = float(bat.get("rated_capacity") or es.get("bat_cap") or 0)
            remaining_wh = float(bat.get("bat_capacity") or 0)
            soc = float(bat.get("soc") or es.get("bat_soc") or 0)
            pv_power = float(es.get("pv_power") or 0)
            ongrid = float(es.get("ongrid_power") or 0)
            offgrid = float(es.get("offgrid_power") or 0)
            bat_power = float(es.get("bat_power") or 0)

            total_rated += rated_wh
            total_remaining += remaining_wh
            total_pv_power += pv_power
            total_ongrid += ongrid
            total_offgrid += offgrid
            total_bat_power += bat_power

            # charge = when ongrid < 0 (drawing from grid to charge)
            total_charge += max(0.0, -ongrid)
            total_discharge += max(0.0, ongrid)

            if rated_wh > 0:
                weighted_soc += soc * rated_wh
                rated_sum += rated_wh

        average_soc = (weighted_soc / rated_sum) if rated_sum > 0 else 0.0

        return {
            "total_rated_capacity_wh": total_rated,
            "total_remaining_capacity_wh": total_remaining,
            "total_pv_power": total_pv_power,
            "total_ongrid_power": total_ongrid,
            "total_offgrid_power": total_offgrid,
            "total_bat_power": total_bat_power,
            "total_charge_power": total_charge,
            "total_discharge_power": total_discharge,
            "average_soc": average_soc,
        }

    def get_device_data(self, ble_mac: str) -> dict[str, Any]:
        if self.data and "devices" in self.data:
            return self.data["devices"].get(ble_mac, {})
        return {}

    def get_aggregates(self) -> dict[str, Any]:
        if self.data:
            return self.data.get("aggregates", {})
        return {}
