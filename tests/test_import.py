"""Basic import tests for the vitreaclient package."""

import pytest


class TestImports:
    """Test that all modules can be imported correctly."""

    def test_import_api_module(self):
        """Test importing the api module."""
        from src.vitreaclient import api
        assert api is not None

    def test_import_client_module(self):
        """Test importing the client module."""
        from src.vitreaclient import client
        assert client is not None

    def test_import_constants_module(self):
        """Test importing the constants module."""
        from src.vitreaclient import constants
        assert constants is not None

    def test_import_main_classes(self):
        """Test importing main classes from api module."""
        from src.vitreaclient.api import VitreaSocket, VitreaKeepAliveHandler, ConnectionUtils, EventEmitter

        assert VitreaSocket is not None
        assert VitreaKeepAliveHandler is not None
        assert ConnectionUtils is not None
        assert EventEmitter is not None

    def test_import_response_object(self):
        """Test importing VitreaResponseObject."""
        from src.vitreaclient.api import VitreaResponseObject
        assert VitreaResponseObject is not None

    def test_import_constants(self):
        """Test importing constants."""
        from src.vitreaclient.constants import VitreaResponse, DeviceStatus
        assert VitreaResponse is not None
        assert DeviceStatus is not None

    def test_package_import(self):
        """Test importing the main package."""
        import src.vitreaclient
        assert src.vitreaclient is not None

    def test_class_instantiation(self):
        """Test that classes can be instantiated."""
        from src.vitreaclient.api import VitreaSocket, VitreaKeepAliveHandler, ConnectionUtils, EventEmitter

        # Test VitreaSocket instantiation
        socket = VitreaSocket("localhost", 8080)
        assert socket.host == "localhost"
        assert socket.port == 8080

        # Test VitreaKeepAliveHandler instantiation
        keepalive = VitreaKeepAliveHandler(interval_seconds=30)
        assert keepalive.interval == 30

        # Test EventEmitter instantiation
        emitter = EventEmitter()
        assert emitter._events is not None

        # Test ConnectionUtils (static methods)
        delay = ConnectionUtils.calculate_backoff_delay(1)
        assert isinstance(delay, float)
        assert delay > 0

    def test_dataclass_import(self):
        """Test that dataclass can be imported and used."""
        from src.vitreaclient.api import VitreaResponseObject

        # Test instantiation
        response = VitreaResponseObject()
        assert response.type is None
        assert response.node is None

        # Test with values
        response = VitreaResponseObject(node="test", key="test_key")
        assert response.node == "test"
        assert response.key == "test_key"

    def test_typing_imports(self):
        """Test that typing imports work correctly."""
        # This test ensures that the Optional import works
        from src.vitreaclient.api import VitreaSocket

        socket = VitreaSocket("test", 123)
        # These should be None initially (Optional types)
        assert socket._reader is None
        assert socket._writer is None
        assert socket._keepalive is None

    def test_asyncio_compatibility(self):
        """Test that asyncio components are properly imported."""
        from src.vitreaclient.api import VitreaSocket
        import asyncio

        socket = VitreaSocket("test", 123)

        # Should have asyncio components
        assert isinstance(socket._mutex, asyncio.Lock)
        assert isinstance(socket._reconnect_mutex, asyncio.Lock)
        assert isinstance(socket._state_mutex, asyncio.Lock)
        assert isinstance(socket._connection_state, asyncio.Event)

    def test_threading_compatibility(self):
        """Test that threading components work correctly."""
        from src.vitreaclient.api import EventEmitter
        import threading

        emitter = EventEmitter()
        assert isinstance(emitter._lock, threading.Lock)

    def test_socket_module_import(self):
        """Test that socket module imports work."""
        from src.vitreaclient.api import VitreaSocket

        socket = VitreaSocket("test", 123)
        new_socket = socket._create_new_socket()

        # Should create a real socket object
        assert hasattr(new_socket, 'family')
        assert hasattr(new_socket, 'type')

        # Clean up
        new_socket.close()
