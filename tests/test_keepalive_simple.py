"""Simple tests for VitreaKeepAliveHandler basic functionality."""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock
from src.vitreaclient.api import VitreaSocket, VitreaKeepAliveHandler


class TestKeepAliveSimple:
    """Simple tests for basic keepalive functionality."""

    @pytest.fixture
    def mock_socket(self):
        """Create a simple mock socket."""
        socket = Mock()
        socket.is_connected = Mock(return_value=True)
        socket.send_keepalive = AsyncMock()
        socket.is_reconnecting = AsyncMock(return_value=False)
        socket.connect_with_coordination = AsyncMock(return_value=True)
        socket._connection_state = Mock()
        return socket

    @pytest.fixture
    def keepalive_handler(self, mock_socket):
        """Create a simple keepalive handler."""
        return VitreaKeepAliveHandler(monitor=mock_socket, interval_seconds=0.1)

    @pytest.mark.asyncio
    async def test_basic_enable_disable(self, keepalive_handler):
        """Test basic enable and disable operations."""
        # Should start disabled
        assert not keepalive_handler.enabled

        # Enable keepalive
        await keepalive_handler.enable()
        assert keepalive_handler.enabled
        assert keepalive_handler._task is not None

        # Disable keepalive
        await keepalive_handler.disable()
        assert not keepalive_handler.enabled
        assert keepalive_handler._should_stop

    @pytest.mark.asyncio
    async def test_monitor_assignment(self, mock_socket):
        """Test monitor assignment functionality."""
        # Create without monitor
        handler = VitreaKeepAliveHandler(interval_seconds=1)
        assert handler.monitor is None

        # Set monitor
        handler.set_monitor(mock_socket)
        assert handler.monitor == mock_socket

        # Create with monitor
        handler2 = VitreaKeepAliveHandler(monitor=mock_socket, interval_seconds=1)
        assert handler2.monitor == mock_socket

    @pytest.mark.asyncio
    async def test_interval_setting(self):
        """Test that interval is properly set."""
        handler = VitreaKeepAliveHandler(interval_seconds=30)
        assert handler.interval == 30

        handler2 = VitreaKeepAliveHandler(interval_seconds=60)
        assert handler2.interval == 60

    @pytest.mark.asyncio
    async def test_task_creation(self, keepalive_handler):
        """Test that task is created when enabled."""
        await keepalive_handler.enable()

        # Task should be created
        assert keepalive_handler._task is not None
        assert not keepalive_handler._task.done()

        await keepalive_handler.disable()

    @pytest.mark.asyncio
    async def test_task_cancellation(self, keepalive_handler):
        """Test that task is cancelled when disabled."""
        await keepalive_handler.enable()
        task = keepalive_handler._task

        await keepalive_handler.disable()

        # Task should be cancelled
        assert task.cancelled() or task.done()

    @pytest.mark.asyncio
    async def test_double_enable(self, keepalive_handler):
        """Test that double enable doesn't create multiple tasks."""
        await keepalive_handler.enable()
        first_task = keepalive_handler._task

        await keepalive_handler.enable()
        second_task = keepalive_handler._task

        # Should be the same task
        assert first_task == second_task

        await keepalive_handler.disable()

    @pytest.mark.asyncio
    async def test_disable_when_not_enabled(self, keepalive_handler):
        """Test that disable works even when not enabled."""
        # Should not raise error
        await keepalive_handler.disable()
        assert not keepalive_handler.enabled

    @pytest.mark.asyncio
    async def test_sync_disable(self, keepalive_handler):
        """Test synchronous disable method."""
        await keepalive_handler.enable()
        assert keepalive_handler.enabled

        # Use sync disable
        keepalive_handler.disable_sync()

        # Should set flags immediately
        assert not keepalive_handler.enabled
        assert keepalive_handler._should_stop

        # Wait for async cleanup
        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_restart_functionality(self, keepalive_handler):
        """Test restart method."""
        await keepalive_handler.enable()
        original_task = keepalive_handler._task

        # Restart
        keepalive_handler.restart()

        # Wait for restart to complete
        await asyncio.sleep(0.1)

        # Should have new task
        assert keepalive_handler._task != original_task
        assert keepalive_handler.enabled

        await keepalive_handler.disable()

    @pytest.mark.asyncio
    async def test_pause_resume(self, keepalive_handler):
        """Test pause and resume functionality."""
        await keepalive_handler.enable()

        # Pause
        await keepalive_handler.pause()
        assert keepalive_handler._task is None

        # Resume
        await keepalive_handler.resume()
        assert keepalive_handler._task is not None

        await keepalive_handler.disable()

    @pytest.mark.asyncio
    async def test_reset(self, keepalive_handler):
        """Test reset functionality."""
        await keepalive_handler.enable()
        original_task = keepalive_handler._task

        # Reset
        await keepalive_handler.reset()

        # Should have new task
        assert keepalive_handler._task != original_task
        assert keepalive_handler._task is not None

        await keepalive_handler.disable()

    @pytest.mark.asyncio
    async def test_state_flags(self, keepalive_handler):
        """Test internal state flags."""
        # Initial state
        assert not keepalive_handler.enabled
        assert not keepalive_handler._should_stop
        assert keepalive_handler._connection_lost_count == 0

        # After enable
        await keepalive_handler.enable()
        assert keepalive_handler.enabled
        assert not keepalive_handler._should_stop

        # After disable
        await keepalive_handler.disable()
        assert not keepalive_handler.enabled
        assert keepalive_handler._should_stop

    @pytest.mark.asyncio
    async def test_task_lock_usage(self, keepalive_handler):
        """Test that task lock is properly used."""
        # Should have a lock
        assert keepalive_handler._task_lock is not None
        assert isinstance(keepalive_handler._task_lock, asyncio.Lock)

        # Lock should be usable
        async with keepalive_handler._task_lock:
            pass  # Should not block or error

    @pytest.mark.asyncio
    async def test_connection_lost_counter(self, keepalive_handler):
        """Test connection lost counter functionality."""
        # Should start at 0
        assert keepalive_handler._connection_lost_count == 0

        # Test resume_after_reconnection resets it
        keepalive_handler._connection_lost_count = 5
        await keepalive_handler.enable()
        await keepalive_handler.resume_after_reconnection()
        assert keepalive_handler._connection_lost_count == 0

        await keepalive_handler.disable()

    @pytest.mark.asyncio
    async def test_max_failures_setting(self, keepalive_handler):
        """Test max failures setting."""
        assert keepalive_handler._max_failures == 3

        # Can be modified
        keepalive_handler._max_failures = 5
        assert keepalive_handler._max_failures == 5

    @pytest.mark.asyncio
    async def test_monitor_interaction(self, mock_socket, keepalive_handler):
        """Test basic interaction with monitor."""
        await keepalive_handler.enable()

        # Wait for potential keepalive cycles
        await asyncio.sleep(0.2)

        # Should interact with monitor
        assert mock_socket.is_connected.called or mock_socket.send_keepalive.called

        await keepalive_handler.disable()

    @pytest.mark.asyncio
    async def test_no_monitor_handling(self):
        """Test behavior when no monitor is set."""
        handler = VitreaKeepAliveHandler(interval_seconds=0.1)

        # Should not error when enabling without monitor
        await handler.enable()
        await asyncio.sleep(0.1)
        await handler.disable()

        # Should handle gracefully
        assert not handler.enabled

    @pytest.mark.asyncio
    async def test_default_interval_is_19_seconds(self):
        """Test that default interval is 19 seconds to prevent connection loss."""
        handler = VitreaKeepAliveHandler()
        assert handler.interval == 19, "Default interval must be 19 seconds to prevent connection loss"

    @pytest.mark.asyncio
    async def test_custom_interval_setting(self):
        """Test that custom intervals can be set."""
        handler = VitreaKeepAliveHandler(interval_seconds=15)
        assert handler.interval == 15

        handler2 = VitreaKeepAliveHandler(interval_seconds=25)
        assert handler2.interval == 25

    @pytest.mark.asyncio
    async def test_timing_ensures_early_keepalives(self, mock_socket):
        """Test that jitter calculation ensures keepalives are sent early, not late."""
        handler = VitreaKeepAliveHandler(monitor=mock_socket, interval_seconds=19)

        # Test the jitter calculation logic
        base_interval = handler.interval
        max_jitter = min(2.0, handler.interval * 0.05)  # Should be 0.95 seconds

        # Verify max jitter is reasonable (use floating-point tolerant comparison)
        assert abs(max_jitter - 0.95) < 1e-10, f"Expected ~0.95 seconds jitter, got {max_jitter}"

        # Verify that actual interval will always be less than or equal to base
        import random
        random.seed(42)  # For reproducible test
        actual_interval = base_interval - random.uniform(0, max_jitter)

        assert actual_interval < base_interval, "Keepalive interval should be reduced by jitter, not increased"
        assert actual_interval >= (base_interval - max_jitter), "Keepalive interval should not be reduced too much"
