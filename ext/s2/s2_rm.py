import time
import math
import logging
import sys
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/aiovelib')
import asyncio

try:
    import dbus_fast
except ImportError:
    from dbus_next.service import method, signal
else:
    from dbus_fast.service import method, signal

from aiovelib.service import Item

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

IFACE="com.victronenergy.S2_ResourceManager"

class S2ResourceManagerItem(Item):

    cem_id = None
    keep_alive_interval = None
    keep_alive_leeway = None
    last_seen = None

    loop = None
    stop_event = None

    def __init__(self, path, loop=asyncio.get_event_loop()):
        super().__init__(path)
        self.name = IFACE
        self.loop = loop

    def _create_connection(self, cem_id: str, keep_alive_interval: int):
        self.cem_id = cem_id
        self.keep_alive_interval = keep_alive_interval
        self.keep_alive_leeway   = int(math.ceil(0.2 * keep_alive_interval))
        self.last_seen = time.time()

        logger.info(f"CEM '{ cem_id }' has connected")
        self._start_keep_alive_monitor()

    def _destroy_connection(self):
        logger.info(f"Closing connection to CEM '{ self.cem_id }'")
        self._stop_keep_alive_monitor()

        self.cem_id = None
        self.keep_alive_interval = None
        self.last_seen = None

    def _start_keep_alive_monitor(self):
        self.stop_event = asyncio.Event()
        self.loop.create_task(self._monitor_keep_alive())

    def _stop_keep_alive_monitor(self):
        self.stop_event.set()

    async def _monitor_keep_alive(self):
        diff = self.keep_alive_interval \
            + self.keep_alive_leeway

        while not self.stop_event.is_set():
            await asyncio.sleep(self.keep_alive_interval)
            if self.last_seen and time.time() - self.last_seen > diff:
                logger.info(f"CEM '{ self.cem_id }' KeepAlive missed")
                self._send_disconnect('KeepAlive missed')
                self._destroy_connection()


    @method('Connect')
    async def _on_connect(self, cem_id: 's', keep_alive_interval: 'i') -> 'b':
        if self.cem_id is None:
            self._create_connection(cem_id, keep_alive_interval)
            return True
        else:
            return False

    @method('Disconnect')
    async def _on_disconnect(self, cem_id: 's'):
        if cem_id != self.cem_id:
            self._send_disconnect('Not connected', cem_id)
            return

        self._destroy_connection()
    
    @method('Message')
    async def _on_message(self, cem_id: 's', message: 's'):
        if cem_id != self.cem_id:
            self._send_disconnect('Not connected', cem_id)
            return

        logger.debug(f"IN '{ message }'")

        self._send_message("Hello, CEM!")  # TODO: Only for testing

        # TODO: Forward this message
    
    @method('KeepAlive')
    async def _on_keep_alive(self, cem_id: 's') -> 'b':
        if cem_id != self.cem_id:
            self._send_disconnect('Not connected', cem_id)
            return False
        
        self.last_seen = time.time()
        return True

    @signal('Message')
    def _send_message(self, message: str) -> 'ss':
        logger.debug(f"OUT '{ message }'")

        return [self.cem_id, message]
    
    @signal('Disconnect')
    def _send_disconnect(self, reason: str, cem_id=None) -> 'ss':
        if cem_id is None: 
            cem_id = self.cem_id
        return [cem_id, reason]