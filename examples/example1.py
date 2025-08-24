import asyncio
import sys
import os
import logging

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from vitreaclient.client import VitreaClient
from vitreaclient.constants import VitreaResponse, DeviceStatus

# Configure logging to see what's happening
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

async def example1():
    print("Starting Vitrea client example...")

    client = VitreaClient(host='192.168.1.136', port=11502)

    status_events = []
    def on_status(event):
        print(f"===> Status Event - Node: {event.node}, Key: {event.key}, Status: {event.status}, Data: {event.data}")
        status_events.append(event)

    def on_ok(event):
        print(f"===> OK Event - Type: {event.type}")

    def on_joined(event):
        print(f"===> Node Joined - Node: {event.node}")

    def on_left(event):
        print(f"===> Node Left - Node: {event.node}")

    # Register event listeners
    print("Registering event listeners...")
    client.on(VitreaResponse.STATUS, on_status)
    client.on(VitreaResponse.OK, on_ok)
    client.on(VitreaResponse.OK_VITREA, on_ok)
    client.on(VitreaResponse.JOINED, on_joined)
    client.on(VitreaResponse.LEFT, on_left)

    try:
        # Connect to the Vitrea box
        print("Connecting to Vitrea box...")
        await client.connect()
        print("Connected successfully!")

        # Give a moment for any initial messages
        await asyncio.sleep(1)

        # Send some commands and wait for responses
        print("Sending key off command...")
        await client.key_off("018", "2")
        await asyncio.sleep(1)

        print("Sending key on command...")
        await client.key_on("018", "2")
        await asyncio.sleep(1)

        # Request status of all nodes
        print("Requesting status of all nodes...")
        await client.status_request()
        await asyncio.sleep(5)  # Wait for status responses - FIXED from 500 seconds

        print(f"Total status events received: {len(status_events)}")

        # If we received status events, show some details
        if status_events:
            print("Sample status events:")
            for i, event in enumerate(status_events[:5]):  # Show first 5 events
                print(f"  {i+1}. Node {event.node}, Key {event.key}: {event.status}")
        else:
            print("No status events received - this might indicate a connection or device issue")

    except Exception as e:
        print(f"Error during example execution: {e}")
        import traceback
        traceback.print_exc()

    finally:
        print("Closing client...")
        await client.close()
        print("Client closed.")

if __name__ == "__main__":
    print("Running Vitrea client example...")
    try:
        asyncio.run(example1())
    except KeyboardInterrupt:
        print("Example interrupted by user")
    except Exception as e:
        print(f"Example failed with error: {e}")
        import traceback
        traceback.print_exc()
