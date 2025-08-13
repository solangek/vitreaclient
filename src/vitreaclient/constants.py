"""Vitrea protocol constants for use in the standalone package."""

from enum import Enum
from typing import Dict

DOMAIN = "vitreaclient"

MONITOR = 'VITREA_MONITOR'
SWITCHES = 'VITREA_SWITCHES'

class DeviceStatus(Enum):
    BOILER_ON = 'o' # boiler on with timer parameter
    BOILER_OFF = 'f' # boiler off
    SWITCH_ON = 'O' # light On
    SWITCH_OFF = 'F' # light Off
    BLIND = 'B' # blind with position param 0-100 - sent twice by vitrea (up and down)
    DIMMER = 'D'
    SATELLITE_SHORT = 'S' # scenario / satellite detected, short message
    SATELLITE_LONG = 'L' # satellite detected, long message
    SATELLITE_RELEASE = 'R' # scenario / satellite released
    JOINED = 'J' # a node joined the network
    LEFT = 'L' # a node left the network
    ERROR = 'E' # error occurred
    OK = 'OK' # command executed successfully


class DeviceAction(Enum):
    ON = 'O'  # light, boiler On for x minutes
    OFF = 'F' # light, boiler Off
    DIMMER = 'D' # <dimmer intensity>:<minutes>
    TOGGLE = 'T' # toggle light, dimmer to previous state, boiler timer to previous state
    BLIND = 'B' # blind position: 0-100, 255 for stop

EOL: str = '\r\n'

class VitreaCommand:
    KEEP_ALIVE = f"P:VITREA{EOL}"
    STATUS = f"H:NALL:G{EOL}"
    STATUS_NODE = f"H:N{{node}}:G{EOL}"
    STATUS_KEY = f"H:N{{node}}:{{key}}:G{EOL}"
    KEY_ON_WITH_TIMER = f"H:N{{node}}:{{key}}:O:{{timer:03d}}{EOL}"
    KEY_ON = f"H:N{{node}}:{{key}}:O:{EOL}"
    KEY_OFF = f"H:N{{node}}:{{key}}:F:000{EOL}"
    BLIND_OPEN = f"H:N{{node}}:{{key}}:B:100{EOL}"
    BLIND_CLOSE = f"H:N{{node}}:{{key}}:B:000{EOL}"
    BLIND_PERCENT = f"H:N{{node}}:{{key}}:B:{{percent:03d}}{EOL}"
    BLIND_STOP = f"H:N{{node}}:{{key}}:B:255{EOL}"
    SCENARIO_ON = f"H:R{{scenario}}{EOL}" # scneario: 5 bytes

class VitreaResponse(Enum):
    SCENARIO_OK= f"S:R{{scenario}}:OK" # scenario: 5 bytes
    SCENARIO_ERROR = f"S:R{{scenario}}:ERROR" # scenario: 5 bytes, error_code: 3 bytes
    STATUS = f"S:N{{node}}:{{key}}:{{status}}:{{data}}" # node: 2 bytes, key: 2 bytes, status: 1 byte, data: variable length
    ERROR = f"E:N{{node}}:{{key}}:{{error}}"
    OK = "S:PSW:OK"
    JOINED = f"J:N{{node}}" # node joined the network
    LEFT = f"L:N{{node}}" # node left the network
    OK_VITREA = "OK"

class ErrorCode(Enum):
    WRONG_COMMAND = "001"
    WRONG_INPUT = "002"
    WRONG_NODE_NUMBER = "003"
    WRONG_KEY_NUMBER = "004"
    NODE_NOT_FOUND = "005"
    WRONG_SCENARIO = "006"

ERROR_MESSAGES: Dict[str, str] = {
    ErrorCode.WRONG_COMMAND.value: "Wrong Command",
    ErrorCode.WRONG_INPUT.value: "Wrong Input",
    ErrorCode.WRONG_NODE_NUMBER.value: "Wrong Node Number",
    ErrorCode.WRONG_KEY_NUMBER.value: "Wrong Key Number",
    ErrorCode.NODE_NOT_FOUND.value: "Node not found",
    ErrorCode.WRONG_SCENARIO.value: "Wrong Scenario"
}

# Index constants for parsing
INDEX_NODE = 0
INDEX_KEY = 1
INDEX_STATUS = 2
INDEX_DATA = 3


