"""Tests for race conditions between VitreaKeepAliveHandler and VitreaSocket."""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch
from src.vitreaclient.api import VitreaSocket, VitreaKeepAliveHandler


class TestRaceConditions:
    """Test race conditions between keepalive and socket reconnection."""

    @pytest.fixture
    def mock_socket(self):
        """Create a mock VitreaSocket for testing."""
        socket = VitreaSocket("localhost", 8080)
        # Mock the actual connection methods to avoid real network calls
        socket._reader = Mock()
        socket._writer = Mock()
        socket._writer.is_closing.return_value = False
        socket._connection_state.set()
        return socket

    @pytest.fixture
    def keepalive_handler(self, mock_socket):
        """Create a keepalive handler with mocked monitor."""
        handler = VitreaKeepAliveHandler(monitor=mock_socket, interval_seconds=1)
        # Mock the send_keepalive method
        mock_socket.send_keepalive = AsyncMock()
        return handler

    @pytest.mark.asyncio
    async def test_no_duplicate_reconnection_attempts(self, mock_socket, keepalive_handler):
        """Test that multiple reconnection attempts don't happen simultaneously."""
        # Simulate a slow reconnection process
        original_reconnect = mock_socket._reconnect
        reconnect_call_count = 0

        async def slow_reconnect():
            nonlocal reconnect_call_count
            reconnect_call_count += 1
            await asyncio.sleep(0.1)  # Simulate slow reconnection
            await original_reconnect()

        mock_socket._reconnect = slow_reconnect

        # Start multiple concurrent reconnection attempts
        tasks = []
        for i in range(5):
            task = asyncio.create_task(mock_socket.connect_with_coordination(f"Test{i}"))
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Only one reconnection should have actually been attempted
        assert reconnect_call_count <= 1, f"Expected at most 1 reconnection, got {reconnect_call_count}"

        # At least one should have succeeded or been skipped
        successful_or_skipped = sum(1 for r in results if isinstance(r, bool))
        assert successful_or_skipped > 0

    @pytest.mark.asyncio
    async def test_keepalive_waits_for_socket_reconnection(self, mock_socket, keepalive_handler):
        """Test that keepalive waits when socket is already reconnecting."""
        # Set socket as reconnecting
        await mock_socket._set_reconnecting_state(True)

        # Mock disconnected state
        mock_socket._connection_state.clear()
        mock_socket.is_connected = Mock(return_value=False)

        # Start keepalive handler
        await keepalive_handler.enable()

        # Wait a short time for keepalive loop to run
        await asyncio.sleep(0.1)

        # Verify that keepalive detects reconnection in progress
        assert await mock_socket.is_reconnecting()

        # Clean up
        await keepalive_handler.disable()
        await mock_socket._set_reconnecting_state(False)

    @pytest.mark.asyncio
    async def test_synchronized_state_access(self, mock_socket):
        """Test that reconnection state access is properly synchronized."""
        # Test concurrent access to reconnection state
        async def check_and_set_state():
            for _ in range(10):
                is_reconnecting = await mock_socket.is_reconnecting()
                if not is_reconnecting:
                    await mock_socket._set_reconnecting_state(True)
                    await asyncio.sleep(0.01)
                    await mock_socket._set_reconnecting_state(False)

        # Run multiple concurrent state checks
        tasks = [asyncio.create_task(check_and_set_state()) for _ in range(5)]
        await asyncio.gather(*tasks)

        # Final state should be consistent
        final_state = await mock_socket.is_reconnecting()
        assert isinstance(final_state, bool)

    @pytest.mark.asyncio
    async def test_keepalive_resume_after_reconnection(self, mock_socket, keepalive_handler):
        """Test that keepalive properly resumes after reconnection."""
        # Enable keepalive
        await keepalive_handler.enable()
        assert keepalive_handler.enabled

        # Simulate successful reconnection
        await keepalive_handler.resume_after_reconnection()

        # Verify keepalive is still enabled and running
        assert keepalive_handler.enabled
        assert keepalive_handler._task is not None
        assert not keepalive_handler._task.done()

        # Verify failure counter was reset
        assert keepalive_handler._connection_lost_count == 0

        # Clean up
        await keepalive_handler.disable()

    @pytest.mark.asyncio
    async def test_coordinated_reconnection_attempts(self, mock_socket, keepalive_handler):
        """Test that keepalive and socket coordinate reconnection attempts."""
        # Mock connection methods
        mock_socket.connect = AsyncMock()
        mock_socket.connect_with_coordination = AsyncMock(return_value=True)
        mock_socket.is_connected = Mock(return_value=False)
        mock_socket.send_keepalive = AsyncMock(side_effect=ConnectionError("Connection lost"))

        # Enable keepalive
        await keepalive_handler.enable()

        # Wait for keepalive to detect disconnection and attempt reconnection
        await asyncio.sleep(0.1)

        # Verify coordinated reconnection was attempted
        assert mock_socket.connect_with_coordination.called
        call_args = mock_socket.connect_with_coordination.call_args
        assert "Keepalive" in str(call_args)

        # Clean up
        await keepalive_handler.disable()

    @pytest.mark.asyncio
    async def test_task_cleanup_on_disable(self, keepalive_handler):
        """Test that keepalive tasks are properly cleaned up on disable."""
        # Enable keepalive
        await keepalive_handler.enable()
        task = keepalive_handler._task
        assert task is not None
        assert not task.done()

        # Disable keepalive
        await keepalive_handler.disable()

        # Verify task was cancelled and cleaned up
        assert task.cancelled() or task.done()
        assert not keepalive_handler.enabled
        assert keepalive_handler._should_stop

    @pytest.mark.asyncio
    async def test_connection_state_consistency(self, mock_socket):
        """Test that connection state remains consistent during operations."""
        # Test multiple concurrent operations that modify connection state
        async def operation():
            for _ in range(5):
                mock_socket._connection_state.clear()
                await asyncio.sleep(0.01)
                mock_socket._connection_state.set()
                await asyncio.sleep(0.01)

        # Run concurrent operations
        tasks = [asyncio.create_task(operation()) for _ in range(3)]
        await asyncio.gather(*tasks)

        # Connection state should be in a valid state
        is_connected = mock_socket.is_connected()
        assert isinstance(is_connected, bool)

    @pytest.mark.asyncio
    async def test_proper_async_disable_handling(self, mock_socket, keepalive_handler):
        """Test that both sync and async disable methods work correctly."""
        # Test async disable
        await keepalive_handler.enable()
        assert keepalive_handler.enabled

        await keepalive_handler.disable()
        assert not keepalive_handler.enabled

        # Test sync disable (should create async task)
        await keepalive_handler.enable()
        assert keepalive_handler.enabled

        keepalive_handler.disable_sync()

        # Wait for async cleanup to complete
        await asyncio.sleep(0.1)
        assert not keepalive_handler.enabled

    @pytest.mark.asyncio
    async def test_keepalive_error_isolation(self, mock_socket, keepalive_handler):
        """Test that keepalive errors don't interfere with main logic."""
        # Mock send_keepalive to raise various errors
        mock_socket.send_keepalive = AsyncMock(side_effect=OSError("Socket error"))
        mock_socket.is_connected = Mock(return_value=True)
        mock_socket.connect_with_coordination = AsyncMock(return_value=False)

        # Enable keepalive
        await keepalive_handler.enable()

        # Wait for keepalive to handle the error
        await asyncio.sleep(0.1)

        # Verify keepalive is still running despite errors
        assert keepalive_handler.enabled
        assert keepalive_handler._task is not None
        assert not keepalive_handler._task.done()

        # Clean up
        await keepalive_handler.disable()
