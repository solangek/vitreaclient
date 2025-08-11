"""Vitrea API communication module."""

import asyncio
import socket
from typing import Optional, Union
from dataclasses import dataclass
import threading
from collections import defaultdict

from src.vitreaclient.constants import VitreaResponse

# debug mode
import logging
#logging.basicConfig(level=logging.DEBUG)
_LOGGER = logging.getLogger(__name__)

@dataclass
class VitreaResponseObject:
    type: str
    node: str = None
    key: str = None
    scenario: str = None
    error: str = None
    status: str = None
    extra: dict = None

@dataclass
class ScenarioOkResponse:
    scenario: str

@dataclass
class ScenarioErrorResponse:
    scenario: str

@dataclass
class ErrorResponse:
    node: str
    key: str
    error: str

@dataclass
class OkResponse:
    pass

VitreaAnyResponse = Union[ScenarioOkResponse, ScenarioErrorResponse, ErrorResponse, OkResponse, VitreaResponseObject]

class EventEmitter:
    def __init__(self):
        # list of event listeners where key is a VitreaResponse
        self._events = defaultdict(list)
        self._lock = threading.Lock()

    def on(self, event_name : VitreaResponse, callback):
        with self._lock:
            _LOGGER.debug(f"Registering listener for event '{event_name}'")
            if not callable(callback):
                raise ValueError(f"Callback for event '{event_name}' must be callable")
            self._events[event_name].append(callback)

    def emit(self, event_name: VitreaResponse, *args, **kwargs):
        with self._lock:
            listeners = list(self._events.get(event_name, []))
        for callback in listeners:
            callback(*args, **kwargs)

class VitreaSocket:
    """A class to manage the Vitrea socket connection."""
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.sock: Optional[socket.socket] = None
        self._socket: Optional[socket.socket] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._keepalive: Optional[VitreaKeepAliveHandler] = None
        #self._event_listeners: Dict[str, List[Callable]] = {}
        self._read_task: Optional[asyncio.Task] = None
        self._mutex = asyncio.Lock()
        self._reconnect_attempt = 0
        self._max_reconnect_attempts = None  # None means unlimited attempts
        self._reconnect_delay = 5  # seconds
        self._connection_state = asyncio.Event()  # To track connection state

    def _create_new_socket(self) -> socket.socket:
        return socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    async def connect(self) -> None:
        async with self._mutex:
            if self._writer is not None and not self._writer.is_closing():
                _LOGGER.debug("Already connected to Vitrea box")
                return  # Already connected
            await self._reconnect()

    def is_connected(self) -> bool:
        """Check if the socket is connected."""
        return self._connection_state.is_set() and self._writer is not None and not self._writer.is_closing()

    async def _reconnect(self) -> None:
        self._reconnect_attempt = 0
        while self._max_reconnect_attempts is None or self._reconnect_attempt < self._max_reconnect_attempts:
            try:
                await self._cleanup_connection()
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=10
                )
                self._socket = self._writer.get_extra_info('socket')
                self.sock = self._socket
                _LOGGER.debug(f"Connected to Vitrea box at {self.host}:{self.port}")
                self._connection_state.set()  # Mark as connected
                if self._keepalive:
                    self._keepalive.enable()
                self._reconnect_attempt = 0
                return
            except (asyncio.TimeoutError, ConnectionError, OSError) as e:
                self._reconnect_attempt += 1
                self._connection_state.clear()  # Mark as disconnected
                if self._max_reconnect_attempts is not None and self._reconnect_attempt >= self._max_reconnect_attempts:
                    _LOGGER.error(
                        f"Failed to connect to Vitrea box after {self._max_reconnect_attempts} attempts: {e}"
                    )
                    raise ConnectionError(f"Failed to connect to Vitrea box: {e}")
                delay = self._reconnect_delay * (2 ** (self._reconnect_attempt - 1))
                _LOGGER.warning(
                    f"Connection attempt {self._reconnect_attempt} failed, retrying in {delay} seconds: {e}"
                )
                await asyncio.sleep(delay)
            except Exception as e:
                _LOGGER.error(f"Unexpected error connecting to Vitrea box: {e}")
                self._connection_state.clear()  # Mark as disconnected
                await self._cleanup_connection()
                raise

    async def start_read_task(self) -> None:
        if self._reader is None:
            await self.connect()
        if self._read_task is None or self._read_task.done():
            self._read_task = asyncio.create_task(self._read_loop())
            _LOGGER.debug("Read loop task started")

    async def _cleanup_connection(self) -> None:
        if self._read_task is not None and not self._read_task.done():
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception as e:
                _LOGGER.debug(f"Error closing writer: {e}")
            self._writer = None
            self._reader = None
        self._connection_state.clear()
        self.sock = None
        self._socket = None

    def disconnect(self) -> None:
        if self._socket is not None:
            _LOGGER.info("Forced a disconnection")
            if self._keepalive is not None:
                self._keepalive.pause()
            asyncio.create_task(self._cleanup_connection())

    async def write(self, data: bytes) -> None:
        async with self._mutex:
            if self._writer is None or self._writer.is_closing():
                _LOGGER.debug("Writer is None or closing, attempting to reconnect")
                if self._keepalive is not None:
                    self._keepalive.pause()
                try:
                    await self.connect()
                except Exception as e:
                    _LOGGER.error(f"Failed to reconnect: {e}")
                    return
                if self._writer is None or self._writer.is_closing():
                    _LOGGER.error("Still no valid connection after reconnect attempt")
                    return
            try:
                self._writer.write(data)
                await self._writer.drain()
            except Exception as e:
                _LOGGER.error(f"Data written with an error - {e}")
                self._writer = None

    async def _read_loop(self) -> None:
        _LOGGER.debug("Starting VitreaSocket _read_loop")
        try:
            while self._reader is not None and not self._reader.at_eof():
                _LOGGER.debug("Waiting to read from socket...")
                data = await self._reader.read(4096)

                if not data:
                    _LOGGER.debug("No data received, breaking read loop.")
                    break
                if b'\r\n' in data:
                    lines = data.split(b'\r\n')
                    for line in lines:
                        if line:
                            _LOGGER.debug(f"Received: {line}")
                            await self._handle_data(line)
                if not data.endswith(b'\r\n'):
                    _LOGGER.debug(f"Need to handle extra data: {data}")
        except asyncio.CancelledError:
            _LOGGER.debug("Read loop cancelled")
            pass
        except Exception as e:
            _LOGGER.error(f"Error in read loop: {e}")
            await self._cleanup_connection()
            try:
                await self.connect()
                _LOGGER.debug("Connection established, restarting read loop")
                self._read_task = asyncio.create_task(self._read_loop())
            except Exception as ex:
                _LOGGER.error("Failed to restart read loop: %s", ex)

    async def _handle_data(self, data: bytes) -> None:
        """Handle incoming data from socket. To be implemented by subclasses."""
        _LOGGER.debug("Received data: %s", data)
        pass

class VitreaKeepAliveHandler:
    def __init__(self, monitor = None, interval_seconds: int = 30):
        """Initialize keepalive handler."""
        self.monitor = monitor
        self.interval = interval_seconds
        self.enabled = False
        self._task: Optional[asyncio.Task] = None
        self._connection_lost_count = 0
        self._max_failures = 3

    def set_monitor(self, monitor):
        """Set the monitor after initialization."""
        self.monitor = monitor

    def enable(self) -> None:
        """Enable keepalive."""
        if not self.enabled:
            self.enabled = True
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._keepalive_loop())

    def disable(self) -> None:
        """Disable keepalive."""
        if self.enabled:
            self.enabled = False
            self._cancel_task()

    def reset(self) -> None:
        """Reset keepalive timer."""
        if self.enabled:
            self._cancel_task()
            self._start_task()

    def pause(self) -> None:
        """Pause keepalive task without disabling."""
        self._cancel_task()

    def resume(self) -> None:
        """Resume paused keepalive task."""
        if self.enabled and (not self._task or self._task.done()):
            self._start_task()

    def _start_task(self) -> None:
        """Start keepalive task."""
        if not self._task or self._task.done():
            try:
                # Create the task and save a reference to it
                self._task = asyncio.create_task(self._keepalive_loop())

                # Add a done callback to check if task completes or fails
                def _task_done_callback(task):
                    try:
                        if task.cancelled():
                            _LOGGER.debug("KeepAlive task was cancelled")
                        elif task.exception():
                            _LOGGER.error("KeepAlive task failed with exception: %s", task.exception())
                        else:
                            _LOGGER.debug("KeepAlive task completed normally")
                    except asyncio.CancelledError:
                        pass

                self._task.add_done_callback(_task_done_callback)
                _LOGGER.debug("KeepAlive task started")
            except Exception as e:
                _LOGGER.exception("Error starting keepalive task: %s", e)
                self._task = None

    def _cancel_task(self) -> None:
        """Cancel keepalive task."""
        if self._task and not self._task.done():
            _LOGGER.debug("Cancelling keepalive task")
            self._task.cancel()

    async def _keepalive_loop(self):
        """Keepalive loop."""
        while self.enabled:
            try:
                if self.monitor:
                    await self.monitor.send_keepalive()
                await asyncio.sleep(self.interval)
            except Exception as ex:
                _LOGGER.error("Keepalive error: %s", ex)
                self._connection_lost_count += 1
                if self._connection_lost_count >= self._max_failures:
                    _LOGGER.error("Max keepalive failures reached, disabling keepalive.")
                    self.enabled = False

    def restart(self):
        """Restart the keepalive handler."""
        _LOGGER.debug("Restarting keepalive handler")
        self.disable()
        self.enable()
