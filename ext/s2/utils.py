import time
import logging
import asyncio
import importlib
import contextlib
from pathlib import Path
from enum import IntEnum
from dbus_fast.aio import MessageBus

from aiovelib.service import Service as _Service
from aiovelib.service import IntegerItem, RootItemInterface

from settings import LocalSettingsServiceMixin

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(levelname)-8s %(serial)-14s %(message)s'))
logger.addHandler(handler)
logger.propagate = False


# Formatters
format_kWh  = lambda v: f"{v:.2f} kWh"
format_w    = lambda v: f"{v:.0f} W"
format_a    = lambda v: f"{v:.1f} A"
format_v    = lambda v: f"{v:.2f} V"
format_f    = lambda v: f"{v:.1f} Hz"
format_s    = lambda v: f"{v:.0f} s"
format_p    = lambda v: f"{v:.1f} %"
format_t    = lambda v: f"{v:.1f} C"
format_km   = lambda v: f"{v:.1f} km"
format_bool = lambda v: "YES" if v > 0 else "NO"


class EnumItem(IntegerItem):

    def __init__(self, path, type, **kwargs):
        self.type = type
        super().__init__(path, **kwargs)

    def get_text(self):
        if self.value is None:
            return ''
        return self.type(self.value).name


class FirmwareVersionItem(IntegerItem):

    def get_text(self):
        v = self.value
        if v is None:
            return ''
        j = (
            (v >> 16) & 0xff,
            (v >> 8) & 0xff,
            v & 0xff
        )
        if j[2] == 0xFF:
            return 'v%x.%02x' % j[0:2]
        else:
            return 'v%x.%02x-beta-%02x' % j


class ProductIdItem(IntegerItem):

    def get_text(self):
        if self.value is None:
            return ''
        return '0x%04x' % self.value


class Service(_Service, LocalSettingsServiceMixin):
    """
    An abstract class representing a dbus service for a device.
    """

    servicetype = None
    allowed_roles = None
    default_role = None
    default_instance = None
    productname = None

    role = None
    is_registered = False

    def __init__(self, bus_type, client, serial: str, root_topic: str):
        self.bus_type = bus_type
        self.client = client
        self.serial = serial
        self.mqtt_topic_root = root_topic
        self.logger = self._configure_logger()

        self.name = self.service_name
        self.objects = {}
        self.changecollectors = []
        self.interface = RootItemInterface(self)

        self.logger.info(f"Discovered { self.productname }")

    async def _setup(self):
        """
        Sets up the dbus related things, including selecting the role,
        getting the device instance from localsettings and
        initializing all dbus paths.
        """
        self.bus = MessageBus(bus_type=self.bus_type)
        await self.bus.connect()
        self.bus.export('/', self.interface)

        await self.set_role_and_instance(
            self.ident, self.default_role, self.default_instance)

    @property
    def ident(self):
        if self.role is None:
            self.role = self.default_role
        return f"{ self.role }_{ self.serial }"

    @property
    def service_name(self):
        return f"{ self.servicetype }.{ self.ident }"

    async def initialize(self):
        """
        Starts initialization by requesting necessary data.
        """
        pass

    async def register(self):
        """
        Registers the service on dbus.
        """
        await self.bus.request_name(self.name)
        self.is_registered = True

    async def deregister(self):
        """
        Deregisters the service from dbus, e.g. if it has gone away.
        """
        await self.bus.release_name(self.name)
        self.is_registered = False

    async def start(self):
        """
        Sets up the service, registers it on dbus and starts the heartbeat check.
        """
        await self._setup()
        await self.initialize()

    def message_handler(self):
        pass

    def _configure_logger(self):
        return logging.LoggerAdapter(logger, {'serial': self.serial})

    @property
    def process_name(self):
        main = importlib.import_module("dbus-mqtt-devices")
        return Path(main.__file__).name


class AsyncResponder:
    """
    The AsyncResponder store enables to await an expected
    asynchronous message until it is received.
    """

    _flags = {}
    _values = {}

    async def wait_for_value(self, ref, timeout: int = 20):
        """ This method waits until set_value() is called 
            to set a value for the given reference.
        """
        flag = asyncio.Event()

        self._flags[ref] = flag
        self._values[ref] = None

        try:
            await asyncio.wait_for(flag.wait(), timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Timeout exceeded for { ref }")

        val = self._values.get(ref)
        with contextlib.suppress(KeyError):
            del self._flags[ref]
            del self._values[ref]

        return val

    def set_value(self, ref, val):
        """ This method signals all awaiting methods for 
            the given reference about the received value.
        """
        try:
            self._values[ref] = val
            self._flags[ref].set()
            return True
        except KeyError:
            return False


class HeartbeatServiceMixin:
    """
    An abstract class providing the interface
    for a Service with heartbeat.
    """
    logger = logging.getLogger(__name__)

    productname:    str  = 'unknown product'
    heartbeat:      int  = 0
    is_connected:   bool = False
    is_initialized: bool = False
    is_registered:  bool = False

    async def initialize(self):
        pass

    async def register(self):
        pass

    async def deregister(self):
        pass


class HeartbeatState(IntEnum):
    OK = 0
    OVERDUE = 1
    LOST = 2


class InitState(IntEnum):
    INITIALIZED = 0
    PENDING     = 1
    OVERDUE     = 2
    FAILED      = 3


class AsyncHeartbeatMonitor:
    """
    AsyncHeartbeatMonitor monitors a given heartbeat and handles
    initialization, registration and the connected state accordingly.
    """

    init: InitState = InitState.PENDING

    def __init__(self,
                 service: HeartbeatServiceMixin,
                 heartbeat_rate: int = 5, leeway: int = 30,
                 check_frequency: int = None):

        self.stop_event = asyncio.Event()
        self.service = service
        self.heartbeat_rate = heartbeat_rate
        self.leeway = leeway
        self.check_frequency = check_frequency or heartbeat_rate

    @property
    def logger(self):
        return self.service.logger

    @property
    def heartbeat(self):
        return self.service.heartbeat

    def start(self, loop = asyncio.get_event_loop()):
        loop.create_task(self._heartbeat_check_loop())

    def stop(self):
        self.stop_event.set()

    def _get_heartbeat_state(self):
        delta = time.time() - self.heartbeat
        if delta < self.heartbeat_rate + 1:
            return HeartbeatState.OK
        elif delta < self.leeway:
            return HeartbeatState.OVERDUE
        else:
            return HeartbeatState.LOST

    async def _heartbeat_check_loop(self):
        while not self.stop_event.is_set():

            state = self._get_heartbeat_state()
            if state == HeartbeatState.OK:
                await self._handle_heartbeat_ok()
            elif state == HeartbeatState.OVERDUE:
                await self._handle_heartbeat_overdue()
            else:
                await self._handle_heartbeat_lost()

            await asyncio.sleep(self.check_frequency)

    async def _handle_heartbeat_ok(self):
        if self.service.is_initialized:
            if not self.service.is_registered:
                self.logger.info("Initialization complete, registering" \
                                 if self.init != InitState.INITIALIZED \
                                 else "Heartbeat ok, registering")
                await self.service.register()
            elif not self.service.is_connected:
                self.logger.info("Heartbeat ok")
            self.init = InitState.INITIALIZED
            self.service.is_connected = True

        elif self.init in [ InitState.OVERDUE, InitState.FAILED ]:
            self.logger.info(
                f"Re-discovered { self.service.productname }, re-initializing")
            self.init = InitState.PENDING
            await self.service.initialize()

    async def _handle_heartbeat_overdue(self):
        if not self.service.is_initialized:
            self.init = InitState.OVERDUE
        self.logger.warning(
            ("Heartbeat" if self.service.is_initialized else "Initialization") \
            + f" overdue, last seen { int(time.time() - self.heartbeat) }s ago")
        self.service.is_connected = False

    async def _handle_heartbeat_lost(self):
        if self.service.is_registered:
            self.logger.error(f"Heartbeat overdue > { self.leeway }s, deregistering")
            self.service.is_connected = False
            await self.service.deregister()
        elif self.init in [ InitState.PENDING, InitState.OVERDUE ]:
            self.logger.error(f"Initialization overdue > { self.leeway }s, aborting")
            self.init = InitState.FAILED