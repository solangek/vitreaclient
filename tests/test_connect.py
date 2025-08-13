import asyncio
import logging

import pytest

from src.vitreaclient.client import VitreaClient
from src.vitreaclient.constants import VitreaResponse, DeviceStatus

logging.basicConfig(level=logging.DEBUG)

@pytest.mark.asyncio
async def test_connect():

    client = VitreaClient(host='192.168.1.136', port=11502)

    status_events = []
    def on_status(event):
        # convert status char to DeviceStatus
        if event.status:
            status = DeviceStatus(event.status)
        else:
            status = None
        print(f"Event type: {event.type}, Node: {event.node}, Key: {event.key}, Status: {status}, data: {event.data}")

        status_events.append(event)

    client.on(VitreaResponse.STATUS, on_status)

    def on_ok(event):
        print("Event type OK")

    client.on(VitreaResponse.OK, on_ok)

    # Start the read task to listen for incoming messages
    await client.connect()
    await client.start_read_task()

    await client.key_off("018", "2")  # Example command to turn on a key
    await asyncio.sleep(2)
    await client.key_on("018", "2")  # Example command to turn off a key

    # get the status of all nodes
    #await client.status_request()

    await asyncio.sleep(10)  # Give time for response
    print(f"Status events received: {len(status_events)}")
    assert len(status_events) > 0, "No status events received"

    client.disconnect()


def main():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(test_connect())
    loop.close()

if __name__ == "__main__":
    main()
    # Run the main function to test the connection
    #asyncio.run(try_connect())  # Uncomment this line if you want to run it

