import asyncio
import logging

import pytest

from vitrea.client import VitreaClient

logging.basicConfig(level=logging.DEBUG)

@pytest.mark.asyncio
async def try_connect():
    from vitrea.constants import VitreaEvents
    client = VitreaClient(host='192.168.1.136', port=11502)

    await client.connect()

    status_events = []
    def on_status(event):
        print(f"Event type: {event.type}, Node: {event.node}, Key: {event.key}, Status: {event.status}, Extra: {event.extra}")
        status_events.append(event)

    print(f"Registering listener for event: {VitreaEvents.RESPONSE_STATUS}")
    client.on(VitreaEvents.RESPONSE_STATUS.value, on_status)

    # Start the read task to listen for incoming messages
    await client.start_read_task()

    await client.key_off("018", "2")  # Example command to turn on a key
    await asyncio.sleep(2)
    await client.key_on("018", "2")  # Example command to turn off a key

    await asyncio.sleep(10)  # Give time for response

    # get the status of all nodes
    #await client.status_request()
    #await asyncio.sleep(30)  # Give time for response
    print(f"Status events received: {len(status_events)}")
    assert len(status_events) > 0, "No status events received"

    client.disconnect()

def main():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(try_connect())
    loop.close()

if __name__ == "__main__":
    main()
    # Run the main function to test the connection
    #asyncio.run(try_connect())  # Uncomment this line if you want to run it

