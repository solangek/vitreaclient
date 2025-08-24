"""Test that event listeners persist after socket reconnection."""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch
from src.vitreaclient.client import VitreaClient
from src.vitreaclient.constants import VitreaResponse


class TestListenersAfterReconnection:
    """Test event listener persistence through reconnections."""

    @pytest.fixture
    def client(self):
        """Create a VitreaClient for testing."""
        return VitreaClient("localhost", 8080)

    @pytest.mark.asyncio
    async def test_listeners_persist_after_reconnection(self, client):
        """Test that event listeners continue working after automatic reconnection."""

        # Event collection
        status_events = []
        ok_events = []
        joined_events = []

        # Event handlers
        def on_status(event):
            status_events.append(event)

        def on_ok(event):
            ok_events.append(event)

        def on_joined(event):
            joined_events.append(event)

        # Register event listeners
        client.on(VitreaResponse.STATUS, on_status)
        client.on(VitreaResponse.OK, on_ok)
        client.on(VitreaResponse.JOINED, on_joined)

        # Verify listeners are registered
        assert len(client._events[VitreaResponse.STATUS]) == 1
        assert len(client._events[VitreaResponse.OK]) == 1
        assert len(client._events[VitreaResponse.JOINED]) == 1

        # Simulate receiving messages before reconnection
        await client._handle_data(b"S:PSW:OK")  # Keepalive OK
        await client._handle_data(b"S:N018:2:ON:100")  # Status message
        await client._handle_data(b"J:N019")  # Node joined

        # Verify events were processed
        assert len(status_events) == 1
        assert len(ok_events) == 1
        assert len(joined_events) == 1

        # Store initial counts
        initial_status_count = len(status_events)
        initial_ok_count = len(ok_events)
        initial_joined_count = len(joined_events)

        # Simulate reconnection process
        # This mimics what happens in VitreaSocket._reconnect()
        listeners_before_reconnection = len(client._events[VitreaResponse.STATUS])

        # Clear connection state (simulating disconnection)
        client._reader = None
        client._writer = None
        if hasattr(client, '_connection_state'):
            client._connection_state.clear()

        # Verify listeners are still registered after "reconnection"
        listeners_after_reconnection = len(client._events[VitreaResponse.STATUS])
        assert listeners_before_reconnection == listeners_after_reconnection

        # Simulate receiving messages after reconnection
        await client._handle_data(b"S:PSW:OK")  # Another keepalive
        await client._handle_data(b"S:N020:1:OFF:0")  # Another status
        await client._handle_data(b"J:N021")  # Another join

        # Verify that events continue to be processed after reconnection
        final_status_count = len(status_events)
        final_ok_count = len(ok_events)
        final_joined_count = len(joined_events)

        assert final_status_count > initial_status_count
        assert final_ok_count > initial_ok_count
        assert final_joined_count > initial_joined_count

        # Verify total event counts
        assert len(status_events) == 2
        assert len(ok_events) == 2
        assert len(joined_events) == 2

    @pytest.mark.asyncio
    async def test_multiple_listeners_same_event(self, client):
        """Test that multiple listeners for the same event persist after reconnection."""

        # Multiple event collectors
        events_collector_1 = []
        events_collector_2 = []
        events_collector_3 = []

        # Multiple handlers for the same event
        def handler_1(event):
            events_collector_1.append(event)

        def handler_2(event):
            events_collector_2.append(event)

        def handler_3(event):
            events_collector_3.append(event)

        # Register multiple listeners for STATUS event
        client.on(VitreaResponse.STATUS, handler_1)
        client.on(VitreaResponse.STATUS, handler_2)
        client.on(VitreaResponse.STATUS, handler_3)

        # Verify all listeners are registered
        assert len(client._events[VitreaResponse.STATUS]) == 3

        # Send a status message
        await client._handle_data(b"S:N018:2:ON:100")

        # All collectors should have received the event
        assert len(events_collector_1) == 1
        assert len(events_collector_2) == 1
        assert len(events_collector_3) == 1

        # Simulate reconnection
        client._reader = None
        client._writer = None

        # Verify all listeners still registered
        assert len(client._events[VitreaResponse.STATUS]) == 3

        # Send another status message after "reconnection"
        await client._handle_data(b"S:N020:1:OFF:0")

        # All collectors should have received both events
        assert len(events_collector_1) == 2
        assert len(events_collector_2) == 2
        assert len(events_collector_3) == 2

    @pytest.mark.asyncio
    async def test_listener_removal_persists_after_reconnection(self, client):
        """Test that listener removal also persists after reconnection."""

        events = []

        def handler(event):
            events.append(event)

        # Register and then remove listener
        client.on(VitreaResponse.STATUS, handler)
        assert len(client._events[VitreaResponse.STATUS]) == 1

        client.off(VitreaResponse.STATUS, handler)
        assert len(client._events[VitreaResponse.STATUS]) == 0

        # Simulate reconnection
        client._reader = None
        client._writer = None

        # Listener should still be removed after reconnection
        assert len(client._events[VitreaResponse.STATUS]) == 0

        # Send status message - should not be handled
        await client._handle_data(b"S:N018:2:ON:100")
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_event_emitter_inheritance_persists(self, client):
        """Test that EventEmitter functionality persists through reconnection simulation."""

        # Verify client inherits from EventEmitter
        from src.vitreaclient.api import EventEmitter
        assert isinstance(client, EventEmitter)

        # Verify EventEmitter methods work
        events = []

        def handler(event):
            events.append(event)

        # Test on/off/emit functionality
        client.on(VitreaResponse.OK, handler)

        # Manually emit an event (bypassing message parsing)
        from src.vitreaclient.api import VitreaResponseObject
        test_event = VitreaResponseObject(type=VitreaResponse.OK)
        client.emit(VitreaResponse.OK, test_event)

        assert len(events) == 1
        assert events[0] == test_event

        # Simulate reconnection
        client._reader = None
        client._writer = None

        # Event emission should still work
        client.emit(VitreaResponse.OK, test_event)
        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_concurrent_listener_operations(self, client):
        """Test that concurrent listener operations work correctly around reconnection."""

        events = []

        def handler(event):
            events.append(event)

        # Register listener
        client.on(VitreaResponse.STATUS, handler)

        # Simulate concurrent operations during "reconnection"
        # In real scenarios, these might happen while reconnection is in progress

        # Send message
        await client._handle_data(b"S:N018:2:ON:100")
        assert len(events) == 1

        # Simulate partial reconnection state
        client._reader = None
        # Keep writer to simulate intermediate state

        # Add another listener during "reconnection"
        events_2 = []
        def handler_2(event):
            events_2.append(event)

        client.on(VitreaResponse.STATUS, handler_2)

        # Complete "reconnection"
        client._writer = None

        # Both listeners should work after reconnection
        await client._handle_data(b"S:N020:1:OFF:0")

        assert len(events) == 2
        assert len(events_2) == 1  # Only got the second message
