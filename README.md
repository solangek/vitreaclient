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
from vitreaclient.client import VitreaClient, VitreaResponse, DeviceStatus
import asyncio


async def vitrea_test():
    client = VitreaClient(host='192.168.1.100', port=11102)

    status_events = []
    def on_status(event):
        print(f"Event type: {event.type}, Node: {event.node}, Key: {event.key}, Status: {event.status}, data: {event.data}")
        status_events.append(event)

    client.on(VitreaResponse.STATUS, on_status)

    def on_ok(event):
        print(f"Event type {event.type}")

    client.on(VitreaResponse.OK, on_ok)

    # connect and start the read task to listen for incoming messages
    await client.connect()
    await client.start_read_task()

    await client.key_off("018", "2")  # Example command to turn on a key
    await asyncio.sleep(2)
    await client.key_on("018", "2")  # Example command to turn off a key

    # get the status of all nodes
    await client.status_request()

    await asyncio.sleep(60)  # Give time for response
    print(f"Status events received: {len(status_events)}")
    # unsubscribe from events
    client.off(VitreaResponse.STATUS, on_status)
    client.off(VitreaResponse.OK, on_ok)
    
    client.disconnect()



```

## License
See [LICENSE](LICENSE).

