# Vitrea

Python package encapsulating the simple Vitrea box communication protocol.
You need to identify the Vitrea box IP address and port to use this package.
Note that the port is not necessarily the port used by the Vitrea app (hint: try +/- 1).

## Installation

```bash
pip install vitreaclient
```

## Usage

```python
from vitreaclient.client import VitreaClient
from vitreaclient.constants import VitreaEvents
import asyncio


async def vitrea_test():
    client = VitreaClient(host='1.2.3.4', port=12345)

    await client.connect()

    status_events = []

    def on_status(event):
        print(
            f"Event type: {event.type}, Node: {event.node}, Key: {event.key}, Status: {event.status}, Extra: {event.extra}")
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
    await client.status_request()
    await asyncio.sleep(30)  # Give time for response
    print(f"Status events received: {len(status_events)}")

    client.disconnect()

```

## License
See [LICENSE](LICENSE).

