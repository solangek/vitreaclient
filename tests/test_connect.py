"""Tests for basic VitreaSocket connection functionality."""

import asyncio
import pytest
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
        assert not await socket.is_reconnecting()

    @pytest.mark.asyncio
    async def test_successful_connection(self, socket):
        """Test successful connection establishment."""
        with patch('asyncio.open_connection') as mock_open:
            reader = Mock()
            writer = Mock()
            writer.is_closing.return_value = False
            writer.get_extra_info.return_value = Mock()
            mock_open.return_value = (reader, writer)

            # Mock start_read_task to avoid actual task creation
            socket.start_read_task = AsyncMock()

            await socket.connect()

            # Verify connection was established
            assert socket._reader == reader
            assert socket._writer == writer
            assert socket.is_connected()
            assert socket._connection_state.is_set()
            mock_open.assert_called_once_with("localhost", 8080)

    @pytest.mark.asyncio
    async def test_connection_already_connected(self, socket):
        """Test that connect() returns early if already connected."""
        # Set up existing connection
        socket._writer = Mock()
        socket._writer.is_closing.return_value = False
        socket._connection_state.set()

        with patch('asyncio.open_connection') as mock_open:
            await socket.connect()

            # Should not attempt new connection
            mock_open.assert_not_called()

    @pytest.mark.asyncio
    async def test_connection_timeout(self, socket):
        """Test connection timeout handling."""
        # Set max attempts to prevent infinite retry loop
        socket._max_reconnect_attempts = 1

        with patch('asyncio.open_connection', side_effect=asyncio.TimeoutError("Timeout")):
            with patch('asyncio.sleep'):  # Speed up test
                with pytest.raises(ConnectionError, match="Failed to connect to Vitrea box"):
                    await socket.connect()

    @pytest.mark.asyncio
    async def test_is_connected_states(self, socket):
        """Test is_connected() method in various states."""
        # Not connected initially
        assert not socket.is_connected()

        # Connected state
        socket._writer = Mock()
        socket._writer.is_closing.return_value = False
        socket._connection_state.set()
        assert socket.is_connected()

        # Writer closing
        socket._writer.is_closing.return_value = True
        assert not socket.is_connected()

        # Connection state cleared
        socket._writer.is_closing.return_value = False
        socket._connection_state.clear()
        assert not socket.is_connected()

        # Writer is None
        socket._writer = None
        socket._connection_state.set()
        assert not socket.is_connected()

    @pytest.mark.asyncio
    async def test_cleanup_connection(self, socket):
        """Test connection cleanup functionality."""
        # Set up connection with read task
        socket._reader = Mock()
        socket._writer = Mock()
        socket._writer.close = Mock()
        socket._writer.wait_closed = AsyncMock()
        socket._connection_state.set()

        # Create a real asyncio task that we can control
        async def dummy_task():
            # This task will be cancelled, so it should raise CancelledError
            await asyncio.sleep(100)  # Long sleep that will be cancelled

        read_task = asyncio.create_task(dummy_task())
        socket._read_task = read_task

        # Capture references before cleanup since they'll be set to None
        writer_mock = socket._writer

        await socket.cleanup_connection()

        # Verify cleanup
        assert read_task.cancelled()  # Task should be cancelled
        writer_mock.close.assert_called_once()
        writer_mock.wait_closed.assert_called_once()
        assert socket._reader is None
        assert socket._writer is None
        assert socket._read_task is None
        assert not socket._connection_state.is_set()

    @pytest.mark.asyncio
    async def test_cleanup_connection_with_error(self, socket):
        """Test cleanup handles writer close errors gracefully."""
        socket._writer = Mock()
        socket._writer.close.side_effect = Exception("Close error")
        socket._writer.wait_closed = AsyncMock()
        socket._connection_state.set()

        # Should not raise exception
        await socket.cleanup_connection()

        # Should still clean up state
        assert socket._writer is None
        assert not socket._connection_state.is_set()

    @pytest.mark.asyncio
    async def test_start_read_task(self, socket):
        """Test read task starting functionality."""
        # Mock connection
        socket._reader = Mock()
        socket._read_loop = AsyncMock()

        await socket.start_read_task()

        # Verify task was created
        assert socket._read_task is not None
        assert not socket._read_task.done()

    @pytest.mark.asyncio
    async def test_start_read_task_when_no_reader(self, socket):
        """Test that start_read_task connects if no reader exists."""
        socket.connect = AsyncMock()
        socket._read_loop = AsyncMock()

        await socket.start_read_task()

        # Should attempt to connect first
        socket.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_read_task_replaces_done_task(self, socket):
        """Test that start_read_task replaces done tasks."""
        socket._reader = Mock()
        socket._read_loop = AsyncMock()

        # Create initial task
        await socket.start_read_task()
        first_task = socket._read_task

        # Mark task as done
        first_task.done = Mock(return_value=True)

        # Start new task
        await socket.start_read_task()
        second_task = socket._read_task

        # Should be different task
        assert second_task != first_task

    @pytest.mark.asyncio
    async def test_create_new_socket(self, socket):
        """Test socket creation."""
        new_socket = socket._create_new_socket()
        assert new_socket.family == 2  # AF_INET
        assert new_socket.type == 1    # SOCK_STREAM

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
        with patch('asyncio.open_connection') as mock_open:
            reader, writer = Mock(), Mock()
            writer.is_closing.return_value = False
            writer.get_extra_info.return_value = Mock()
            mock_open.return_value = (reader, writer)
            socket.start_read_task = AsyncMock()

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
        with patch('asyncio.open_connection') as mock_open:
            reader = Mock()
            writer = Mock()
            writer.is_closing.return_value = False
            mock_socket = Mock()
            writer.get_extra_info.return_value = mock_socket
            mock_open.return_value = (reader, writer)
            socket.start_read_task = AsyncMock()

            await socket.connect()

            # Verify socket references are set
            assert socket.sock == mock_socket
            assert socket._socket == mock_socket

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
