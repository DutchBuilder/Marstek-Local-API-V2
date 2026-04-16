"""Marstek Local API V2 - UDP client (based on Rev 2.0 API specification)."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import socket
import time
from typing import Any

from .const import (
    COMMAND_BACKOFF_BASE,
    COMMAND_BACKOFF_FACTOR,
    COMMAND_BACKOFF_JITTER,
    COMMAND_BACKOFF_MAX,
    COMMAND_TIMEOUT,
    DEFAULT_PORT,
    DISCOVERY_TIMEOUT,
    MAX_RETRIES,
    METHOD_BAT_STATUS,
    METHOD_BLE_ADV,
    METHOD_BLE_STATUS,
    METHOD_DOD_SET,
    METHOD_EM_STATUS,
    METHOD_ES_GET_MODE,
    METHOD_ES_SET_MODE,
    METHOD_ES_STATUS,
    METHOD_GET_DEVICE,
    METHOD_LED_CTRL,
    METHOD_PV_STATUS,
    METHOD_WIFI_STATUS,
    RETRY_DELAY,
)

_LOGGER = logging.getLogger(__name__)

# Shared sockets per port (reference counted)
_shared_sockets: dict[int, tuple[socket.socket, int]] = {}
_socket_lock = asyncio.Lock()


async def _get_shared_socket(port: int) -> socket.socket:
    """Get or create a shared UDP socket for the given port."""
    async with _socket_lock:
        if port in _shared_sockets:
            sock, ref_count = _shared_sockets[port]
            _shared_sockets[port] = (sock, ref_count + 1)
            return sock

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass  # Not supported on Windows
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        sock.bind(("", port))
        _shared_sockets[port] = (sock, 1)
        _LOGGER.debug("Created shared UDP socket on port %d", port)
        return sock


async def _release_shared_socket(port: int) -> None:
    """Release a shared socket reference."""
    async with _socket_lock:
        if port not in _shared_sockets:
            return
        sock, ref_count = _shared_sockets[port]
        if ref_count <= 1:
            sock.close()
            del _shared_sockets[port]
            _LOGGER.debug("Closed shared UDP socket on port %d", port)
        else:
            _shared_sockets[port] = (sock, ref_count - 1)


class MarstekUDPClient:
    """Async UDP client for Marstek devices (Rev 2.0 API)."""

    def __init__(self, host: str, port: int = DEFAULT_PORT) -> None:
        self.host = host
        self.port = port
        self._msg_id: int = 0
        self._sock: socket.socket | None = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        """Obtain shared socket."""
        if not self._connected:
            self._sock = await _get_shared_socket(self.port)
            self._connected = True

    async def disconnect(self) -> None:
        """Release shared socket reference."""
        if self._connected:
            await _release_shared_socket(self.port)
            self._sock = None
            self._connected = False

    def _next_id(self) -> int:
        self._msg_id = (self._msg_id + 1) & 0xFFFF
        return self._msg_id

    async def _send_command(
        self,
        method: str,
        params: dict[str, Any],
        timeout: float = COMMAND_TIMEOUT,
        max_attempts: int = MAX_RETRIES,
    ) -> dict[str, Any]:
        """Send UDP command and wait for response with retry + backoff."""
        if not self._connected or self._sock is None:
            await self.connect()

        msg_id = self._next_id()
        payload = json.dumps({"id": msg_id, "method": method, "params": params}).encode()

        loop = asyncio.get_event_loop()
        backoff = COMMAND_BACKOFF_BASE

        for attempt in range(max_attempts):
            try:
                self._sock.sendto(payload, (self.host, self.port))
                deadline = loop.time() + timeout
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    try:
                        data = await asyncio.wait_for(
                            loop.run_in_executor(None, self._recv_one),
                            timeout=min(remaining, 2.0),
                        )
                    except asyncio.TimeoutError:
                        if loop.time() >= deadline:
                            raise
                        continue

                    try:
                        response = json.loads(data)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue

                    if response.get("id") == msg_id:
                        if "error" in response:
                            raise ValueError(
                                f"API error {response['error'].get('code')}: "
                                f"{response['error'].get('message')}"
                            )
                        return response.get("result", {})

            except asyncio.TimeoutError:
                if attempt < max_attempts - 1:
                    jitter = random.uniform(
                        -COMMAND_BACKOFF_JITTER, COMMAND_BACKOFF_JITTER
                    )
                    wait = min(backoff + jitter, COMMAND_BACKOFF_MAX)
                    _LOGGER.debug(
                        "Timeout on attempt %d for %s@%s, retrying in %.1fs",
                        attempt + 1,
                        method,
                        self.host,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    backoff = min(backoff * COMMAND_BACKOFF_FACTOR, COMMAND_BACKOFF_MAX)
                else:
                    raise
            except Exception as err:
                if attempt < max_attempts - 1:
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    raise

        raise RuntimeError(f"All {max_attempts} attempts failed for {method}")

    def _recv_one(self) -> bytes:
        """Blocking receive (called in executor)."""
        try:
            data, _ = self._sock.recvfrom(4096)
            return data
        except BlockingIOError:
            return b""

    # ------------------------------------------------------------------ #
    # Query commands                                                       #
    # ------------------------------------------------------------------ #

    async def get_device_info(self, ble_mac: str = "0") -> dict[str, Any]:
        """Marstek.GetDevice – identify the device."""
        return await self._send_command(
            METHOD_GET_DEVICE, {"ble_mac": ble_mac}
        )

    async def get_wifi_status(self) -> dict[str, Any]:
        """Wifi.GetStatus"""
        return await self._send_command(METHOD_WIFI_STATUS, {"id": 0})

    async def get_ble_status(self) -> dict[str, Any]:
        """BLE.GetStatus"""
        return await self._send_command(METHOD_BLE_STATUS, {"id": 0})

    async def get_bat_status(self) -> dict[str, Any]:
        """Bat.GetStatus"""
        return await self._send_command(METHOD_BAT_STATUS, {"id": 0})

    async def get_pv_status(self) -> dict[str, Any]:
        """PV.GetStatus"""
        return await self._send_command(METHOD_PV_STATUS, {"id": 0})

    async def get_es_status(self) -> dict[str, Any]:
        """ES.GetStatus"""
        return await self._send_command(METHOD_ES_STATUS, {"id": 0})

    async def get_es_mode(self) -> dict[str, Any]:
        """ES.GetMode"""
        return await self._send_command(METHOD_ES_GET_MODE, {"id": 0})

    async def get_em_status(self) -> dict[str, Any]:
        """EM.GetStatus"""
        return await self._send_command(METHOD_EM_STATUS, {"id": 0})

    # ------------------------------------------------------------------ #
    # Set commands                                                         #
    # ------------------------------------------------------------------ #

    async def set_es_mode(self, config: dict[str, Any]) -> dict[str, Any]:
        """ES.SetMode"""
        return await self._send_command(
            METHOD_ES_SET_MODE, {"id": 0, "config": config}
        )

    async def set_dod(self, value: int) -> dict[str, Any]:
        """DOD.SET – value range 30-88."""
        value = max(30, min(88, int(value)))
        return await self._send_command(METHOD_DOD_SET, {"id": 0, "value": value})

    async def set_ble_advertising(self, enable: bool) -> dict[str, Any]:
        """Ble.Adv – 0=enable, 1=disable (API inverted)."""
        return await self._send_command(
            METHOD_BLE_ADV, {"enable": 0 if enable else 1}
        )

    async def set_led(self, state: bool) -> dict[str, Any]:
        """Led.Ctrl – 1=on, 0=off."""
        return await self._send_command(
            METHOD_LED_CTRL, {"state": 1 if state else 0}
        )

    # ------------------------------------------------------------------ #
    # Mode helpers                                                         #
    # ------------------------------------------------------------------ #

    async def set_mode_auto(self) -> dict[str, Any]:
        return await self.set_es_mode({"mode": "Auto", "auto_cfg": {"enable": 1}})

    async def set_mode_ai(self) -> dict[str, Any]:
        return await self.set_es_mode({"mode": "AI", "ai_cfg": {"enable": 1}})

    async def set_mode_ups(self) -> dict[str, Any]:
        return await self.set_es_mode({"mode": "UPS", "ups_cfg": {"enable": 1}})

    async def set_mode_manual(
        self,
        time_num: int,
        start_time: str,
        end_time: str,
        power: int = 0,
        week_set: int = 127,
        enable: int = 1,
    ) -> dict[str, Any]:
        return await self.set_es_mode(
            {
                "mode": "Manual",
                "manual_cfg": {
                    "time_num": time_num,
                    "start_time": start_time,
                    "end_time": end_time,
                    "week_set": week_set,
                    "power": power,
                    "enable": enable,
                },
            }
        )

    async def set_mode_passive(self, power: int, cd_time: int) -> dict[str, Any]:
        return await self.set_es_mode(
            {
                "mode": "Passive",
                "passive_cfg": {"power": power, "cd_time": cd_time},
            }
        )


# ------------------------------------------------------------------ #
# Device discovery                                                    #
# ------------------------------------------------------------------ #


async def discover_devices(
    port: int = DEFAULT_PORT,
    timeout: float = DISCOVERY_TIMEOUT,
) -> list[dict[str, Any]]:
    """
    Broadcast Marstek.GetDevice on the LAN and collect responses.
    Returns a list of device info dicts.
    """
    loop = asyncio.get_event_loop()
    devices: dict[str, dict[str, Any]] = {}  # ble_mac → info

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)

    try:
        sock.bind(("", port))
        payload = json.dumps(
            {"id": 0, "method": METHOD_GET_DEVICE, "params": {"ble_mac": "0"}}
        ).encode()

        deadline = loop.time() + timeout
        broadcast_interval = 2.0
        last_broadcast = 0.0

        while loop.time() < deadline:
            now = loop.time()
            if now - last_broadcast >= broadcast_interval:
                try:
                    sock.sendto(payload, ("255.255.255.255", port))
                except Exception:
                    pass
                last_broadcast = now

            remaining = deadline - loop.time()
            try:
                data = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: _recv_broadcast(sock)),
                    timeout=min(remaining, 0.5),
                )
                if data:
                    try:
                        resp = json.loads(data)
                        result = resp.get("result", {})
                        mac = result.get("ble_mac")
                        if mac and mac not in devices:
                            devices[mac] = result
                            _LOGGER.debug(
                                "Discovered Marstek device: %s", result
                            )
                    except (json.JSONDecodeError, AttributeError):
                        pass
            except asyncio.TimeoutError:
                pass

    finally:
        sock.close()

    return list(devices.values())


def _recv_broadcast(sock: socket.socket) -> bytes:
    try:
        data, _ = sock.recvfrom(4096)
        return data
    except BlockingIOError:
        return b""


async def validate_connection(host: str, port: int) -> dict[str, Any]:
    """Validate connection to a device and return its info."""
    client = MarstekUDPClient(host, port)
    try:
        await client.connect()
        info = await client.get_device_info()
        return info
    finally:
        await client.disconnect()
