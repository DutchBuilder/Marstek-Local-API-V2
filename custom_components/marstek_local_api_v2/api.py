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


class MarstekUDPClient:
    """
    Async UDP client for a single Marstek device (Rev 2.0 API).

    Each client has its own socket so responses from different devices
    never interfere with each other.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        source_port: int | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._source_port = source_port  # None → ephemeral; int → bind to fixed source port
        self._msg_id: int = 0
        self._sock: socket.socket | None = None

    @property
    def is_connected(self) -> bool:
        return self._sock is not None

    async def connect(self) -> None:
        if self._sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except AttributeError:
                pass  # not available on all platforms (e.g. Windows)
            if self._source_port is not None:
                # Older firmware (e.g. VenusE fw 1573) only responds to packets
                # whose source port equals its own listening port (30000).
                sock.bind(("", self._source_port))
            # Non-blocking so the event loop can use it with sock_recvfrom.
            sock.setblocking(False)
            self._sock = sock
            _LOGGER.debug(
                "UDP socket opened for %s:%d (source_port=%s)",
                self.host, self.port, self._source_port or "ephemeral",
            )

    async def disconnect(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
            _LOGGER.debug("UDP socket closed for %s:%d", self.host, self.port)

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
        """Send a UDP command and wait for the matching response (native async)."""
        if self._sock is None:
            await self.connect()

        msg_id = self._next_id()
        payload = json.dumps(
            {"id": msg_id, "method": method, "params": params}
        ).encode()

        loop = asyncio.get_running_loop()
        backoff = COMMAND_BACKOFF_BASE

        for attempt in range(max_attempts):
            try:
                self._sock.sendto(payload, (self.host, self.port))
                deadline = loop.time() + timeout

                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError(
                            f"No response for id={msg_id} from {self.host}:{self.port}"
                        )
                    # Cooperative receive: yields to the event loop every 2 s so
                    # HA task cancellation is never blocked by a hanging socket.
                    try:
                        data, _ = await asyncio.wait_for(
                            loop.sock_recvfrom(self._sock, 4096),
                            timeout=min(remaining, 2.0),
                        )
                    except asyncio.TimeoutError:
                        continue  # keep waiting within the deadline

                    try:
                        response = json.loads(data)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue

                    if response.get("id") != msg_id:
                        continue  # stale packet from another command / device

                    if "error" in response:
                        raise ValueError(
                            f"API error {response['error'].get('code')}: "
                            f"{response['error'].get('message')}"
                        )
                    return response.get("result", {})

            except asyncio.TimeoutError as err:
                if attempt < max_attempts - 1:
                    jitter = random.uniform(
                        -COMMAND_BACKOFF_JITTER, COMMAND_BACKOFF_JITTER
                    )
                    wait = min(backoff + jitter, COMMAND_BACKOFF_MAX)
                    _LOGGER.debug(
                        "Timeout attempt %d/%d for %s@%s – retry in %.1fs",
                        attempt + 1, max_attempts, method, self.host, wait,
                    )
                    await asyncio.sleep(wait)
                    backoff = min(backoff * COMMAND_BACKOFF_FACTOR, COMMAND_BACKOFF_MAX)
                else:
                    raise asyncio.TimeoutError(
                        f"All {max_attempts} attempts timed out for {method}@{self.host}"
                    ) from err

            except OSError as err:
                if attempt < max_attempts - 1:
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    raise

        raise RuntimeError(f"All {max_attempts} attempts failed for {method}")

    # ------------------------------------------------------------------ #
    # Query commands                                                       #
    # ------------------------------------------------------------------ #

    async def get_device_info(self, ble_mac: str = "0", timeout: float = COMMAND_TIMEOUT, max_attempts: int = MAX_RETRIES) -> dict[str, Any]:
        return await self._send_command(METHOD_GET_DEVICE, {"ble_mac": ble_mac}, timeout=timeout, max_attempts=max_attempts)

    async def get_wifi_status(self, timeout: float = COMMAND_TIMEOUT, max_attempts: int = MAX_RETRIES) -> dict[str, Any]:
        return await self._send_command(METHOD_WIFI_STATUS, {"id": 0}, timeout=timeout, max_attempts=max_attempts)

    async def get_ble_status(self, timeout: float = COMMAND_TIMEOUT, max_attempts: int = MAX_RETRIES) -> dict[str, Any]:
        return await self._send_command(METHOD_BLE_STATUS, {"id": 0}, timeout=timeout, max_attempts=max_attempts)

    async def get_bat_status(self, timeout: float = COMMAND_TIMEOUT, max_attempts: int = MAX_RETRIES) -> dict[str, Any]:
        return await self._send_command(METHOD_BAT_STATUS, {"id": 0}, timeout=timeout, max_attempts=max_attempts)

    async def get_pv_status(self, timeout: float = COMMAND_TIMEOUT, max_attempts: int = MAX_RETRIES) -> dict[str, Any]:
        return await self._send_command(METHOD_PV_STATUS, {"id": 0}, timeout=timeout, max_attempts=max_attempts)

    async def get_es_status(self, timeout: float = COMMAND_TIMEOUT, max_attempts: int = MAX_RETRIES) -> dict[str, Any]:
        return await self._send_command(METHOD_ES_STATUS, {"id": 0}, timeout=timeout, max_attempts=max_attempts)

    async def get_es_mode(self, timeout: float = COMMAND_TIMEOUT, max_attempts: int = MAX_RETRIES) -> dict[str, Any]:
        return await self._send_command(METHOD_ES_GET_MODE, {"id": 0}, timeout=timeout, max_attempts=max_attempts)

    async def get_em_status(self, timeout: float = COMMAND_TIMEOUT, max_attempts: int = MAX_RETRIES) -> dict[str, Any]:
        return await self._send_command(METHOD_EM_STATUS, {"id": 0}, timeout=timeout, max_attempts=max_attempts)

    # ------------------------------------------------------------------ #
    # Set commands                                                         #
    # ------------------------------------------------------------------ #

    async def set_es_mode(self, config: dict[str, Any]) -> dict[str, Any]:
        return await self._send_command(
            METHOD_ES_SET_MODE, {"id": 0, "config": config}
        )

    async def set_dod(self, value: int) -> dict[str, Any]:
        value = max(30, min(88, int(value)))
        return await self._send_command(METHOD_DOD_SET, {"id": 0, "value": value})

    async def set_ble_advertising(self, enable: bool) -> dict[str, Any]:
        return await self._send_command(
            METHOD_BLE_ADV, {"enable": 0 if enable else 1}
        )

    async def set_led(self, state: bool) -> dict[str, Any]:
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
# Blocking helper (runs in thread executor)                           #
# ------------------------------------------------------------------ #


def _transact(
    sock: socket.socket,
    payload: bytes,
    host: str,
    port: int,
    msg_id: int,
    timeout: float,
) -> dict[str, Any]:
    """
    Send payload and wait for the matching response.
    Runs in a thread executor — may block up to `timeout` seconds.
    Discards any packets with non-matching IDs (e.g. stale responses).
    """
    deadline = time.monotonic() + timeout
    sock.sendto(payload, (host, port))

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout(f"No response for id={msg_id} from {host}:{port}")

        sock.settimeout(min(remaining, 2.0))
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue

        try:
            response = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        if response.get("id") != msg_id:
            continue  # stale / wrong device response

        if "error" in response:
            raise ValueError(
                f"API error {response['error'].get('code')}: "
                f"{response['error'].get('message')}"
            )
        return response.get("result", {})


# ------------------------------------------------------------------ #
# Device discovery                                                    #
# ------------------------------------------------------------------ #


async def discover_devices(
    port: int = DEFAULT_PORT,
    timeout: float = DISCOVERY_TIMEOUT,
) -> list[dict[str, Any]]:
    """
    Broadcast Marstek.GetDevice and collect responses from all devices.
    Returns a list of device-info dicts.
    """
    loop = asyncio.get_event_loop()
    devices: dict[str, dict[str, Any]] = {}

    result = await loop.run_in_executor(
        None,
        lambda: _discover_blocking(port, timeout),
    )
    return result


def _discover_blocking(port: int, timeout: float) -> list[dict[str, Any]]:
    """Blocking discovery — runs in thread executor."""
    devices: dict[str, dict[str, Any]] = {}

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    payload = json.dumps(
        {"id": 0, "method": METHOD_GET_DEVICE, "params": {"ble_mac": "0"}}
    ).encode()

    deadline = time.monotonic() + timeout
    last_broadcast = 0.0

    try:
        sock.bind(("", port))
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now - last_broadcast >= 2.0:
                try:
                    sock.sendto(payload, ("255.255.255.255", port))
                except Exception:
                    pass
                last_broadcast = now

            remaining = deadline - time.monotonic()
            sock.settimeout(min(remaining, 0.5))
            try:
                data, _ = sock.recvfrom(4096)
                resp = json.loads(data)
                result = resp.get("result", {})
                mac = result.get("ble_mac")
                if mac and mac not in devices:
                    devices[mac] = result
                    _LOGGER.debug("Discovered Marstek: %s", result)
            except socket.timeout:
                pass
            except (json.JSONDecodeError, AttributeError, OSError):
                pass
    finally:
        sock.close()

    return list(devices.values())


async def validate_connection(host: str, port: int) -> dict[str, Any]:
    """Validate connectivity and return device info.

    Tries several commands in order.  If none elicit a valid JSON-RPC
    response, falls back to a raw probe that accepts ANY bytes back.
    This helps diagnose devices with unknown firmware (e.g. Venus E2).

    Returns device-info dict on success, {} when reachable but protocol
    doesn't match, or raises OSError if the device is truly unreachable.
    """
    client = MarstekUDPClient(host, port)
    loop = asyncio.get_running_loop()
    _LOGGER.debug("validate_connection: connecting to %s:%d", host, port)
    try:
        await client.connect()

        # ── Try structured commands ──────────────────────────────────────
        for coro, label in [
            (lambda: client.get_device_info(timeout=5.0, max_attempts=1), "Marstek.GetDevice"),
            (lambda: client.get_bat_status(timeout=5.0, max_attempts=1),  "Bat.GetStatus"),
            (lambda: client.get_es_status(timeout=5.0, max_attempts=1),   "ES.GetStatus"),
        ]:
            try:
                result = await coro()
                _LOGGER.info(
                    "validate_connection %s:%d – success via %s: %s",
                    host, port, label, result,
                )
                return result
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.warning(
                    "validate_connection %s:%d – %s no response: %s",
                    host, port, label, err,
                )

        # ── Raw probe: send a packet and accept ANY bytes back ────────────
        # Logs raw hex so we can identify unknown protocol variants.
        probe = json.dumps(
            {"id": 1, "method": METHOD_GET_DEVICE, "params": {"ble_mac": "0"}}
        ).encode()
        _LOGGER.debug(
            "validate_connection %s:%d – sending raw probe (%d bytes): %s",
            host, port, len(probe), probe.decode(),
        )
        client._sock.sendto(probe, (host, port))
        try:
            raw, addr = await asyncio.wait_for(
                loop.sock_recvfrom(client._sock, 4096), timeout=5.0
            )
            _LOGGER.warning(
                "validate_connection %s:%d – raw response from %s (%d bytes): hex=%s text=%r",
                host, port, addr, len(raw),
                raw[:128].hex(), raw[:128],
            )
            return {}
        except asyncio.TimeoutError:
            pass

        _LOGGER.warning(
            "validate_connection %s:%d – no response on ephemeral source port; "
            "retrying with source port bound to %d (older firmware workaround)",
            host, port, port,
        )
    finally:
        await client.disconnect()

    # ── Retry with source port == dest port (older firmware requirement) ────
    client2 = MarstekUDPClient(host, port, source_port=port)
    try:
        await client2.connect()
        for coro2, label2 in [
            (lambda: client2.get_device_info(timeout=5.0, max_attempts=1), "Marstek.GetDevice"),
            (lambda: client2.get_bat_status(timeout=5.0, max_attempts=1),  "Bat.GetStatus"),
            (lambda: client2.get_es_status(timeout=5.0, max_attempts=1),   "ES.GetStatus"),
        ]:
            try:
                result = await coro2()
                _LOGGER.info(
                    "validate_connection %s:%d – success via %s (source_port=%d): %s",
                    host, port, label2, port, result,
                )
                result["_needs_source_port"] = True
                return result
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.warning(
                    "validate_connection %s:%d – %s no response (source_port=%d): %s",
                    host, port, label2, port, err,
                )
    finally:
        await client2.disconnect()

    _LOGGER.warning(
        "validate_connection %s:%d – no response to any command; "
        "check IP address, UDP port, and network path",
        host, port,
    )
    raise OSError(f"No UDP response from {host}:{port} – device unreachable or wrong port")
