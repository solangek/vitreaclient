"""Tests for basic VitreaSocket connection functionality."""

import asyncio
import pytest
import socket as socket_module
from unittest.mock import Mock, AsyncMock, patch
from src.vitreaclient.api import VitreaSocket, VitreaKeepAliveHandler


class TestConnection:
    """Test basic VitreaSocket connection functionality."""

    @pytest.fixture
    def socket(self):
        """Create a VitreaSocket for testing."""
        return VitreaSocket("localhost", 8080)

    @pytest.mark.asyncio
    async def test_initial_state(self, socket):
        """Test that socket starts in correct initial state."""
        assert socket.host == "localhost"
        assert socket.port == 8080
        assert socket._reader is None
        assert socket._writer is None
        assert socket._keepalive is None
        assert socket._read_task is None
        assert not socket.is_connected()
        assert socket._should_maintain_connection is True
        assert not socket._is_reconnecting

    @pytest.mark.asyncio
    async def test_successful_connection(self, socket):
        """Test successful connection establishment."""
        reader = Mock()
        reader.at_eof.return_value = False  # Mock reader is not at EOF
        writer = Mock()
        writer.is_closing.return_value = False
        mock_socket = Mock()
        writer.get_extra_info.return_value = mock_socket

        with patch('asyncio.wait_for') as mock_wait_for:
            mock_wait_for.return_value = (reader, writer)

            with patch.object(socket, 'cleanup_connection', new_callable=AsyncMock) as mock_cleanup:
                # Mock keepalive operations
                mock_keepalive = Mock()
                mock_keepalive.enabled = False
                mock_keepalive.enable = AsyncMock()
                mock_keepalive.resume_after_reconnection = AsyncMock()
                socket._keepalive = mock_keepalive

                # Create a mock read task that appears to be running
                mock_read_task = Mock()
                mock_read_task.done.return_value = False  # Task is still running

                # Mock start_read_task to set up the mock read task
                async def mock_start_read_task():
                    socket._read_task = mock_read_task

                with patch.object(socket, 'start_read_task', side_effect=mock_start_read_task):
                    await socket.connect()

                    # Verify connection was established
                    assert socket._reader == reader
                    assert socket._writer == writer
                    assert socket._socket == mock_socket
                    assert socket.sock == mock_socket
                    assert socket.is_connected()
                    assert socket._connection_state.is_set()

                    # Verify the flow was called
                    mock_wait_for.assert_called_once()
                    mock_cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_connection_already_connected(self, socket):
        """Test that connect() returns early if already connected."""
        # Set up existing connection
        writer = Mock()
        writer.is_closing.return_value = False
        socket._writer = writer
        socket._connection_state.set()

        with patch('asyncio.wait_for') as mock_wait_for:
            await socket.connect()

            # Should not attempt new connection
            mock_wait_for.assert_not_called()

    @pytest.mark.asyncio
    async def test_connection_timeout(self, socket):
        """Test connection timeout handling."""
        # Set max attempts to prevent infinite retry loop
        socket._max_reconnect_attempts = 1

        with patch('asyncio.wait_for', side_effect=asyncio.TimeoutError("Timeout")):
            with patch('asyncio.sleep'):  # Speed up test
                with patch.object(socket, 'cleanup_connection', new_callable=AsyncMock):
                    with pytest.raises(ConnectionError, match="Failed to connect to Vitrea box"):
                        await socket.connect()

    @pytest.mark.asyncio
    async def test_is_connected_states(self, socket):
        """Test is_connected() method in various states."""
        # Not connected initially
        assert not socket.is_connected()

        # Connected state - need all conditions satisfied
        reader = Mock()
        reader.at_eof.return_value = False  # Reader not at EOF
        writer = Mock()
        writer.is_closing.return_value = False
        mock_read_task = Mock()
        mock_read_task.done.return_value = False  # Task is running

        socket._reader = reader
        socket._writer = writer
        socket._read_task = mock_read_task
        socket._connection_state.set()
        assert socket.is_connected()

        # Writer closing
        writer.is_closing.return_value = True
        assert not socket.is_connected()

        # Connection state cleared
        writer.is_closing.return_value = False
        socket._connection_state.clear()
        assert not socket.is_connected()

        # Writer is None
        socket._writer = None
        socket._connection_state.set()
        assert not socket.is_connected()

        # Reader at EOF
        socket._writer = writer
        reader.at_eof.return_value = True
        assert not socket.is_connected()

        # Read task done
        reader.at_eof.return_value = False
        mock_read_task.done.return_value = True
        assert not socket.is_connected()

    @pytest.mark.asyncio
    async def test_cleanup_connection(self, socket):
        """Test connection cleanup functionality."""
        # Set up connection with read task
        reader = Mock()
        writer = Mock()
        writer.close = Mock()
        writer.wait_closed = AsyncMock()
        socket._reader = reader
        socket._writer = writer
        socket._connection_state.set()

        # Create a real asyncio task for testing cancellation
        async def dummy_read_loop():
            try:
                await asyncio.sleep(100)  # This will be cancelled
            except asyncio.CancelledError:
                raise  # Re-raise to simulate proper cancellation

        read_task = asyncio.create_task(dummy_read_loop())
        socket._read_task = read_task

        await socket.cleanup_connection()

        # Verify cleanup
        assert read_task.cancelled()  # Task should be cancelled
        writer.close.assert_called_once()
        writer.wait_closed.assert_called_once()
        assert socket._reader is None
        assert socket._writer is None
        assert socket._read_task is None
        assert not socket._connection_state.is_set()

    @pytest.mark.asyncio
    async def test_cleanup_connection_with_error(self, socket):
        """Test cleanup handles writer close errors gracefully."""
        writer = Mock()
        writer.close.side_effect = Exception("Close error")
        writer.wait_closed = AsyncMock()
        socket._writer = writer
        socket._connection_state.set()

        # Should not raise exception
        await socket.cleanup_connection()

        # Should still clean up state
        assert socket._writer is None
        assert not socket._connection_state.is_set()

    @pytest.mark.asyncio
    async def test_start_read_task(self, socket):
        """Test read task starting functionality."""
        # Mock connection and read loop
        reader = Mock()
        socket._reader = reader

        # Mock the _read_loop method
        with patch.object(socket, '_read_loop', new_callable=AsyncMock) as mock_read_loop:
            # Make the read loop run indefinitely (simulating real behavior)
            async def long_running_task():
                await asyncio.sleep(100)  # Long sleep to simulate continuous reading
            mock_read_loop.side_effect = long_running_task

            await socket.start_read_task()

            # Verify task was created and is running
            assert socket._read_task is not None
            assert not socket._read_task.done()

            # Clean up the task
            if socket._read_task and not socket._read_task.done():
                socket._read_task.cancel()
                try:
                    await socket._read_task
                except asyncio.CancelledError:
                    pass

    @pytest.mark.asyncio
    async def test_start_read_task_when_no_reader(self, socket):
        """Test that start_read_task handles no reader gracefully."""
        # No reader set
        socket._reader = None

        with patch.object(socket, '_read_loop', new_callable=AsyncMock):
            await socket.start_read_task()

            # Should not create task when no reader
            assert socket._read_task is None

    @pytest.mark.asyncio
    async def test_start_read_task_replaces_done_task(self, socket):
        """Test that start_read_task replaces done tasks."""
        reader = Mock()
        socket._reader = reader

        # Create an already done task
        async def completed_task():
            return "completed"

        first_task = asyncio.create_task(completed_task())
        await first_task  # Wait for it to complete
        socket._read_task = first_task

        assert first_task.done()

        # Mock the read loop for the new task
        with patch.object(socket, '_read_loop', new_callable=AsyncMock) as mock_read_loop:
            async def long_running_task():
                await asyncio.sleep(100)
            mock_read_loop.side_effect = long_running_task

            await socket.start_read_task()

            second_task = socket._read_task

            # Should be different task
            assert second_task != first_task
            assert not second_task.done()

            # Clean up
            if second_task and not second_task.done():
                second_task.cancel()
                try:
                    await second_task
                except asyncio.CancelledError:
                    pass

    @pytest.mark.asyncio
    async def test_create_new_socket(self, socket):
        """Test socket creation."""
        new_socket = socket._create_new_socket()
        assert new_socket.family == socket_module.AF_INET
        assert new_socket.type == socket_module.SOCK_STREAM
        new_socket.close()  # Clean up

    @pytest.mark.asyncio
    async def test_connection_state_event(self, socket):
        """Test connection state event functionality."""
        # Initially not set
        assert not socket._connection_state.is_set()

        # Set and verify
        socket._connection_state.set()
        assert socket._connection_state.is_set()

        # Clear and verify
        socket._connection_state.clear()
        assert not socket._connection_state.is_set()

    @pytest.mark.asyncio
    async def test_should_maintain_connection_flag(self, socket):
        """Test should_maintain_connection flag behavior."""
        # Initially True
        assert socket._should_maintain_connection is True

        # Can be set to False
        socket._should_maintain_connection = False
        assert socket._should_maintain_connection is False

        # Re-enabled on explicit connect
        reader = Mock()
        writer = Mock()
        writer.is_closing.return_value = False
        writer.get_extra_info.return_value = Mock()

        with patch('asyncio.wait_for', return_value=(reader, writer)):
            with patch.object(socket, 'start_read_task', new_callable=AsyncMock):
                with patch.object(socket, 'cleanup_connection', new_callable=AsyncMock):
                    await socket.connect()
                    assert socket._should_maintain_connection is True

    @pytest.mark.asyncio
    async def test_host_port_properties(self, socket):
        """Test that host and port are properly stored."""
        assert socket.host == "localhost"
        assert socket.port == 8080

        # Test with different values
        socket2 = VitreaSocket("example.com", 9999)
        assert socket2.host == "example.com"
        assert socket2.port == 9999

    @pytest.mark.asyncio
    async def test_socket_references(self, socket):
        """Test socket reference management."""
        # Initially None
        assert socket.sock is None
        assert socket._socket is None

        # Set up connection
        reader = Mock()
        writer = Mock()
        writer.is_closing.return_value = False
        mock_socket_obj = Mock()
        writer.get_extra_info.return_value = mock_socket_obj

        with patch('asyncio.wait_for', return_value=(reader, writer)):
            with patch.object(socket, 'start_read_task', new_callable=AsyncMock):
                with patch.object(socket, 'cleanup_connection', new_callable=AsyncMock):
                    await socket.connect()

                    # Verify socket references are set
                    assert socket.sock == mock_socket_obj
                    assert socket._socket == mock_socket_obj

    @pytest.mark.asyncio
    async def test_mutex_usage(self, socket):
        """Test that mutex is used for connection operations."""
        # The mutex should be an asyncio.Lock
        assert isinstance(socket._mutex, asyncio.Lock)
        assert isinstance(socket._reconnect_mutex, asyncio.Lock)
        assert isinstance(socket._state_mutex, asyncio.Lock)

        # Test that mutex can be acquired
        async with socket._mutex:
            pass  # Should not block or error

    @pytest.mark.asyncio
    async def test_keepalive_assignment(self, socket):
        """Test keepalive handler assignment."""
        # Initially None
        assert socket._keepalive is None

        # Can assign keepalive
        keepalive = VitreaKeepAliveHandler(monitor=socket)
        socket._keepalive = keepalive
        assert socket._keepalive == keepalive

    @pytest.mark.asyncio
    async def test_write_method_basic(self, socket):
        """Test basic write functionality."""
        # Set up connected state
        writer = Mock()
        writer.is_closing.return_value = False
        writer.write = Mock()
        writer.drain = AsyncMock()
        socket._writer = writer
        socket._connection_state.set()

        test_data = b"test message"

        await socket.write(test_data)

        writer.write.assert_called_once_with(test_data)
        writer.drain.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_method_reconnects_on_error(self, socket):
        """Test that write method handles connection errors by reconnecting."""
        # Set up initial writer that will fail
        failing_writer = Mock()
        failing_writer.is_closing.return_value = False
        failing_writer.write = Mock(side_effect=ConnectionResetError("Connection lost"))
        failing_writer.drain = AsyncMock()
        socket._writer = failing_writer
        socket._connection_state.set()

        # Set up successful reconnection
        new_writer = Mock()
        new_writer.is_closing.return_value = False
        new_writer.write = Mock()
        new_writer.drain = AsyncMock()

        with patch.object(socket, 'cleanup_connection', new_callable=AsyncMock):
            with patch.object(socket, 'connect', new_callable=AsyncMock) as mock_connect:
                # After connect is called, set up the new writer
                def setup_new_writer(*args, **kwargs):
                    socket._writer = new_writer
                    socket._connection_state.set()
                mock_connect.side_effect = setup_new_writer

                test_data = b"test message"
                await socket.write(test_data)

                # Should have tried to reconnect
                mock_connect.assert_called()
                # Should have written with new writer
                new_writer.write.assert_called_with(test_data)
                new_writer.drain.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_method(self, socket):
        """Test disconnect functionality."""
        # Set up connected state with keepalive
        writer = Mock()
        writer.close = Mock()
        writer.wait_closed = AsyncMock()
        socket._writer = writer
        socket._connection_state.set()

        # Mock keepalive
        keepalive = Mock()
        keepalive.disable = AsyncMock()
        socket._keepalive = keepalive

        await socket.disconnect()

        # Should disable maintenance and clean up
        assert not socket._should_maintain_connection
        keepalive.disable.assert_called_once()
        assert socket._writer is None
        assert not socket._connection_state.is_set()

    @pytest.mark.asyncio
    async def test_close_method(self, socket):
        """Test close functionality."""
        # Set up connected state with keepalive
        writer = Mock()
        writer.close = Mock()
        writer.wait_closed = AsyncMock()
        socket._writer = writer
        socket._connection_state.set()

        # Mock keepalive
        keepalive = Mock()
        keepalive.disable = AsyncMock()
        socket._keepalive = keepalive

        await socket.close()

        # Should disable maintenance and clean up
        assert not socket._should_maintain_connection
        keepalive.disable.assert_called_once()
        assert socket._writer is None
        assert not socket._connection_state.is_set()
