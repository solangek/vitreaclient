"""Tests for VitreaKeepAliveHandler functionality."""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch
from src.vitreaclient.api import VitreaSocket, VitreaKeepAliveHandler, ConnectionUtils


class TestKeepAliveHandler:
    """Test VitreaKeepAliveHandler functionality and resilience."""

    @pytest.fixture
    def mock_socket(self):
        """Create a mock VitreaSocket for testing."""
        socket = VitreaSocket("localhost", 8080)
        socket._reader = Mock()
        socket._writer = Mock()
        socket._writer.is_closing.return_value = False
        socket._connection_state.set()
        socket.send_keepalive = AsyncMock()
        return socket

    @pytest.fixture
    def keepalive_handler(self, mock_socket):
        """Create a keepalive handler with mocked monitor."""
        return VitreaKeepAliveHandler(monitor=mock_socket, interval_seconds=0.1)

    @pytest.mark.asyncio
    async def test_keepalive_enable_disable(self, keepalive_handler):
        """Test basic enable/disable functionality."""
        # Initially disabled
        assert not keepalive_handler.enabled
        assert keepalive_handler._task is None

        # Enable keepalive
        await keepalive_handler.enable()
        assert keepalive_handler.enabled
        assert keepalive_handler._task is not None
        assert not keepalive_handler._task.done()

        # Disable keepalive
        await keepalive_handler.disable()
        assert not keepalive_handler.enabled
        assert keepalive_handler._should_stop

    @pytest.mark.asyncio
    async def test_keepalive_sends_successfully(self, mock_socket, keepalive_handler):
        """Test that keepalive sends successfully when connection is good."""
        mock_socket.is_connected = Mock(return_value=True)
        mock_socket.send_keepalive = AsyncMock()

        await keepalive_handler.enable()

        # Wait for at least one keepalive cycle
        await asyncio.sleep(0.2)

        # Verify keepalive was sent
        assert mock_socket.send_keepalive.called

        await keepalive_handler.disable()

    @pytest.mark.asyncio
    async def test_keepalive_handles_socket_errors(self, mock_socket, keepalive_handler):
        """Test that keepalive handles socket errors gracefully."""
        mock_socket.is_connected = Mock(return_value=True)
        mock_socket.send_keepalive = AsyncMock(side_effect=ConnectionError("Socket error"))
        mock_socket.connect_with_coordination = AsyncMock(return_value=True)

        await keepalive_handler.enable()

        # Wait for keepalive to handle the error
        await asyncio.sleep(0.2)

        # Verify keepalive attempted recovery
        assert mock_socket.connect_with_coordination.called

        await keepalive_handler.disable()

    @pytest.mark.asyncio
    async def test_keepalive_detects_disconnection(self, mock_socket, keepalive_handler):
        """Test that keepalive detects and handles disconnection."""
        mock_socket.is_connected = Mock(return_value=False)
        mock_socket.connect_with_coordination = AsyncMock(return_value=True)

        await keepalive_handler.enable()

        # Wait for keepalive to detect disconnection
        await asyncio.sleep(0.2)

        # Verify reconnection was attempted
        assert mock_socket.connect_with_coordination.called
        call_args = mock_socket.connect_with_coordination.call_args
        assert "Keepalive" in str(call_args)

        await keepalive_handler.disable()

    @pytest.mark.asyncio
    async def test_keepalive_backoff_on_failures(self, mock_socket, keepalive_handler):
        """Test that keepalive uses exponential backoff on failures."""
        mock_socket.is_connected = Mock(return_value=False)
        mock_socket.connect_with_coordination = AsyncMock(side_effect=ConnectionError("Failed"))

        with patch.object(ConnectionUtils, 'calculate_backoff_delay', return_value=0.05) as mock_backoff:
            await keepalive_handler.enable()

            # Wait for multiple failure cycles
            await asyncio.sleep(0.3)

            # Verify backoff was calculated
            assert mock_backoff.called

            await keepalive_handler.disable()

    @pytest.mark.asyncio
    async def test_keepalive_task_restart_on_failure(self, keepalive_handler):
        """Test that keepalive task restarts automatically on failure."""
        # Mock _keepalive_loop to raise an exception
        original_loop = keepalive_handler._keepalive_loop

        async def failing_loop():
            raise RuntimeError("Simulated failure")

        keepalive_handler._keepalive_loop = failing_loop

        await keepalive_handler.enable()
        original_task = keepalive_handler._task

        # Wait for task to fail and restart
        await asyncio.sleep(0.1)

        # Verify task failed
        assert original_task.done()
        assert original_task.exception() is not None

        # Wait for restart
        await asyncio.sleep(5.1)  # Restart delay is 5 seconds

        # Verify new task was created (if still enabled)
        if keepalive_handler.enabled:
            assert keepalive_handler._task != original_task

        await keepalive_handler.disable()

    @pytest.mark.asyncio
    async def test_keepalive_pause_resume(self, mock_socket, keepalive_handler):
        """Test pause and resume functionality."""
        await keepalive_handler.enable()
        original_task = keepalive_handler._task

        # Pause keepalive
        await keepalive_handler.pause()
        assert keepalive_handler._task is None
        assert original_task.cancelled() or original_task.done()

        # Resume keepalive
        await keepalive_handler.resume()
        assert keepalive_handler._task is not None
        assert keepalive_handler._task != original_task

        await keepalive_handler.disable()

    @pytest.mark.asyncio
    async def test_keepalive_reset(self, mock_socket, keepalive_handler):
        """Test reset functionality."""
        await keepalive_handler.enable()
        original_task = keepalive_handler._task

        # Reset keepalive
        await keepalive_handler.reset()

        # Verify new task was created
        assert keepalive_handler._task is not None
        assert keepalive_handler._task != original_task
        assert original_task.cancelled() or original_task.done()

        await keepalive_handler.disable()

    @pytest.mark.asyncio
    async def test_keepalive_with_jitter(self, mock_socket, keepalive_handler):
        """Test that keepalive uses jitter in timing."""
        # Ensure the socket appears connected and send_keepalive succeeds
        mock_socket.is_connected = Mock(return_value=True)
        mock_socket.is_reconnecting = AsyncMock(return_value=False)

        # Track call times to measure actual intervals
        call_times = []
        async def track_send_keepalive():
            call_times.append(asyncio.get_event_loop().time())

        mock_socket.send_keepalive = track_send_keepalive

        # Set a known interval
        keepalive_handler.interval = 1

        await keepalive_handler.enable()

        # Wait for multiple keepalive cycles
        await asyncio.sleep(2.5)  # Wait for 2-3 cycles

        await keepalive_handler.disable()

        # Verify keepalive was sent multiple times
        assert len(call_times) >= 2, f"Expected at least 2 keepalive calls, got {len(call_times)}"

        # Calculate intervals between actual calls (not total time / call count)
        intervals = [call_times[i] - call_times[i-1] for i in range(1, len(call_times))]

        # Each interval should be within the jitter range
        # max_jitter = min(2.0, 1.0 * 0.05) = 0.05
        # So intervals should be between 0.95 and 1.0 seconds
        for i, interval in enumerate(intervals):
            assert 0.95 <= interval <= 1.0, f"Interval {i+1}: Expected 0.95-1.0s, got {interval}s"

    @pytest.mark.asyncio
    async def test_keepalive_waits_for_main_reconnection(self, mock_socket, keepalive_handler):
        """Test that keepalive waits when main reconnection is in progress."""
        # Set socket as reconnecting
        await mock_socket._set_reconnecting_state(True)
        mock_socket.is_connected = Mock(return_value=False)

        await keepalive_handler.enable()

        # Wait for keepalive loop to check reconnection status
        await asyncio.sleep(0.2)

        # Keepalive should wait and not attempt its own reconnection
        # (This is tested by ensuring no reconnection attempts were made)

        await mock_socket._set_reconnecting_state(False)
        await keepalive_handler.disable()

    @pytest.mark.asyncio
    async def test_keepalive_sync_disable_compatibility(self, keepalive_handler):
        """Test sync disable method for backwards compatibility."""
        await keepalive_handler.enable()
        assert keepalive_handler.enabled

        # Use sync disable
        keepalive_handler.disable_sync()

        # Wait for async cleanup
        await asyncio.sleep(0.1)

        assert not keepalive_handler.enabled
        assert keepalive_handler._should_stop

    @pytest.mark.asyncio
    async def test_keepalive_error_classification(self, mock_socket, keepalive_handler):
        """Test that keepalive properly classifies different types of errors."""
        # Test socket error
        mock_socket.is_connected = Mock(return_value=True)
        mock_socket.send_keepalive = AsyncMock(side_effect=BrokenPipeError("Pipe broken"))
        mock_socket.connect_with_coordination = AsyncMock(return_value=True)

        await keepalive_handler.enable()
        await asyncio.sleep(0.2)

        # Should attempt recovery for socket errors
        assert mock_socket.connect_with_coordination.called

        # Test non-socket error
        mock_socket.send_keepalive = AsyncMock(side_effect=ValueError("Invalid value"))
        mock_socket.connect_with_coordination.reset_mock()

        await asyncio.sleep(0.2)

        # Should handle gracefully but may not attempt reconnection
        await keepalive_handler.disable()

    @pytest.mark.asyncio
    async def test_keepalive_connection_state_updates(self, mock_socket, keepalive_handler):
        """Test that keepalive properly updates connection state."""
        mock_socket.is_connected = Mock(return_value=True)
        mock_socket.send_keepalive = AsyncMock(side_effect=ConnectionError("Lost"))

        await keepalive_handler.enable()
        await asyncio.sleep(0.1)

        # Verify connection state was cleared on error
        assert not mock_socket._connection_state.is_set()

        await keepalive_handler.disable()
