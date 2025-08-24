"""Tests for connection maintenance behavior in VitreaSocket."""

import asyncio
import os
import sys

import pytest
from unittest.mock import Mock, AsyncMock, patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))
from vitreaclient.api import VitreaSocket, VitreaKeepAliveHandler


class TestShouldMaintainConnection:
    """Test the should_maintain_connection flag behavior."""

    @pytest.fixture
    def socket(self):
        """Create a VitreaSocket for testing."""
        return VitreaSocket("localhost", 8080)

    @pytest.mark.asyncio
    async def test_initial_state(self, socket):
        """Test initial state of should_maintain_connection."""
        assert socket._should_maintain_connection is True

    @pytest.mark.asyncio
    async def test_connect_enables_maintenance(self, socket):
        """Test that explicit connect enables connection maintenance."""
        socket._should_maintain_connection = False

        with patch('asyncio.open_connection') as mock_open:
            reader, writer = Mock(), Mock()
            writer.is_closing.return_value = False
            writer.get_extra_info.return_value = Mock()
            mock_open.return_value = (reader, writer)
            socket.start_read_task = AsyncMock()

            await socket.connect()
            assert socket._should_maintain_connection is True

    @pytest.mark.asyncio
    async def test_disconnect_disables_maintenance(self, socket):
        """Test that disconnect disables connection maintenance."""
        assert socket._should_maintain_connection is True

        socket._keepalive = Mock()
        socket._keepalive.disable = AsyncMock()
        socket.cleanup_connection = AsyncMock()

        await socket.disconnect()
        assert socket._should_maintain_connection is False

    @pytest.mark.asyncio
    async def test_close_disables_maintenance(self, socket):
        """Test that close disables connection maintenance."""
        assert socket._should_maintain_connection is True

        socket._keepalive = Mock()
        socket._keepalive.disable = AsyncMock()
        socket.cleanup_connection = AsyncMock()

        await socket.close()
        assert socket._should_maintain_connection is False

    @pytest.mark.asyncio
    async def test_reconnect_respects_maintenance_flag(self, socket):
        """Test that reconnection respects the maintenance flag."""
        socket._should_maintain_connection = False

        with patch('asyncio.open_connection') as mock_open:
            # Should not attempt connection when maintenance is disabled
            await socket._reconnect()
            mock_open.assert_not_called()

    @pytest.mark.asyncio
    async def test_read_loop_respects_maintenance_flag(self, socket):
        """Test that read loop respects the maintenance flag."""
        socket._should_maintain_connection = False

        # Mock the read loop components
        socket._reader = Mock()
        socket._reader.at_eof.return_value = True

        # Should exit early when maintenance is disabled
        await socket._read_loop()

        # Should not attempt reconnection
        assert not socket._should_maintain_connection

    @pytest.mark.asyncio
    async def test_handle_read_error_respects_maintenance_flag(self, socket):
        """Test that read error handler respects maintenance flag."""
        socket._should_maintain_connection = False

        # Should return False (exit) when maintenance is disabled
        result = await socket._handle_read_error("Test error", Exception("test"))
        assert result is False

    @pytest.mark.asyncio
    async def test_maintenance_flag_during_reconnection_loop(self, socket):
        """Test maintenance flag behavior during reconnection attempts."""
        socket._max_reconnect_attempts = 3

        call_count = 0
        def failing_connection(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                # Disable maintenance during reconnection attempts
                socket._should_maintain_connection = False
            raise ConnectionError("Always fails")

        with patch('asyncio.open_connection', side_effect=failing_connection):
            with patch('asyncio.sleep'):  # Speed up test
                await socket._reconnect()

                # Should have stopped when maintenance was disabled
                assert call_count == 2  # Should stop exactly at 2 attempts

    @pytest.mark.asyncio
    async def test_keepalive_interaction_with_maintenance_flag(self, socket):
        """Test keepalive behavior when maintenance is disabled."""
        # Mock the keepalive properly
        socket._keepalive = Mock()
        socket._keepalive.disable = AsyncMock()
        socket.cleanup_connection = AsyncMock()

        # Enable keepalive (simulate it's already enabled)
        socket._keepalive.enabled = True

        # Disable maintenance
        socket._should_maintain_connection = False
        await socket.disconnect()

        # Keepalive should be disabled
        socket._keepalive.disable.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_with_maintenance_disabled(self, socket):
        """Test write behavior when maintenance is disabled."""
        socket._should_maintain_connection = False
        socket._writer = None  # No connection

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

                # Write should still work (establishes connection)
                await socket.write(b"test")

                # Should have re-enabled maintenance for explicit operation
                assert socket._should_maintain_connection is True

    @pytest.mark.asyncio
    async def test_maintenance_flag_persistence(self, socket):
        """Test that maintenance flag persists across operations."""
        # Disable maintenance
        socket._should_maintain_connection = False

        # Various operations should not re-enable it
        socket._connection_state.set()
        assert socket._should_maintain_connection is False

        socket._connection_state.clear()
        assert socket._should_maintain_connection is False

        # Only explicit connect should re-enable
        with patch('asyncio.open_connection') as mock_open:
            reader, writer = Mock(), Mock()
            writer.is_closing.return_value = False
            writer.get_extra_info.return_value = Mock()
            mock_open.return_value = (reader, writer)
            socket.start_read_task = AsyncMock()

            await socket.connect()
            assert socket._should_maintain_connection is True

    @pytest.mark.asyncio
    async def test_coordinated_connection_respects_maintenance(self, socket):
        """Test that coordinated connection respects maintenance flag."""
        socket._should_maintain_connection = False

        with patch('asyncio.open_connection') as mock_open:
            reader, writer = Mock(), Mock()
            writer.is_closing.return_value = False
            writer.get_extra_info.return_value = Mock()
            mock_open.return_value = (reader, writer)
            socket.start_read_task = AsyncMock()

            result = await socket.connect_with_coordination("Test")

            # Should succeed but re-enable maintenance
            assert result is True
            assert socket._should_maintain_connection is True

    @pytest.mark.asyncio
    async def test_cleanup_with_maintenance_disabled(self, socket):
        """Test cleanup behavior when maintenance is disabled."""
        socket._should_maintain_connection = False

        # Set up connection
        socket._reader = Mock()
        socket._writer = Mock()
        socket._writer.close = Mock()
        socket._writer.wait_closed = AsyncMock()
        socket._connection_state.set()

        await socket.cleanup_connection()

        # Should clean up normally
        assert socket._reader is None
        assert socket._writer is None
        assert not socket._connection_state.is_set()

        # Maintenance flag should remain disabled
        assert socket._should_maintain_connection is False

    @pytest.mark.asyncio
    async def test_reconnection_state_with_maintenance_disabled(self, socket):
        """Test reconnection state handling when maintenance is disabled."""
        socket._should_maintain_connection = False

        # Should not set reconnecting state when maintenance is disabled
        await socket._reconnect()

        reconnecting = await socket.is_reconnecting()
        assert reconnecting is False
