"""Vitrea API communication module."""

import asyncio
import socket
from typing import Optional
from dataclasses import dataclass
import threading
from collections import defaultdict

from .constants import VitreaResponse, DeviceStatus

# Minimal logging setup - let the application configure logging
import logging
_LOGGER = logging.getLogger(__name__)

@dataclass
class VitreaResponseObject:
    type: VitreaResponse = None
    node: str = None
    key: str = None
    scenario: str = None
    error: str = None
    status: DeviceStatus = None
    data: dict = None


class ConnectionUtils:
    """Shared utilities for connection management and error handling.
    Jitter and backoff logic adapted from:
    https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/

    When multiple clients experience connection failures simultaneously
    - Without jitter, they would all retry at exactly the same times
    - This creates traffic spikes that can overwhelm a recovering server
    - Bidirectional jitter (±10%) spreads retries across time
    """

    @staticmethod
    def calculate_backoff_delay(attempt: int, base_delay: int = 1, max_delay: int = 60) -> float:
        """Calculate exponential backoff delay with jitter.

        Args:
            attempt: The attempt number (1-based)
            base_delay: Base delay in seconds
            max_delay: Maximum delay in seconds

        Returns:
            Delay in seconds with jitter applied
        """
        import random
        delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
        # Add 10% jitter to prevent thundering herd
        jitter = delay * 0.1
        return delay + random.uniform(-jitter, jitter)

    @staticmethod
    def is_socket_error(exception: Exception) -> bool:
        """Check if an exception is a socket-related error."""
        return isinstance(exception, (ConnectionResetError, ConnectionAbortedError,
                                    BrokenPipeError, OSError, ConnectionError))

    @staticmethod
    def log_connection_error(logger, error_type: str, attempt: int, exception: Exception, max_attempts: int = None):
        """Standardized logging for connection errors."""
        if max_attempts:
            logger.warning(f"{error_type} (attempt {attempt}/{max_attempts}): {exception}")
        else:
            logger.warning(f"{error_type} (attempt {attempt}): {exception}")

    @staticmethod
    def log_reconnection_success(logger, context: str):
        """Standardized logging for successful reconnections."""
        logger.info(f"{context}: Connection re-established successfully")

    @staticmethod
    def should_continue_retrying(attempt: int, max_attempts: Optional[int], should_maintain: bool) -> bool:
        """Determine if retrying should continue."""
        if not should_maintain:
            return False
        if max_attempts is None:
            return True
        return attempt < max_attempts


class EventEmitter:
    def __init__(self):
        # list of event listeners where key is a VitreaResponse
        self._events = defaultdict(list)
        self._lock = threading.Lock()

    def on(self, event_name : VitreaResponse, callback):
        """Register a listener for an event."""
        with self._lock:
            _LOGGER.debug(f"Registering listener for event '{event_name}'")
            if not callable(callback):
                raise ValueError(f"Callback for event '{event_name}' must be callable")
            self._events[event_name].append(callback)

    def off(self, event_name: VitreaResponse, callback):
        """Unregister a listener for an event."""
        with self._lock:
            _LOGGER.debug(f"Unregistering listener for event '{event_name}'")
            if event_name in self._events:
                try:
                    self._events[event_name].remove(callback)
                except ValueError:
                    _LOGGER.warning(f"Callback not found for event '{event_name}'")
            else:
                _LOGGER.warning(f"No listeners registered for event '{event_name}'")

    def on_once(self, event_name: VitreaResponse, callback):
        """Register a one-time listener for an event."""
        def wrapper(*args, **kwargs):
            callback(*args, **kwargs)
            self.off(event_name, wrapper)

        self.on(event_name, wrapper)

    def emit(self, event_name: VitreaResponse, *args, **kwargs):
        """Emit an event to all registered listeners."""
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
        self._read_task: Optional[asyncio.Task] = None
        self._mutex = asyncio.Lock()
        self._reconnect_mutex = asyncio.Lock()  # Separate mutex for reconnection
        self._state_mutex = asyncio.Lock()  # New mutex for state synchronization
        self._reconnect_attempt = 0
        self._max_reconnect_attempts = None  # None means unlimited attempts
        self._reconnect_delay = 5  # seconds
        self._connection_state = asyncio.Event()  # To track connection state
        self._is_reconnecting = False  # Flag to prevent multiple simultaneous reconnections
        self._should_maintain_connection = True  # Flag to control automatic reconnection

    def _create_new_socket(self) -> socket.socket:
        return socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    async def connect(self, force_enable_maintenance: bool = True) -> None:
        # Only re-enable connection maintenance when explicitly requested (default True for backward compatibility)
        # This allows internal reconnection logic to preserve the current maintenance state
        if force_enable_maintenance:
            self._should_maintain_connection = True

        if self._writer is not None and not self._writer.is_closing():
            _LOGGER.debug("Already connected to Vitrea box")
            return  # Already connected

        # Call _reconnect without holding mutex to prevent deadlock
        await self._reconnect()

    async def connect_with_coordination(self, caller_context: str = "Unknown") -> bool:
        """Attempt connection with proper coordination for external callers like keepalive.

        Args:
            caller_context: Context string for logging (e.g., "Keepalive", "ReadLoop")

        Returns:
            bool: True if connection was established, False if skipped due to ongoing reconnection
        """
        # Check if reconnection is already in progress without holding main mutex
        if self._is_reconnecting:
            _LOGGER.debug(f"{caller_context}: Reconnection already in progress, skipping attempt")
            return False

        # Check if already connected
        if self._writer is not None and not self._writer.is_closing():
            _LOGGER.debug(f"{caller_context}: Already connected, skipping attempt")
            return False

        # Use main mutex to ensure coordination across all connection attempts
        async with self._mutex:
            try:
                # Re-enable connection maintenance when connect is explicitly called
                self._should_maintain_connection = True
                await self._reconnect()
                _LOGGER.debug(f"{caller_context}: Successfully triggered reconnection")
                return True
            except Exception as e:
                _LOGGER.debug(f"{caller_context}: Connection attempt failed: {e}")
                return False

    def is_connected(self) -> bool:
        """Check if the socket is connected with proper synchronization."""
        # More thorough connection check
        if not self._connection_state.is_set():
            return False
        if self._writer is None or self._writer.is_closing():
            return False
        if self._reader is None or self._reader.at_eof():
            return False
        # Check if the read task is still running
        if self._read_task is None or self._read_task.done():
            return False
        return True

    async def is_reconnecting(self) -> bool:
        """Check if reconnection is in progress with proper synchronization."""
        async with self._state_mutex:
            return self._is_reconnecting

    async def _set_reconnecting_state(self, state: bool) -> None:
        """Set reconnection state with proper synchronization."""
        async with self._state_mutex:
            self._is_reconnecting = state

    async def _reconnect(self) -> None:
        # First check if we should proceed without holding any locks
        if not self._should_maintain_connection:
            _LOGGER.debug("Connection maintenance disabled, skipping reconnection")
            return

        # Check reconnection state first without acquiring mutex
        if self._is_reconnecting:
            _LOGGER.debug("Reconnection already in progress, skipping this attempt")
            return

        # Set reconnection state immediately to prevent race conditions
        self._is_reconnecting = True

        try:
            self._reconnect_attempt = 0

            while (self._max_reconnect_attempts is None or self._reconnect_attempt < self._max_reconnect_attempts) and self._should_maintain_connection:
                self._reconnect_attempt += 1
                try:
                    _LOGGER.debug(f"Reconnection attempt {self._reconnect_attempt}")

                    # Clean up any existing connection without holding mutex
                    await self.cleanup_connection()

                    # Attempt connection with timeout
                    self._reader, self._writer = await asyncio.wait_for(
                        asyncio.open_connection(self.host, self.port),
                        timeout=10
                    )

                    self._socket = self._writer.get_extra_info('socket')
                    self.sock = self._socket
                    _LOGGER.debug(f"Connected to Vitrea box at {self.host}:{self.port}")
                    self._connection_state.set()  # Mark as connected

                    # Start read task - this must not hold mutex to prevent deadlock
                    await self.start_read_task()

                    # Enable keepalive after successful connection
                    if self._keepalive and not self._keepalive.enabled:
                        await self._keepalive.enable()
                        _LOGGER.debug("Keepalive enabled for new connection")

                    # Re-enable keepalive after successful reconnection
                    if self._keepalive:
                        await self._keepalive.resume_after_reconnection()

                    self._reconnect_attempt = 0
                    _LOGGER.info("Reconnection successful")
                    return

                except (asyncio.TimeoutError, ConnectionError, OSError) as e:
                    self._connection_state.clear()  # Mark as disconnected
                    _LOGGER.warning(f"Connection attempt {self._reconnect_attempt} failed: {e}")

                    # Check if maintenance was disabled during the attempt
                    if not self._should_maintain_connection:
                        _LOGGER.debug("Connection maintenance disabled during reconnection, stopping")
                        return

                    if self._max_reconnect_attempts is not None and self._reconnect_attempt >= self._max_reconnect_attempts:
                        _LOGGER.error(f"Failed to connect to Vitrea box after {self._max_reconnect_attempts} attempts: {e}")
                        raise ConnectionError(f"Failed to connect to Vitrea box: {e}")

                    # Calculate delay and sleep between attempts
                    delay = ConnectionUtils.calculate_backoff_delay(self._reconnect_attempt, self._reconnect_delay)
                    _LOGGER.warning(f"Connection attempt {self._reconnect_attempt} failed, retrying in {delay} seconds: {e}")
                    await asyncio.sleep(delay)

                except Exception as e:
                    _LOGGER.error(f"Unexpected error connecting to Vitrea box: {e}")
                    self._connection_state.clear()  # Mark as disconnected
                    await self.cleanup_connection()
                    raise
        finally:
            # Always reset the reconnecting flag
            self._is_reconnecting = False

    async def start_read_task(self) -> None:
        # Use mutex to prevent race conditions when starting read tasks
        async with self._mutex:
            # Cancel any existing task first to ensure clean state
            if self._read_task is not None and not self._read_task.done():
                _LOGGER.debug("Cancelling existing read task before starting new one")
                self._read_task.cancel()
                try:
                    await self._read_task
                except asyncio.CancelledError:
                    pass
                self._read_task = None

            # Log warning if no reader, but don't start task without reader
            if self._reader is None:
                _LOGGER.warning("Cannot start read task - no reader available")
                return

            # Create new read task
            self._read_task = asyncio.create_task(self._read_loop())
            _LOGGER.debug("Read loop task started")

    async def cleanup_connection(self) -> None:
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

    async def disconnect(self) -> None:
        """Disconnect from the Vitrea box and stop all operations."""
        _LOGGER.info("Initiating disconnect")
        self._should_maintain_connection = False  # Stop automatic reconnection

        if self._keepalive is not None:
            await self._keepalive.disable()  # Use async disable for consistency

        await self.cleanup_connection()

    async def close(self) -> None:
        """Properly close the client and all resources."""
        _LOGGER.info("Closing VitreaSocket")
        self._should_maintain_connection = False

        if self._keepalive is not None:
            await self._keepalive.disable()  # Use async disable

        await self.cleanup_connection()

    async def write(self, data: bytes) -> None:
        async with self._mutex:
            if self._writer is None or self._writer.is_closing():
                _LOGGER.debug("Writer is None or closing, attempting to reconnect")
                if self._keepalive is not None:
                    await self._keepalive.pause()
                try:
                    await self.connect()
                except Exception as e:
                    _LOGGER.error(f"Failed to reconnect: {e}")
                    raise
                if self._writer is None or self._writer.is_closing():
                    _LOGGER.error("Still no valid connection after reconnect attempt")
                    raise ConnectionError("Unable to establish connection for write operation")

            retry_count = 0
            max_retries = 3

            while retry_count < max_retries:
                try:
                    self._writer.write(data)
                    await self._writer.drain()
                    return  # Success, exit the method
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError) as e:
                    _LOGGER.warning(f"Socket error during write (attempt {retry_count + 1}): {e}")
                    retry_count += 1
                    self._connection_state.clear()  # Mark as disconnected

                    if retry_count < max_retries:
                        _LOGGER.debug("Attempting to reconnect after write error...")
                        if self._keepalive is not None:
                            await self._keepalive.pause()
                        try:
                            await self.cleanup_connection()
                            # Don't force re-enable maintenance - preserve current state during write retry
                            await self.connect(force_enable_maintenance=False)
                        except Exception as reconnect_error:
                            _LOGGER.error(f"Reconnection failed (attempt {retry_count}): {reconnect_error}")
                            if retry_count == max_retries - 1:
                                raise ConnectionError(f"Failed to write data after {max_retries} attempts: {e}")
                    else:
                        raise ConnectionError(f"Failed to write data after {max_retries} attempts: {e}")
                except Exception as e:
                    _LOGGER.error(f"Unexpected error during write: {e}")
                    self._connection_state.clear()
                    raise

    async def _read_loop(self) -> None:
        _LOGGER.debug("Starting VitreaSocket _read_loop")

        # Buffer to accumulate partial messages
        buffer = b''

        while self._should_maintain_connection:
            try:
                # Wait for reader to become available if it's not set yet
                if self._reader is None:
                    _LOGGER.debug("Read loop waiting for connection to be established...")
                    await asyncio.sleep(0.1)  # Brief wait before checking again
                    continue

                while self._reader is not None and not self._reader.at_eof():
                    try:
                        data = await self._reader.read(4096)
                    except asyncio.IncompleteReadError as e:
                        # Handle partial read
                        data = e.partial
                        if not data:
                            _LOGGER.debug("Incomplete read with no data, connection may be closed")
                            raise ConnectionError("Incomplete read, connection closed")

                    if not data:
                        _LOGGER.debug("No data received, connection may be closed.")
                        raise ConnectionError("No data received, connection closed")

                    # Add received data to buffer
                    buffer += data

                    # Process complete messages (ending with \r\n)
                    while b'\r\n' in buffer:
                        line, buffer = buffer.split(b'\r\n', 1)
                        if line:  # Only process non-empty lines
                            try:
                                await self._handle_data(line)
                            except Exception as handle_error:
                                _LOGGER.error(f"Error handling received data: {handle_error}")
                                # Continue processing other messages instead of failing completely

                    # Check if buffer is getting too large (prevent memory issues)
                    if len(buffer) > 65536:  # 64KB limit
                        _LOGGER.warning(f"Buffer size exceeded limit, clearing buffer. Lost data: {buffer[:100]}...")
                        buffer = b''

                # If we reach here, the loop ended normally (EOF)
                _LOGGER.debug("Read loop ended normally (EOF)")
                if self._should_maintain_connection:
                    _LOGGER.debug("Attempting reconnection after EOF")
                    raise ConnectionError("Connection ended (EOF)")
                else:
                    break

            except asyncio.CancelledError:
                _LOGGER.debug("Read loop cancelled")
                break

            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError, ConnectionError) as e:
                _LOGGER.warning(f"Socket error in read loop: {e}")
                # Clear buffer on connection error to start fresh
                buffer = b''

                # Trigger reconnection and exit this read loop
                if self._should_maintain_connection:
                    _LOGGER.debug("Triggering reconnection from read loop")
                    # Schedule reconnection task without blocking this loop
                    asyncio.create_task(self._trigger_reconnection())
                break

            except Exception as e:
                _LOGGER.error(f"Unexpected error in read loop: {e}")
                # Clear buffer on unexpected error to prevent corruption
                buffer = b''

                # Trigger reconnection and exit this read loop
                if self._should_maintain_connection:
                    _LOGGER.debug("Triggering reconnection from read loop due to unexpected error")
                    # Schedule reconnection task without blocking this loop
                    asyncio.create_task(self._trigger_reconnection())
                break

        _LOGGER.debug("Read loop exited")

    async def _trigger_reconnection(self):
        """Trigger reconnection in a separate task to avoid blocking the read loop."""
        reconnection_id = id(self)  # Unique ID for this reconnection attempt
        _LOGGER.info(f"[{reconnection_id}] Starting triggered reconnection process...")

        try:
            await asyncio.sleep(1)  # Brief delay before reconnection

            # Double-check if we should still reconnect
            if not self._should_maintain_connection:
                _LOGGER.debug(f"[{reconnection_id}] Connection maintenance disabled, skipping reconnection")
                return

            if self.is_connected():
                _LOGGER.debug(f"[{reconnection_id}] Already connected, skipping reconnection")
                return

            _LOGGER.info(f"[{reconnection_id}] Executing triggered reconnection...")
            await self._reconnect()
            _LOGGER.info(f"[{reconnection_id}] Triggered reconnection completed successfully")

        except Exception as e:
            _LOGGER.error(f"[{reconnection_id}] Error during triggered reconnection: {e}")
            # If reconnection fails, try multiple times with increasing delays
            for retry in range(3):
                if not self._should_maintain_connection or self.is_connected():
                    break

                delay = (retry + 1) * 3  # 3, 6, 9 seconds
                _LOGGER.info(f"[{reconnection_id}] Reconnection attempt {retry + 1} failed, retrying in {delay} seconds...")
                await asyncio.sleep(delay)

                try:
                    await self._reconnect()
                    _LOGGER.info(f"[{reconnection_id}] Reconnection retry {retry + 1} succeeded")
                    return
                except Exception as retry_error:
                    _LOGGER.warning(f"[{reconnection_id}] Reconnection retry {retry + 1} failed: {retry_error}")

            _LOGGER.error(f"[{reconnection_id}] All reconnection attempts failed")

    async def _handle_read_error(self, error_type: str, error: Exception) -> bool:
        """Handle read loop errors with proper reconnection logic.

        Returns:
            bool: True if should continue read loop, False if should exit
        """
        self._connection_state.clear()  # Mark as disconnected

        if not self._should_maintain_connection:
            _LOGGER.debug("Connection maintenance disabled, exiting read loop")
            return False

        # Exit this read loop - let the main reconnection logic handle it
        # This prevents multiple read loops from being created
        _LOGGER.debug(f"Read loop exiting due to {error_type}, main connection logic will handle reconnection")
        return False

    async def _handle_data(self, data: bytes) -> None:
        """Handle incoming data from socket. To be implemented by subclasses."""
        _LOGGER.debug("Received data: %s", data)
        pass

class VitreaKeepAliveHandler:
    def __init__(self, monitor = None, interval_seconds: int = 19):
        """Initialize keepalive handler.

        Args:
            monitor: The socket monitor to send keepalives to
            interval_seconds: Keepalive interval in seconds (default 19 to prevent connection loss)
        """
        self.monitor = monitor
        self.interval = interval_seconds
        self.enabled = False
        self._task: Optional[asyncio.Task] = None
        self._connection_lost_count = 0
        self._max_failures = 3
        self._task_lock = asyncio.Lock()  # Protect task management operations
        # Removed duplicate _is_reconnecting flag - use monitor's flag instead
        self._should_stop = False  # Clean shutdown flag

    def set_monitor(self, monitor):
        """Set the monitor after initialization."""
        self.monitor = monitor

    async def enable(self) -> None:
        """Enable keepalive with proper race condition protection."""
        async with self._task_lock:
            if not self.enabled:
                self.enabled = True
                self._should_stop = False
                await self._ensure_task_running()

    async def disable(self) -> None:
        """Disable keepalive with proper cleanup."""
        async with self._task_lock:
            if self.enabled:
                self.enabled = False
                self._should_stop = True
                await self._cancel_task()

    def disable_sync(self) -> None:
        """Synchronous disable for compatibility - creates async task."""
        if self.enabled:
            self.enabled = False
            self._should_stop = True
            # Create a task to handle the async cleanup
            asyncio.create_task(self._async_disable_cleanup())

    async def _async_disable_cleanup(self) -> None:
        """Handle async cleanup for sync disable."""
        async with self._task_lock:
            await self._cancel_task()

    async def reset(self) -> None:
        """Reset keepalive timer with race condition protection."""
        async with self._task_lock:
            if self.enabled:
                await self._cancel_task()
                await self._ensure_task_running()

    async def pause(self) -> None:
        """Pause keepalive task without disabling."""
        async with self._task_lock:
            await self._cancel_task()

    async def resume(self) -> None:
        """Resume paused keepalive task."""
        async with self._task_lock:
            if self.enabled and (not self._task or self._task.done()):
                await self._ensure_task_running()

    async def resume_after_reconnection(self) -> None:
        """Resume keepalive after successful reconnection with proper coordination."""
        async with self._task_lock:
            if self.enabled:
                # Cancel any existing task first
                await self._cancel_task()
                # Reset failure counter after successful reconnection
                self._connection_lost_count = 0
                # Start fresh keepalive task
                await self._ensure_task_running()
                _LOGGER.debug("Keepalive resumed after successful reconnection")

    async def _ensure_task_running(self) -> None:
        """Ensure keepalive task is running. Must be called with _task_lock held."""
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
                            # Automatically restart task if it failed and we're still enabled
                            if self.enabled and not self._should_stop:
                                _LOGGER.info("Restarting failed keepalive task")
                                asyncio.create_task(self._restart_task_after_failure())
                        else:
                            _LOGGER.debug("KeepAlive task completed normally")
                    except asyncio.CancelledError:
                        pass

                self._task.add_done_callback(_task_done_callback)
                _LOGGER.debug("KeepAlive task started")
            except Exception as e:
                _LOGGER.exception("Error starting keepalive task: %s", e)
                self._task = None

    async def _restart_task_after_failure(self) -> None:
        """Restart task after failure with proper locking."""
        try:
            async with self._task_lock:
                if self.enabled and not self._should_stop:
                    await asyncio.sleep(5)  # Brief delay before restart
                    await self._ensure_task_running()
        except Exception as e:
            _LOGGER.error(f"Failed to restart keepalive task: {e}")

    async def _cancel_task(self) -> None:
        """Cancel keepalive task. Must be called with _task_lock held."""
        if self._task and not self._task.done():
            _LOGGER.debug("Cancelling keepalive task")
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _keepalive_loop(self):
        """Keepalive loop with bulletproof connection error handling."""
        consecutive_failures = 0
        _LOGGER.debug("Keepalive loop started")

        while self.enabled and not self._should_stop:
            try:
                # Check if we should continue running
                if self._should_stop:
                    break

                if self.monitor:
                    # Prevent race conditions with main reconnection logic using proper async check
                    if hasattr(self.monitor, 'is_reconnecting') and await self.monitor.is_reconnecting():
                        _LOGGER.debug("Main reconnection in progress, keepalive waiting...")
                        # Use shorter wait to avoid interfering with keepalive timing
                        await asyncio.sleep(0.5)  # Brief wait instead of 2 seconds
                        continue

                    # Check connection state and attempt keepalive or reconnection
                    if not self.monitor.is_connected():
                        await self._handle_disconnection(consecutive_failures)
                        consecutive_failures += 1
                    else:
                        # Connection appears good, try to send keepalive
                        keepalive_success = await self._send_keepalive_safely()
                        if keepalive_success:
                            consecutive_failures = 0  # Reset on successful keepalive
                            _LOGGER.debug("Keepalive sent successfully")
                        else:
                            consecutive_failures += 1
                            # Handle keepalive failure with backoff to prevent tight loop
                            if consecutive_failures <= 3:
                                # For first few failures, try reconnection
                                try:
                                    success = await self.monitor.connect_with_coordination("Keepalive")
                                    if success:
                                        ConnectionUtils.log_reconnection_success(_LOGGER, "Keepalive")
                                        consecutive_failures = 0  # Reset on successful reconnection
                                except Exception as reconnect_error:
                                    _LOGGER.debug(f"Keepalive reconnection failed: {reconnect_error}")

                            # Apply backoff delay to prevent tight retry loop
                            backoff_delay = ConnectionUtils.calculate_backoff_delay(
                                consecutive_failures, base_delay=1, max_delay=10
                            )
                            await asyncio.sleep(backoff_delay)
                            continue

                # Use conservative jitter calculation to ensure keepalive is sent within required timeframe
                base_interval = self.interval
                # Limit jitter to ensure we never exceed the critical timing requirement
                # Use negative jitter only to send keepalives earlier, not later
                max_jitter = min(2.0, self.interval * 0.05)  # Maximum 5% jitter, capped at 2 seconds
                import random
                # Only subtract jitter to ensure we're always early, never late
                actual_interval = base_interval - random.uniform(0, max_jitter)

                await asyncio.sleep(actual_interval)

            except asyncio.CancelledError:
                _LOGGER.debug("Keepalive loop cancelled")
                break

            except Exception as ex:
                consecutive_failures += 1

                # Use shared utilities for error handling
                if ConnectionUtils.is_socket_error(ex):
                    ConnectionUtils.log_connection_error(_LOGGER, "Socket error in keepalive", consecutive_failures, ex)
                    await self._handle_socket_error(consecutive_failures)
                else:
                    _LOGGER.error(f"Unexpected keepalive error (attempt {consecutive_failures}): {ex}")
                    # Use ConnectionUtils for backoff calculation
                    backoff_delay = ConnectionUtils.calculate_backoff_delay(consecutive_failures, base_delay=2, max_delay=30)
                    await asyncio.sleep(backoff_delay)

        _LOGGER.debug("Keepalive loop exited")

    async def _send_keepalive_safely(self) -> bool:
        """Send keepalive with proper error isolation.

        Returns:
            bool: True if keepalive was sent successfully, False otherwise
        """
        try:
            await self.monitor.send_keepalive()
            return True
        except Exception as e:
            # Use shared utility for error classification
            if ConnectionUtils.is_socket_error(e):
                _LOGGER.debug(f"Socket error during keepalive send: {e}")
            else:
                _LOGGER.warning(f"Keepalive send failed with unexpected error: {e}")

            # Mark connection as potentially lost
            if hasattr(self.monitor, '_connection_state'):
                self.monitor._connection_state.clear()
            return False

    async def _handle_disconnection(self, consecutive_failures: int) -> None:
        """Handle detected disconnection with proper coordination."""
        _LOGGER.warning(f"Connection lost detected by keepalive (failure #{consecutive_failures + 1})")

        # Avoid interfering with main reconnection logic using proper async check
        if hasattr(self.monitor, 'is_reconnecting') and await self.monitor.is_reconnecting():
            _LOGGER.debug("Main reconnection already in progress, keepalive backing off")
            backoff_delay = ConnectionUtils.calculate_backoff_delay(consecutive_failures, base_delay=5, max_delay=30)
            await asyncio.sleep(backoff_delay)
            return

        # Attempt coordinated reconnection
        try:
            success = await self.monitor.connect_with_coordination("Keepalive")
            if success:
                ConnectionUtils.log_reconnection_success(_LOGGER, "Keepalive")
            else:
                _LOGGER.debug("Keepalive reconnection skipped - handled by main logic")
        except Exception as reconnect_error:
            _LOGGER.debug(f"Keepalive reconnection failed: {reconnect_error}")
            # Don't log as error since main reconnection logic will handle it

            # Use shared utility for backoff calculation
            backoff_delay = ConnectionUtils.calculate_backoff_delay(consecutive_failures, base_delay=5)
            _LOGGER.debug(f"Keepalive backing off for {backoff_delay:.1f} seconds")
            await asyncio.sleep(backoff_delay)

    async def _handle_socket_error(self, consecutive_failures: int) -> None:
        """Handle socket errors during keepalive operations."""
        # Mark connection as lost
        if hasattr(self.monitor, '_connection_state'):
            self.monitor._connection_state.clear()

        # Don't immediately try to reconnect if main logic is already handling it using proper async check
        if hasattr(self.monitor, 'is_reconnecting') and await self.monitor.is_reconnecting():
            _LOGGER.debug("Main reconnection handling socket error, keepalive waiting")
            await asyncio.sleep(10)
            return

        # Try to recover from socket error with coordination
        if self.monitor:
            try:
                success = await self.monitor.connect_with_coordination("Keepalive")
                if success:
                    ConnectionUtils.log_reconnection_success(_LOGGER, "Keepalive")
            except Exception as reconnect_error:
                _LOGGER.debug(f"Keepalive socket error recovery failed: {reconnect_error}")

        # Use shared utility for backoff calculation
        backoff_delay = ConnectionUtils.calculate_backoff_delay(consecutive_failures, base_delay=5)
        await asyncio.sleep(backoff_delay)

    def restart(self):
        """Restart the keepalive handler (legacy sync method)."""
        _LOGGER.debug("Restarting keepalive handler")
        asyncio.create_task(self._restart_async())

    async def _restart_async(self):
        """Async restart implementation."""
        await self.disable()
        await self.enable()
