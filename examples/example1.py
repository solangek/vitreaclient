import asyncio

from src import VitreaClient
from src.vitreaclient.constants import VitreaResponse, DeviceStatus

async def example1():

    client = VitreaClient(host='192.168.1.136', port=11502)

    status_events = []
    def on_status(event):
        print(f"===> Event type: {event.type}, Node: {event.node}, Key: {event.key}, Status: {event.status}, Extra: {event.extra}")
        status_events.append(event)

    print(f"Registering listener for event: {VitreaResponse.STATUS}")
    client.on(VitreaResponse.STATUS, on_status)

    def on_ok(event):
        print(f"===> Event type {event.type}")

    print(f"Registering listener for event: {VitreaResponse.OK}")
    client.on(VitreaResponse.OK, on_ok)
    client.on(VitreaResponse.OK_VITREA, on_ok)

    # Start the read task to listen for incoming messages
    await client.connect()
    await client.start_read_task()

    #await client.key_off("018", "2")  # Example command to turn on a key
    #await asyncio.sleep(2)
    #await client.key_on("018", "2")  # Example command to turn off a key

    # get the status of all nodes
    await client.status_request()

    await asyncio.sleep(180)  # Give time for response
    print(f"Status events received: {len(status_events)}")
    assert len(status_events) > 0, "No status events received"

    client.disconnect()

def main():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(example1())
    loop.close()

if __name__ == "__main__":
    main()
    # Run the main function to test the connection
    #asyncio.run(try_connect())  # Uncomment this line if you want to run it

