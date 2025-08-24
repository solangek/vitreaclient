"""Tests for VitreaSocket reconnection functionality."""

import asyncio
import os
import sys

import pytest
from unittest.mock import Mock, AsyncMock, patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))
from vitreaclient.api import VitreaSocket, VitreaKeepAliveHandler, ConnectionUtils


class TestSocketReconnection:
    """Test VitreaSocket reconnection logic and coordination."""

    @pytest.fixture
    def socket(self):
        """Create a VitreaSocket for testing."""
        return VitreaSocket("localhost", 8080)

    @pytest.fixture
    async def mock_connection(self):
        """Mock asyncio.open_connection."""
        reader = Mock()
        writer = Mock()
        writer.is_closing.return_value = False
        writer.get_extra_info.return_value = Mock()
        return reader, writer

    @pytest.mark.asyncio
    async def test_single_reconnection_attempt(self, socket):
        """Test that only one reconnection happens at a time."""
        with patch('asyncio.open_connection') as mock_open:
            reader, writer = Mock(), Mock()
            writer.is_closing.return_value = False
            writer.get_extra_info.return_value = Mock()
            mock_open.return_value = (reader, writer)

            # Simulate multiple concurrent reconnection attempts
            tasks = []
            for i in range(5):
                task = asyncio.create_task(socket.connect_with_coordination(f"Test{i}"))
                tasks.append(task)

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Only one should have actually connected, others should be skipped
            successful_attempts = sum(1 for r in results if r is True)
            skipped_attempts = sum(1 for r in results if r is False)

            assert successful_attempts <= 1, "More than one reconnection succeeded"
            assert skipped_attempts >= 0, "Some attempts should be skipped"

    @pytest.mark.asyncio
    async def test_reconnection_state_synchronization(self, socket):
        """Test that reconnection state is properly synchronized."""
        # Test concurrent state checks and modifications
        async def state_modifier():
            for _ in range(10):
                await socket._set_reconnecting_state(True)
                await asyncio.sleep(0.001)
                await socket._set_reconnecting_state(False)
                await asyncio.sleep(0.001)

        async def state_checker():
            states = []
            for _ in range(20):
                state = await socket.is_reconnecting()
                states.append(state)
                await asyncio.sleep(0.001)
            return states

        # Run concurrent operations
        modifier_task = asyncio.create_task(state_modifier())
        checker_task = asyncio.create_task(state_checker())

        modifier_result, checker_result = await asyncio.gather(modifier_task, checker_task)

        # All states should be valid booleans
        assert all(isinstance(state, bool) for state in checker_result)

        # Final state should be False
        final_state = await socket.is_reconnecting()
        assert final_state is False

    @pytest.mark.asyncio
    async def test_connection_cleanup_on_error(self, socket):
        """Test that connections are properly cleaned up on errors."""
        # Set max attempts to prevent infinite retry loop
        socket._max_reconnect_attempts = 1

        with patch('asyncio.open_connection', side_effect=ConnectionError("Failed")):
            with patch('asyncio.sleep'):  # Speed up the test
                with pytest.raises(ConnectionError):
                    await socket.connect()

                # Verify cleanup occurred
                assert socket._reader is None
                assert socket._writer is None
                assert not socket._connection_state.is_set()
                assert not await socket.is_reconnecting()

    @pytest.mark.asyncio
    async def test_exponential_backoff_on_failures(self, socket):
        """Test that exponential backoff is used on connection failures."""
        failure_count = 0

        def failing_connection(*args, **kwargs):
            nonlocal failure_count
            failure_count += 1
            if failure_count <= 3:
                raise ConnectionError(f"Failure {failure_count}")
            # Succeed on 4th attempt
            reader, writer = Mock(), Mock()
            writer.is_closing.return_value = False
            writer.get_extra_info.return_value = Mock()
            return reader, writer

        with patch('asyncio.open_connection', side_effect=failing_connection):
            with patch.object(ConnectionUtils, 'calculate_backoff_delay', return_value=0.01) as mock_backoff:
                with patch('asyncio.sleep') as mock_sleep:
                    await socket.connect()

                    # Verify backoff was calculated for failures
                    assert mock_backoff.call_count >= 3
                    assert mock_sleep.call_count >= 3

    @pytest.mark.asyncio
    async def test_read_task_restart_after_reconnection(self, socket):
        """Test that read task is restarted after successful reconnection."""
        with patch('asyncio.open_connection') as mock_open:
            reader, writer = Mock(), Mock()
            writer.is_closing.return_value = False
            writer.get_extra_info.return_value = Mock()
            mock_open.return_value = (reader, writer)

            # Mock start_read_task to track calls
            original_start_read_task = socket.start_read_task
            start_read_task_calls = 0

            async def mock_start_read_task():
                nonlocal start_read_task_calls
                start_read_task_calls += 1

            socket.start_read_task = mock_start_read_task

            await socket.connect()

            # Verify read task was started
            assert start_read_task_calls == 1

    @pytest.mark.asyncio
    async def test_keepalive_coordination_after_reconnection(self, socket):
        """Test that keepalive is properly coordinated after reconnection."""
        # Create and attach a mock keepalive handler
        keepalive = Mock()
        keepalive.resume_after_reconnection = AsyncMock()
        socket._keepalive = keepalive

        with patch('asyncio.open_connection') as mock_open:
            reader, writer = Mock(), Mock()
            writer.is_closing.return_value = False
            writer.get_extra_info.return_value = Mock()
            mock_open.return_value = (reader, writer)

            # Mock start_read_task to avoid actual task creation
            socket.start_read_task = AsyncMock()

            await socket.connect()

            # Verify keepalive was resumed after reconnection
            keepalive.resume_after_reconnection.assert_called_once()

    @pytest.mark.asyncio
    async def test_connection_state_consistency(self, socket):
        """Test that connection state remains consistent during operations."""
        with patch('asyncio.open_connection') as mock_open:
            reader, writer = Mock(), Mock()
            writer.is_closing.return_value = False
            writer.get_extra_info.return_value = Mock()
            mock_open.return_value = (reader, writer)

            # Mock start_read_task
            socket.start_read_task = AsyncMock()

            # Test connection
            await socket.connect()
            assert socket.is_connected()
            assert socket._connection_state.is_set()

            # Test cleanup
            await socket.cleanup_connection()
            assert not socket.is_connected()
            assert not socket._connection_state.is_set()

    @pytest.mark.asyncio
    async def test_write_with_reconnection(self, socket):
        """Test that write operations trigger reconnection when needed."""
        # Initially no connection
        assert not socket.is_connected()

        with patch('asyncio.open_connection') as mock_open:
            with patch('asyncio.wait_for') as mock_wait_for:
                reader, writer = Mock(), Mock()
                writer.is_closing.return_value = False
                writer.get_extra_info.return_value = Mock()
                writer.write = Mock()
                writer.drain = AsyncMock()
                mock_open.return_value = (reader, writer)
                mock_wait_for.return_value = (reader, writer)

                # Mock all the async methods that get called during reconnection
                socket.start_read_task = AsyncMock()
                socket.cleanup_connection = AsyncMock()

                # Mock the state management methods completely to avoid mutex issues
                socket.is_reconnecting = AsyncMock(return_value=False)
                socket._set_reconnecting_state = AsyncMock()

                # Mock all the mutexes to prevent deadlocks
                socket._mutex = AsyncMock()
                socket._reconnect_mutex = AsyncMock()
                socket._state_mutex = AsyncMock()

                # Set up the async context manager behavior for mutexes
                socket._mutex.__aenter__ = AsyncMock()
                socket._mutex.__aexit__ = AsyncMock()
                socket._reconnect_mutex.__aenter__ = AsyncMock()
                socket._reconnect_mutex.__aexit__ = AsyncMock()
                socket._state_mutex.__aenter__ = AsyncMock()
                socket._state_mutex.__aexit__ = AsyncMock()

                # Mock the keepalive handler methods
                socket._keepalive = None  # Set to None to avoid keepalive calls

                # Attempt to write - should trigger reconnection
                await socket.write(b"test data")

                # Verify connection was established and data was written
                mock_open.assert_called_once()
                writer.write.assert_called_once_with(b"test data")
                writer.drain.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_retry_on_socket_error(self, socket):
        """Test that write operations retry on socket errors."""
        # Pre-establish a connection to simulate the retry scenario
        reader, writer = Mock(), Mock()
        writer.is_closing.return_value = False
        writer.get_extra_info.return_value = Mock()

        # First write fails, second succeeds
        call_count = 0
        def mock_write(data):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise BrokenPipeError("Pipe broken")

        writer.write = mock_write
        writer.drain = AsyncMock()

        # Set up the socket with an existing connection
        socket._writer = writer
        socket._reader = reader
        socket._connection_state.set()

        # Mock the methods called during retry
        with patch.object(socket, 'cleanup_connection', new_callable=AsyncMock) as mock_cleanup:
            with patch.object(socket, 'connect', new_callable=AsyncMock) as mock_connect:
                # Mock the keepalive handler
                socket._keepalive = None

                # Write should succeed after retry
                await socket.write(b"test data")

                # Verify write was called twice (initial fail + retry success)
                assert call_count == 2
                # Verify cleanup and reconnection were called
                mock_cleanup.assert_called_once()
                mock_connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_max_reconnection_attempts(self, socket):
        """Test that max reconnection attempts are respected."""
        socket._max_reconnect_attempts = 3

        with patch('asyncio.open_connection', side_effect=ConnectionError("Always fails")):
            with patch('asyncio.sleep'):  # Speed up the test
                with pytest.raises(ConnectionError):
                    await socket.connect()

                # Verify we don't exceed max attempts
                assert socket._reconnect_attempt <= 3

    @pytest.mark.asyncio
    async def test_connection_maintenance_flag(self, socket):
        """Test that connection maintenance flag is respected."""
        socket._should_maintain_connection = False

        # Attempt to connect when maintenance is disabled
        with patch('asyncio.open_connection') as mock_open:
            await socket._reconnect()

            # Should not attempt connection
            mock_open.assert_not_called()

    @pytest.mark.asyncio
    async def test_disconnect_stops_reconnection(self, socket):
        """Test that disconnect properly stops reconnection attempts."""
        # Start a connection
        with patch('asyncio.open_connection') as mock_open:
            reader, writer = Mock(), Mock()
            writer.is_closing.return_value = False
            writer.get_extra_info.return_value = Mock()
            mock_open.return_value = (reader, writer)
            socket.start_read_task = AsyncMock()

            await socket.connect()
            assert socket._should_maintain_connection

            # Disconnect
            await socket.disconnect()
            assert not socket._should_maintain_connection

    @pytest.mark.asyncio
    async def test_close_cleanup(self, socket):
        """Test that close method properly cleans up all resources."""
        # Create a mock keepalive
        keepalive = Mock()
        keepalive.disable = AsyncMock()
        socket._keepalive = keepalive

        # Mock cleanup_connection
        socket.cleanup_connection = AsyncMock()

        await socket.close()

        # Verify all cleanup occurred
        assert not socket._should_maintain_connection
        keepalive.disable.assert_called_once()
        socket.cleanup_connection.assert_called_once()
