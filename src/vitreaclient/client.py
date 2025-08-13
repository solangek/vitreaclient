"""Vitrea API client implementation."""

from .api import *
import asyncio
from typing import Optional, Dict, Callable, List
from .constants import *

# debug mode
import logging
#logging.basicConfig(level=logging.DEBUG)
_LOGGER = logging.getLogger(__name__)


class VitreaClient(VitreaSocket, EventEmitter):
    """Monitor for Vitrea devices."""
    def __init__(self, host: str, port: int) -> None:
        """Initialize the Vitrea client."""

        VitreaSocket.__init__(self, host, port)
        EventEmitter.__init__(self)
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._new_switch_callback: Optional[Callable[[list], None]] = None
        self._new_cover_callback: Optional[Callable[[list], None]] = None
        self._timer_callbacks: List[Callable[[str, str, int], None]] = []
        self._keepalive = VitreaKeepAliveHandler(self, interval_seconds=19)
        #self._keepalive.set_monitor(self)


    async def _handle_data(self, data: bytes) -> None:
        """Handle incoming data from the Vitrea box."""
        if isinstance(data, bytes):
            message = data.decode('utf-8').strip()
        elif isinstance(data, str):
            message = data.strip()
        else:
            _LOGGER.error(f"_handle_data received unsupported data type: {type(data)}")

        _LOGGER.debug("Received message: %s", message)
        parts = message.split(":")
        response = None
        event = None

        # keep alive response
        if message.startswith("S:PSW:OK"):
            response = VitreaResponseObject(type=VitreaResponse.OK)
            event = VitreaResponse.OK
        if message.startswith("OK"):
            response = VitreaResponseObject(type=VitreaResponse.OK_VITREA)
            event = VitreaResponse.OK_VITREA
        # Scenario Request acknowledgment
        if message.startswith("S:R") and message.endswith(":OK"):
            scenario = parts[1][1:] if len(parts) > 1 else None
            response =  VitreaResponseObject(type=VitreaResponse.SCENARIO_OK, scenario=scenario)
            event = VitreaResponse.SCENARIO_OK
        # Scenario Request error
        if message.startswith("S:R") and message.endswith(":ERROR"):
            scenario = parts[1][1:] if len(parts) > 1 else None
            response =  VitreaResponseObject(type=VitreaResponse.SCENARIO_ERROR, scenario=scenario)
            event = VitreaResponse.SCENARIO_ERROR
        # Node status response
        if message.startswith("S:N"):
            node = parts[1][1:] if len(parts) > 1 else None
            key = parts[2] if len(parts) > 2 else None
            status = parts[3] if len(parts) > 3 else None
            data = parts[4] if len(parts) > 4 else None
            response = VitreaResponseObject(type=VitreaResponse.STATUS, node=node, key=key, status=DeviceStatus(status), data=data)
            event = VitreaResponse.STATUS
        # node joined
        if message.startswith("J:N"):
            node = parts[1][1:] if len(parts) > 1 else None
            response = VitreaResponseObject(type=VitreaResponse.JOINED, node=node)
            event = VitreaResponse.JOINED
        # node left
        if message.startswith("L:N"):
            node = parts[1][1:] if len(parts) > 1 else None
            response = VitreaResponseObject(type=VitreaResponse.LEFT, node=node)
            event = VitreaResponse.LEFT

        if event:
            # _LOGGER.debug(f"Emitting event '{event}' with response: {response}")
            self.emit(event, response)
        else:
            raise ValueError(f"Unknown Vitrea response: {message}")

    # commands to Vitrea box
    async def status_request(self) -> None:
        """Send a status request status of all nodes and keys.
        """
        #_LOGGER.debug("Sending status command to Vitrea box")
        await self.write(VitreaCommand.STATUS.encode())

    async def status_request_node(self, node: str) -> None:
        """Send a status request for a specific node.
        :param node: Node identifier (e.g., '001')
        """
        #_LOGGER.debug(f"Sending status request for node {node}")
        command = VitreaCommand.STATUS_NODE.format(node=node)
        await self.write(command.encode())

    async def status_request_key(self, node: str, key: str) -> None:
        """Send a status request for a specific key.
        :param node: Node identifier (e.g., '001')
        :param key: Key identifier (e.g., '1')
        """
        #_LOGGER.debug(f"Sending status request for key {key} on node {node}")
        command = VitreaCommand.STATUS_KEY.format(node=node, key=key)
        await self.write(command.encode())

    async def key_on(self, node: str, key: str) -> None:
        """Send a key on command.
        :param node: Node identifier (e.g., '001')
        :param key: Key identifier (e.g., '1')
        """
        #_LOGGER.debug(f"Sending key on command for {node}/{key}")
        command = VitreaCommand.KEY_ON.format(node=node, key=key)
        await self.write(command.encode())

    async def key_off(self, node: str, key: str) -> None:
        """Send a key off command.
        :param node: Node identifier (e.g., '001')
        :param key: Key identifier (e.g., '1')
        """
        #_LOGGER.debug(f"Sending key off command for {node}/{key}")
        command = VitreaCommand.KEY_OFF.format(node=node, key=key)
        await self.write(command.encode())

    async def blind_open(self, node: str, key: str) -> None:
        """Send a blind open command.
        :param node: Node identifier (e.g., '001')
        :param key: Key identifier (e.g., '1')
        """
        #_LOGGER.debug(f"Sending blind open command for {node}/{key}")
        command = VitreaCommand.BLIND_OPEN.format(node=node, key=key)
        await self.write(command.encode())

    async def blind_close(self, node: str, key: str) -> None:
        """Send a blind close command.
        :param node: Node identifier (e.g., '001')
        :param key: Key identifier (e.g., '1')
        """
        #_LOGGER.debug(f"Sending blind close command for {node}/{key}")
        command = VitreaCommand.BLIND_CLOSE.format(node=node, key=key)
        await self.write(command.encode())

    async def blind_percent(self, node: str, key: str, percent: int) -> None:
        """Send a blind percent command.
        :param node: Node identifier (e.g., '001')
        :param key: Key identifier (e.g., '1')
        :param percent: Percentage value (0-100)
        """
        #_LOGGER.debug(f"Sending blind percent command for {node}/{key} to {percent}%")
        if percent < 0 or percent > 100:
            _LOGGER.error(f"Invalid blind percentage {percent} for switch {node}/{key}. Must be between 0 and 100.")
            return
        command = VitreaCommand.BLIND_PERCENT.format(node=node, key=key, percent=percent)
        await self.write(command.encode())

    async def blind_stop(self, node: str, key: str) -> None:
        """Send a blind stop command.
        :param node: Node identifier (e.g., '001')
        :param key: Key identifier (e.g., '1')
        """
        #_LOGGER.debug(f"Sending blind stop command for {node}/{key}")
        command = VitreaCommand.BLIND_STOP.format(node=node, key=key)
        await self.write(command.encode())

    async def scenario_on(self, scenario: str) -> None:
        """Send a scenario on command.
        :param scenario: Scenario identifier (5 characters)
        """
        #_LOGGER.debug(f"Sending scenario on command for scenario {scenario}")
        if len(scenario) != 5:
            _LOGGER.error(f"Invalid scenario length: {len(scenario)}. Must be 5 bytes.")
            return
        command = VitreaCommand.SCENARIO_ON.format(scenario=scenario)
        await self.write(command.encode())

    async def set_timer(self, node: str, key: str, minutes: int) -> None:
        """Set a timer for a switch (commonly a boiler).
        :param node: Node identifier (e.g., '001')
        :param key: Key identifier (e.g., '1')
        :param minutes: Timer duration in minutes (0-120)
        """
        #_LOGGER.debug(f"Setting timer for {node}/{key} to {minutes} minutes")
        if minutes < 0 or minutes > 120:
            _LOGGER.error(f"Invalid timer value {minutes} for switch {node}/{key}. Must be between 0 and 120.")
            return
        if minutes == 0:
            command = VitreaCommand.KEY_OFF.format(node=node, key=key)
        else:
            command = VitreaCommand.KEY_ON_WITH_TIMER.format(node=node, key=key, timer=minutes)
        await self.write(command.encode())

    async def send_keepalive(self):
        """Send a keepalive command to the Vitrea device."""
        await self.write(VitreaCommand.KEEP_ALIVE.encode())
